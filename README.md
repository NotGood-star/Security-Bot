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
| `/verify-setup` | Posts a "Verify" button; clicking it grants a Verified role, so new members can't post/raid until they click. |

Auto-detection: if 5+ members join within 10 seconds (tune `RAID_JOIN_COUNT` /
`RAID_JOIN_WINDOW` at the top of `bot.py`), the bot automatically locks the
server and pings a channel so mods can react.

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

## Notes

- State (lockdown status, raidmode toggle, saved permissions) is kept in memory.
  If you restart the bot mid-lockdown, `/unlock` won't know the prior permissions —
  for production use, swap `guild_state` for a small SQLite/JSON store.
- `/kick-new-accounts` always shows you the exact member list and requires a
  button click to proceed — nothing is ever auto-kicked without review (except
  the lockdown itself, which is non-destructive and fully reversible).
- Tune `RAID_JOIN_COUNT` and `RAID_JOIN_WINDOW` in `bot.py` to match your
  server's normal traffic so real growth spurts aren't mistaken for raids.
