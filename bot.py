"""
Anti-Raid Discord Bot
=====================

Defensive moderation toolkit — protects YOUR server from raids.
No command in this bot can mass-ban or mass-delete-channels, by design:
those actions are indistinguishable from an actual raid and are a liability
even in the hands of a trusted owner (compromised token, mis-click, insider
threat). Instead, this bot neutralizes raids by locking the server down
and letting mods review/act on specific accounts.

Commands:
  /lockdown                 - Instantly stop everyone from posting/joining voice
  /unlock                   - Reverse a lockdown
  /raidmode <on|off>        - Toggle auto-detection of join spikes
  /kick-new-accounts <hrs>  - Preview + confirm-kick accounts newer than N hours
  /ban-batch <members>      - Ban up to 10 specific named members at once (preview + confirm)
  /announce <channel> <msg> - Post an announcement to ONE channel you choose
  /announce-all <msg>       - Post an announcement to EVERY text channel (preview + confirm)
  /dm-role <role> <msg>     - DM every member with a specific role (preview + confirm, rate-limited)
  /add-server               - Post a button that lets members OAuth-join your support server (opt-in)
  /verify-setup             - Post a one-click verification gate for new members

Note on DMs: this bot deliberately has no "/dm-all" or blast-DM-everyone
command. Discord's Developer Terms of Service prohibit sending unsolicited
DMs/spam to users, and bots caught doing it get their tokens revoked. /dm-role
scopes sending to members who already hold a role you chose (e.g. an
opt-in "Announcements" role), which keeps it consensual and ToS-compliant.

Requirements:
  pip install -U discord.py

Setup:
  1. Create an application + bot at https://discord.com/developers/applications
  2. Enable "Server Members Intent" and "Message Content Intent" under Bot settings
  3. Set environment variable DISCORD_BOT_TOKEN (see .env.example)
  4. Invite the bot with the "applications.commands" and "bot" scopes, with
     permissions: Manage Channels, Manage Roles, Kick Members, Send Messages,
     Manage Messages, Moderate Members
  5. Run: python bot.py
"""

