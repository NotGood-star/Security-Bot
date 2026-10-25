# Discord OAuth2 Utility Bot (JavaScript + Render)

This project includes opt-in announcement DMs, invite utilities, and an OAuth2 flow for people who choose to join the configured destination server.

## Commands

- `/opt-in` — consent to announcement DMs.
- `/opt-out` — stop future announcement DMs.
- `/dm-all message:<text>` — send an announcement only to opted-in users.
- `/invite` — create a voluntary invite link.
- `/invite-info code:<invite>` — view invite information.
- `/announcement-status` — show opt-in count.
- `/add-members` — create a link that starts a consent-based OAuth2 join flow.

The OAuth flow requests `identify guilds.join`. The user must authorize it. The bot must already be in the destination server and have `Create Instant Invite` permission. See the official Discord docs:
https://discord.com/developers/docs/resources/guild#add-guild-member
https://discord.com/developers/docs/topics/oauth2

## 1. Create the Discord application

1. Go to https://discord.com/developers/applications and create an application.
2. Under **Bot**, create the bot and copy its token privately.
3. Copy the **Application ID** from General Information as `CLIENT_ID`.
4. Under **OAuth2 → General**, add a Redirect URL exactly matching `OAUTH_REDIRECT_URI`, e.g. `https://your-service.onrender.com/oauth/callback`.
5. Add the bot to your destination server with `bot` and `applications.commands` scopes. Grant it `View Channels`, `Send Messages`, `Create Instant Invite`, and other permissions needed for the channels it uses.
6. Copy the destination server ID as `GUILD_ID` (enable Developer Mode in Discord, then copy server ID).

## 2. Configure and deploy on Render

1. Upload this project to a GitHub repository.
2. In Render, create a **Web Service** from the repository.
3. Build command: `npm install`
4. Start command: `npm start`
5. Add the variables from `.env.example` in Render's Environment settings. Set `BASE_URL` to the public Render URL and `OAUTH_REDIRECT_URI` to `${BASE_URL}/oauth/callback` using the exact expanded URL. Do not include trailing slash.
6. Deploy and wait for the service to become live.

The included `render.yaml` is a Blueprint starter; Render may still require you to enter secret values manually.

## 3. Register slash commands

Run locally after creating `.env`:
```bash
npm install
npm run register
```
`GUILD_ID` is used for fast guild-scoped command registration. You can also run this from a local computer or another Node.js environment. Avoid running command registration on every production restart.

## 4. Important storage note

The sample stores announcement opt-ins in `data/opted-in.json`. Render filesystems may be ephemeral, so opt-in data can be lost on redeploy/restart. For a production deployment, replace this JSON file with a persistent database (for example, PostgreSQL) before relying on long-term consent records. OAuth tokens are not written to disk or logged.

## 5. Test

1. Run `/opt-in` with a test account.
2. Run `/announcement-status` as a server manager.
3. Run `/dm-all message: Test announcement` and check the opt-in account's DMs.
4. Run `/add-members`, open the link in a browser, and authorize with a test account.
5. Check Render logs if the join fails. The bot must already be in the configured destination server and have `Create Instant Invite` permission.
