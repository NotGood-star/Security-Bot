# Anti-Raid Discord Bot

A defensive moderation bot that protects your server during raids — without
shipping a "ban everyone" or "delete every channel" command. Those commands
are the same whether a raider or an owner runs them, so instead this bot
focuses on **containment and reversible action**.

## Commands

| Command | What it does |
|---|---|
| `/lockdown` | Instantly blocks @everyone from sending messages / joining voice in every channel. Reversible. |
| `/unlock` | Restores the permissions from before `/lockdown`. |
| `/raidmode on\|off` | Toggle automatic lockdown when join-spikes are detected. |
| `/kick-new-accounts <hours>` | Shows every account created in the last N hours, then kicks only on your confirmation. |
| `/ban-batch <member1...member10>` | Bans up to 10 specific members you name, after a preview + confirm. Owner, admins, and mods can't be targeted. |
| `/announce <channel> <message>` | Posts an embed to one channel you pick. |
| `/announce-all <message>` | Posts an embed to every text channel (shows the full channel list and requires confirm first). |
| `/dm-role <role> <message>` | DMs every member with a chosen role (preview + confirm, rate-limited). There is deliberately no "DM everyone" command — see note below. |
| `/add-server` | Posts a button members click to OAuth-authorize joining your support server. Consent-based by design (Discord requires the user's own click to authorize `guilds.join`). |
| `/verify-setup` | Posts a "Verify" button; clicking it grants a Verified role, so new members can't post/raid until they click. |

Auto-detection: if 5+ members join within 10 seconds (tune `RAID_JOIN_COUNT` /
`RAID_JOIN_WINDOW` at the top of `bot.py`), the bot automatically locks the
server and pings a channel so mods can react.

## Why there's no "DM everyone" command

Discord's Developer Terms of Service explicitly prohibit sending unsolicited
DMs or spam to users via the API, and bots caught doing this get their tokens
revoked (and the owning account flagged). `/dm-role` covers the real use case
— reaching your members — by scoping it to whoever holds a role you choose
(e.g. make an opt-in "Announcements" role and DM only those members). That
keeps it consensual and compliant while still being genuinely useful.

## Setting up `/add-server` (OAuth support-server join)

1. In your bot's application at https://discord.com/developers/applications,
   go to **OAuth2** → add a redirect URI. The simplest option: use
   `https://discord.com/oauth2/authorized` (Discord's own generic landing
   page) — no server of your own required.
2. Copy your **Client ID** from the **General Information** tab.
3. Get your support server's ID (enable Developer Mode in Discord → right-click
   the server icon → Copy Server ID).
4. Set these environment variables (Railway → Variables, same place as the token):
   ```
   DISCORD_CLIENT_ID=your-client-id
   DISCORD_OAUTH_REDIRECT_URI=https://discord.com/oauth2/authorized
   SUPPORT_SERVER_GUILD_ID=your-support-server-id
   ```
5. Run `/add-server` in any channel — it posts a button members click to
   authorize joining. They always see exactly what's being requested
   (`identify` + `guilds.join`) before approving.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Create a bot application at https://discord.com/developers/applications
   - Under **Bot**, enable **Server Members Intent** and **Message Content Intent**
   - Copy the bot token
3. Set the token as an environment variable:
   ```bash
   cp .env.example .env
   # edit .env and paste your token
   export $(cat .env | xargs)   # or use python-dotenv / your host's env settings
   ```
4. Invite the bot to your server with scopes `bot` + `applications.commands` and permissions:
   - Manage Channels, Manage Roles, Kick Members, Send Messages, Manage Messages, Moderate Members
5. Run it:
   ```bash
   python bot.py
   ```

## Deploying on GitHub + Render (free tier)

1. **Push to GitHub**
   ```bash
   cd antiraid-bot
   git init
   git add .
   git commit -m "Anti-raid Discord bot"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```
   `.gitignore` already excludes `.env` so your token never gets committed.