import os
import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Join-spike detection thresholds (tune to your server's normal traffic)
RAID_JOIN_COUNT = 5        # this many joins...
RAID_JOIN_WINDOW = 10      # ...within this many seconds triggers auto-lockdown

VERIFIED_ROLE_NAME = "Verified"
MUTED_EVERYONE_OVERWRITE_KEY = "raidlock"  # marker so we know we set it

# --- /add-server (OAuth join) config ---
# Create an application at https://discord.com/developers/applications,
# under OAuth2 add a redirect URI (can be a simple page you control, or
# Discord's own https://discord.com/oauth2/authorized as a no-op landing page),
# and set these so /add-server can build a working authorize link.
OAUTH_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
OAUTH_REDIRECT_URI = os.getenv("DISCORD_OAUTH_REDIRECT_URI", "https://discord.com/oauth2/authorized")
SUPPORT_SERVER_GUILD_ID = os.getenv("SUPPORT_SERVER_GUILD_ID", "")  # the server members will be added to

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


# Per-guild state (in-memory; for persistence across restarts, swap this for
# a small JSON/SQLite store)
guild_state: dict[int, dict] = {}


def state_for(guild_id: int) -> dict:
    return guild_state.setdefault(
        guild_id,
        {
            "raidmode": True,
            "recent_joins": [],  # list[datetime]
            "locked": False,
            "saved_overwrites": {},  # channel_id -> prior @everyone overwrite
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def do_lockdown(guild: discord.Guild) -> int:
    """Lock every text/voice channel's @everyone permissions. Reversible.
    Returns number of channels locked."""
    st = state_for(guild.id)
    if st["locked"]:
        return 0

    everyone = guild.default_role
    count = 0
    for channel in guild.channels:
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
            continue
        try:
            current = channel.overwrites_for(everyone)
            # Save prior state so /unlock restores exactly what was there before
            st["saved_overwrites"][channel.id] = {
                "send_messages": current.send_messages,
                "connect": current.connect,
                "add_reactions": current.add_reactions,
                "create_public_threads": current.create_public_threads,
            }
            new_overwrite = discord.PermissionOverwrite(
                send_messages=False,
                connect=False,
                add_reactions=False,
                create_public_threads=False,
            )
            await channel.set_permissions(
                everyone, overwrite=new_overwrite, reason="Anti-raid lockdown"
            )
            count += 1
        except discord.Forbidden:
            continue
    st["locked"] = True
    return count


async def do_unlock(guild: discord.Guild) -> int:
    """Restore permissions saved before lockdown."""
    st = state_for(guild.id)
    if not st["locked"]:
        return 0

    everyone = guild.default_role
    count = 0
    for channel in guild.channels:
        saved = st["saved_overwrites"].get(channel.id)
        if saved is None:
            continue
        try:
            current = channel.overwrites_for(everyone)
            current.send_messages = saved["send_messages"]
            current.connect = saved["connect"]
            current.add_reactions = saved["add_reactions"]
            current.create_public_threads = saved["create_public_threads"]
            await channel.set_permissions(
                everyone, overwrite=current, reason="Anti-raid lockdown lifted"
            )
            count += 1
        except discord.Forbidden:
            continue
    st["locked"] = False
    st["saved_overwrites"] = {}
    return count


def is_mod():
    """Restrict a command to members who can kick/ban or manage the server."""
    async def predicate(interaction: discord.Interaction) -> bool:
        perms = interaction.user.guild_permissions
        return perms.kick_members or perms.ban_members or perms.administrator
    return app_commands.check(predicate)


# ---------------------------------------------------------------------------
# Events: auto-raid-detection
# ---------------------------------------------------------------------------

TEST_GUILD_ID = os.getenv("TEST_GUILD_ID")  # optional — your server's ID, for instant sync while testing


class AntiRaidBot(commands.Bot):
    async def setup_hook(self):
        # setup_hook runs once per client startup, unlike on_ready which can
        # fire again after a gateway reconnect.
        if TEST_GUILD_ID:
            try:
                guild_id = int(TEST_GUILD_ID)
            except ValueError as exc:
                raise RuntimeError("TEST_GUILD_ID must be a numeric Discord server ID.") from exc

            guild_obj = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            print(f"Synced {len(synced)} slash commands to test guild {guild_id}.")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global slash commands. Global propagation may take time.")

bot = AntiRaidBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Discord gateway connected as {bot.user} (ID: {bot.user.id}).")


@bot.event
async def on_member_join(member: discord.Member):
    st = state_for(member.guild.id)
    if not st["raidmode"]:
        return

    now = datetime.now(timezone.utc)
    st["recent_joins"].append(now)
    # Drop joins outside the detection window
    cutoff = now - timedelta(seconds=RAID_JOIN_WINDOW)
    st["recent_joins"] = [t for t in st["recent_joins"] if t > cutoff]

    if len(st["recent_joins"]) >= RAID_JOIN_COUNT and not st["locked"]:
        locked_count = await do_lockdown(member.guild)
        alert_channel = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel)
            and c.permissions_for(member.guild.me).send_messages,
            member.guild.channels,
        )
        if alert_channel:
            await alert_channel.send(
                f"🚨 **Raid detected** — {len(st['recent_joins'])} joins in "
                f"{RAID_JOIN_WINDOW}s. Auto-locked {locked_count} channels. "
                f"Mods: review new accounts with `/kick-new-accounts` then `/unlock` when safe."
            )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@bot.tree.command(description="Instantly lock all channels (stop sends/joins) to stop an active raid")
@is_mod()
async def lockdown(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    count = await do_lockdown(interaction.guild)
    if count == 0:
        await interaction.followup.send("Server is already locked down.")
    else:
        await interaction.followup.send(f"🔒 Locked {count} channels. Run `/unlock` when it's safe.")


@bot.tree.command(description="Reverse a lockdown and restore normal permissions")
@is_mod()
async def unlock(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    count = await do_unlock(interaction.guild)
    if count == 0:
        await interaction.followup.send("Server isn't currently locked down.")
    else:
        await interaction.followup.send(f"🔓 Restored {count} channels to normal.")


@bot.tree.command(description="Turn automatic raid detection on or off")
@app_commands.describe(mode="on or off")
@is_mod()
async def raidmode(interaction: discord.Interaction, mode: str):
    st = state_for(interaction.guild.id)
    mode = mode.lower().strip()
    if mode not in ("on", "off"):
        await interaction.response.send_message("Use `/raidmode on` or `/raidmode off`.", ephemeral=True)
        return
    st["raidmode"] = mode == "on"
    await interaction.response.send_message(f"Auto raid-detection is now **{mode}**.")


@bot.tree.command(description="Preview accounts newer than N hours, then confirm to kick them")
@app_commands.describe(hours="Kick accounts created more recently than this many hours ago")
@is_mod()
async def kick_new_accounts(interaction: discord.Interaction, hours: int):
    await interaction.response.defer(thinking=True)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    candidates = [
        m for m in interaction.guild.members
        if m.created_at > cutoff and not m.bot and m != interaction.guild.owner
    ]

    if not candidates:
        await interaction.followup.send("No members match that account-age filter.")
        return

    preview = "\n".join(f"- {m.mention} (account made {m.created_at:%Y-%m-%d %H:%M UTC})" for m in candidates[:25])
    more = f"\n…and {len(candidates) - 25} more." if len(candidates) > 25 else ""

    view = ConfirmKickView(candidates)
    await interaction.followup.send(
        f"**{len(candidates)} account(s)** created in the last {hours}h:\n{preview}{more}\n\n"
        f"Confirm to kick these specific accounts. This does **not** touch anyone else.",
        view=view,
    )


class ConfirmKickView(discord.ui.View):
    def __init__(self, candidates: list[discord.Member]):
        super().__init__(timeout=60)
        self.candidates = candidates

    @discord.ui.button(label="Confirm kick", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        kicked = 0
        for m in self.candidates:
            try:
                await m.kick(reason="Anti-raid: new-account filter, mod-confirmed")
                kicked += 1
            except discord.Forbidden:
                continue
        await interaction.response.edit_message(content=f"✅ Kicked {kicked} account(s).", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled. No one was kicked.", view=None)


@bot.tree.command(description="Ban up to 10 specific members at once (preview shown, confirm required)")
@app_commands.describe(
    member1="Member to ban", member2="Optional", member3="Optional", member4="Optional",
    member5="Optional", member6="Optional", member7="Optional", member8="Optional",
    member9="Optional", member10="Optional",
    reason="Reason shown in the audit log",
)
@is_mod()
async def ban_batch(
    interaction: discord.Interaction,
    member1: discord.Member,
    member2: discord.Member = None,
    member3: discord.Member = None,
    member4: discord.Member = None,
    member5: discord.Member = None,
    member6: discord.Member = None,
    member7: discord.Member = None,
    member8: discord.Member = None,
    member9: discord.Member = None,
    member10: discord.Member = None,
    reason: str = "Raid response (batch ban)",
):
    targets = [m for m in (member1, member2, member3, member4, member5,
                            member6, member7, member8, member9, member10) if m is not None]

    # Never allow the owner, the bot itself, or other mods/admins to be swept up
    safe_targets = []
    blocked = []
    for m in targets:
        if m == interaction.guild.owner or m == bot.user:
            blocked.append(m)
        elif m.guild_permissions.kick_members or m.guild_permissions.ban_members or m.guild_permissions.administrator:
            blocked.append(m)
        else:
            safe_targets.append(m)

    if not safe_targets:
        await interaction.response.send_message(
            "No valid targets — the server owner, the bot, and other mods/admins can't be banned this way.",
            ephemeral=True,
        )
        return

    preview = "\n".join(f"- {m.mention} ({m})" for m in safe_targets)
    blocked_note = ""
    if blocked:
        blocked_note = "\n\n⚠️ Skipped (owner/mod/bot, can't be targeted): " + ", ".join(str(m) for m in blocked)

    view = ConfirmBanView(safe_targets, reason)
    await interaction.response.send_message(
        f"**Ready to ban {len(safe_targets)} member(s)** for: *{reason}*\n{preview}{blocked_note}\n\n"
        f"Confirm to proceed. This bans only the members listed above — no one else.",
        view=view,
        ephemeral=True,
    )


class ConfirmBanView(discord.ui.View):
    def __init__(self, targets: list[discord.Member], reason: str):
        super().__init__(timeout=60)
        self.targets = targets
        self.reason = reason

    @discord.ui.button(label="Confirm ban", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        banned, failed = [], []
        for m in self.targets:
            try:
                await m.ban(reason=self.reason, delete_message_seconds=3600)
                banned.append(str(m))
            except discord.Forbidden:
                failed.append(str(m))
        msg = f"✅ Banned {len(banned)}: {', '.join(banned) if banned else 'none'}"
        if failed:
            msg += f"\n⚠️ Couldn't ban (missing permissions): {', '.join(failed)}"
        await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled. No one was banned.", view=None)


@bot.tree.command(description="Post an announcement to one channel you choose")
@app_commands.describe(channel="The channel to post in", message="The announcement text")
@is_mod()
async def announce(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    embed = discord.Embed(description=message, color=discord.Color.blurple())
    embed.set_footer(text=f"Announcement by {interaction.user.display_name}")
    await channel.send(embed=embed)
    await interaction.response.send_message(f"Posted to {channel.mention}.", ephemeral=True)


@bot.tree.command(description="Post an announcement to EVERY text channel in the server")
@app_commands.describe(message="The announcement text")
@is_mod()
async def announce_all(interaction: discord.Interaction, message: str):
    text_channels = [
        c for c in interaction.guild.channels
        if isinstance(c, discord.TextChannel)
        and c.permissions_for(interaction.guild.me).send_messages
    ]

    if not text_channels:
        await interaction.response.send_message("No text channels I can post in.", ephemeral=True)
        return

    view = ConfirmAnnounceAllView(text_channels, message, interaction.user.display_name)
    preview = "\n".join(f"- {c.mention}" for c in text_channels[:25])
    more = f"\n…and {len(text_channels) - 25} more." if len(text_channels) > 25 else ""
    await interaction.response.send_message(
        f"This will post to **{len(text_channels)} channel(s)**:\n{preview}{more}\n\nConfirm to send.",
        view=view,
        ephemeral=True,
    )


class ConfirmAnnounceAllView(discord.ui.View):
    def __init__(self, channels: list[discord.TextChannel], message: str, author_name: str):
        super().__init__(timeout=60)
        self.channels = channels
        self.message = message
        self.author_name = author_name

    @discord.ui.button(label="Confirm send", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(description=self.message, color=discord.Color.blurple())
        embed.set_footer(text=f"Server-wide announcement by {self.author_name}")
        sent, failed = 0, 0
        for channel in self.channels:
            try:
                await channel.send(embed=embed)
                sent += 1
                await asyncio.sleep(0.5)  # gentle pacing to avoid rate limits
            except discord.Forbidden:
                failed += 1
        msg = f"✅ Posted to {sent} channel(s)."
        if failed:
            msg += f" ⚠️ Skipped {failed} (missing permission)."
        await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled. Nothing was posted.", view=None)


@bot.tree.command(description="DM every member who holds a specific role (preview + confirm)")
@app_commands.describe(role="Only members with this role will be DMed", message="The message to send")
@is_mod()
async def dm_role(interaction: discord.Interaction, role: discord.Role, message: str):
    targets = [m for m in role.members if not m.bot]

    if not targets:
        await interaction.response.send_message(f"No members (that aren't bots) hold {role.mention}.", ephemeral=True)
        return

    view = ConfirmDMRoleView(targets, message, role.name, interaction.user.display_name)
    preview = ", ".join(m.display_name for m in targets[:15])
    more = f", and {len(targets) - 15} more" if len(targets) > 15 else ""
    await interaction.response.send_message(
        f"This will DM **{len(targets)} member(s)** with the role **{role.name}**: {preview}{more}\n\n"
        f"Message preview:\n> {message}\n\nConfirm to send.",
        view=view,
        ephemeral=True,
    )


class ConfirmDMRoleView(discord.ui.View):
    def __init__(self, targets: list[discord.Member], message: str, role_name: str, author_name: str):
        super().__init__(timeout=60)
        self.targets = targets
        self.message = message
        self.role_name = role_name
        self.author_name = author_name

    @discord.ui.button(label="Confirm send", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(description=self.message, color=discord.Color.blurple())
        embed.set_footer(text=f"Sent to {self.role_name} members by {self.author_name}")
        sent, failed = 0, 0
        for member in self.targets:
            try:
                await member.send(embed=embed)
                sent += 1
                await asyncio.sleep(1.0)  # pacing: DMs rate-limit faster than channel messages
            except discord.Forbidden:
                failed += 1  # DMs closed / blocked the bot
        msg = f"✅ DMed {sent} member(s)."
        if failed:
            msg += f" ⚠️ Skipped {failed} (DMs closed or blocked the bot)."
        await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled. No DMs were sent.", view=None)


@bot.tree.command(description="Post a button letting members opt in to join your support server via OAuth")
@is_mod()
async def add_server(interaction: discord.Interaction):
    if not OAUTH_CLIENT_ID or not SUPPORT_SERVER_GUILD_ID:
        await interaction.response.send_message(
            "Not configured yet — set DISCORD_CLIENT_ID and SUPPORT_SERVER_GUILD_ID "
            "as environment variables first (see README).",
            ephemeral=True,
        )
        return

    authorize_url = (
        "https://discord.com/oauth2/authorize"
        f"?client_id={OAUTH_CLIENT_ID}"
        f"&redirect_uri={OAUTH_REDIRECT_URI}"
        "&response_type=code"
        "&scope=identify%20guilds.join"
    )

    embed = discord.Embed(
        title="Join our support server",
        description="Click below to securely authorize joining via Discord — you'll see exactly "
                    "what's being requested before you approve anything.",
        color=discord.Color.green(),
    )
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Join support server", style=discord.ButtonStyle.link, url=authorize_url))
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("Posted the join button.", ephemeral=True)


@bot.tree.command(description="Post a one-click verification gate for new members")
@is_mod()
async def verify_setup(interaction: discord.Interaction):
    role = discord.utils.get(interaction.guild.roles, name=VERIFIED_ROLE_NAME)
    if role is None:
        role = await interaction.guild.create_role(name=VERIFIED_ROLE_NAME, reason="Anti-raid verification gate")

    view = VerifyView(role)
    embed = discord.Embed(
        title="Verify to join the conversation",
        description="Click the button below to unlock the rest of the server.",
        color=discord.Color.green(),
    )
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("Verification gate posted.", ephemeral=True)


class VerifyView(discord.ui.View):
    def __init__(self, role: discord.Role):
        super().__init__(timeout=None)
        self.role = role

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="antiraid_verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.user.add_roles(self.role, reason="Self-verified")
            await interaction.response.send_message("You're verified! Welcome.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to assign that role — ask a mod.", ephemeral=True
            )


@lockdown.error
@unlock.error
@raidmode.error
@kick_new_accounts.error
@ban_batch.error
@announce.error
@announce_all.error
@dm_role.error
@add_server.error
@verify_setup.error
async def on_mod_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message(
            "You need Kick/Ban Members or Administrator permission to use this.", ephemeral=True
        )
    else:
        raise error


# ---------------------------------------------------------------------------
# Render free-tier support: a tiny web server so Render sees an open port
# and your uptime monitor (e.g. UptimeRobot) can ping it to prevent spin-down.
# ---------------------------------------------------------------------------

web = Flask(__name__)


@web.route("/")
def health():
    return "Anti-raid bot is running.", 200


def run_web():
    port = int(os.getenv("PORT", 10000))  # Render injects PORT automatically
    web.run(host="0.0.0.0", port=port)


def run_bot_once():
    """Run the bot a single time. discord.py tears down its internal network
    session once bot.run() exits on error, so a Client CANNOT be re-run with
    bot.run() again in the same process — that throws 'RuntimeError: Session
    is closed'. The only reliable way to retry a login is a fresh OS process,
    which the host platform's own restart policy already gives us."""
    try:
        bot.run(TOKEN)
    except discord.errors.LoginFailure:
        # Wrong/revoked token — retrying won't help, fail fast with a clear message
        # so the host's restart loop doesn't spin forever on an unfixable error.
        print("FATAL: Discord rejected the bot token (LoginFailure / 401). "
              "Verify the current bot token in the Developer Portal and Render Variables; "
              "check for whitespace or an outdated token.")
        raise SystemExit(1)
    except discord.errors.HTTPException as e:
        if e.status == 429:
            # Let a short pause happen before this process exits, then exit
            # non-zero so the platform (Render/Railway) restarts us in a FRESH
            # process — which gets a clean aiohttp session and often a
            # different outcome than retrying in-process ever could.
            print(f"Discord returned HTTP 429 during startup: {e}. "
                  "The cause is not established from status code alone. "
                  "Avoid rapid restart loops; inspect Discord/Cloudflare response details "
                  "and wait before retrying.")
            time.sleep(60)
            raise SystemExit(1)
        raise


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set the DISCORD_BOT_TOKEN environment variable before running.")
    threading.Thread(target=run_web, daemon=True).start()
    run_bot_once()
