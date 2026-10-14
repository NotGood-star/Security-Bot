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
  /verify-setup             - Post a one-click verification gate for new members

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

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

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

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} — slash commands synced.")


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


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set the DISCORD_BOT_TOKEN environment variable before running.")
    threading.Thread(target=run_web, daemon=True).start()
    bot.run(TOKEN)