2. **Create the Render service**
   - Go to https://dashboard.render.com → **New** → **Web Service**
   - Connect your GitHub repo
   - Render will detect `render.yaml` automatically (Build: `pip install -r requirements.txt`, Start: `python bot.py`)
   - Under **Environment**, add `DISCORD_BOT_TOKEN` with your real token (this is the `sync: false` var — Render asks you to fill it in the dashboard, it's never in the repo)
   - Plan: **Free**

3. **Why the Flask server is in `bot.py`**
   Render's free web-service tier requires your app to bind to the `$PORT`
   it provides, or it considers the deploy failed. `bot.py` starts a tiny
   Flask server on that port in a background thread (`/` returns "Anti-raid
   bot is running.") purely so Render sees the port open — your actual bot
   logic is the discord.py client running alongside it.

4. **Keep it awake (free tier sleeps after inactivity)**
   Free Render web services spin down after ~15 minutes with no HTTP traffic.
   Use a free uptime pinger (e.g. https://uptimerobot.com) to hit your
   Render URL (shown on your service's dashboard, e.g.
   `https://antiraid-discord-bot.onrender.com`) every 5 minutes so the bot
   stays online.

5. **Redeploy on changes**
   Just `git push` — Render auto-deploys on every push to `main` by default.

## If slash commands (`/lockdown`, `/announce`, etc.) don't show up in Discord

Check these in order:

1. **Invite scope** — the invite link must include BOTH `bot` AND
   `applications.commands` scopes. If the bot was invited with only `bot`
   checked, slash commands will never appear no matter what the code does.
   Fix: Developer Portal → your app → **OAuth2 → URL Generator** → check
   both scopes → re-invite the bot with that link (no need to kick it first).
2. **Global sync delay** — a global `bot.tree.sync()` can take up to an hour
   to show up everywhere. For instant testing, set a `TEST_GUILD_ID` env var
   to your server's ID (Developer Mode on → right-click server icon → Copy
   Server ID) — the bot will sync to just that server instantly on startup.
   Remove the variable once you're ready to go global.
3. **Check the logs** for `slash commands synced` — if that line never
   appears, the bot never reached `on_ready`; look for errors above it.
4. **Restart your Discord client** — it sometimes caches the old command list.

## If you keep hitting "429 Too Many Requests" / Cloudflare error 1015 on login

This means **Discord rejected the login itself**, before the bot even starts —
not a bug in this code. It happens because Render's free tier shares a small
pool of outbound IPs across many apps, and if other apps on your IP have been
hitting Discord's API hard, Discord's Cloudflare rate-limits the whole IP.

Important: discord.py cannot cleanly retry a login **within the same process**
once it fails once (its internal network session gets closed). So `bot.py`
does NOT loop and retry in-process — instead, on a 429 it waits 30 seconds
and then exits with a non-zero status, so the **host platform's own restart
policy** (Render/Railway) relaunches it as a fresh process, which gets a
clean session and often succeeds. Render web services restart automatically
whenever the process exits — no config needed — which is what makes this work.

If it keeps happening repeatedly:

**Switch to Railway** (often has better IP reputation with Discord):
1. Push the same repo to GitHub (already done if you followed the steps above)
2. Go to https://railway.app → **New Project** → **Deploy from GitHub repo**
3. Railway auto-detects `railway.json` (same build/start commands as Render)
4. Add `DISCORD_BOT_TOKEN` under **Variables**
5. Deploy — no port-binding issues since Railway doesn't require one for background services, but the built-in Flask server still works fine if you want the health check

Railway's free tier gives ~$5/month of usage credit, which easily covers a
small Discord bot running continuously.

## Notes

- State (lockdown status, raidmode toggle, saved permissions) is kept in memory.
  If you restart the bot mid-lockdown, `/unlock` won't know the prior permissions —
  for production use, swap `guild_state` for a small SQLite/JSON store.
- `/kick-new-accounts` always shows you the exact member list and requires a
  button click to proceed — nothing is ever auto-kicked without review (except
  the lockdown itself, which is non-destructive and fully reversible).
- Tune `RAID_JOIN_COUNT` and `RAID_JOIN_WINDOW` in `bot.py` to match your
  server's normal traffic so real growth spurts aren't mistaken for raids.
