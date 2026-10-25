const express = require("express");
const crypto = require("node:crypto");

function startOAuthServer(client) {
  const app = express();
  const pendingStates = new Map();
  const PORT = Number(process.env.PORT) || 3000;
  const REDIRECT_URI = process.env.OAUTH_REDIRECT_URI;
  const CLIENT_ID = process.env.CLIENT_ID;
  const CLIENT_SECRET = process.env.CLIENT_SECRET;
  const GUILD_ID = process.env.GUILD_ID;

  app.disable("x-powered-by");
  app.get("/", (_req, res) => res.type("html").send(page("Discord Utility Bot", `
    <h1>Discord Utility Bot</h1>
    <p>This service provides opt-in announcements and a consent-based server joining flow.</p>
    <p><a href="/oauth/start">Continue to Discord authorization</a></p>
  `)));

  app.get("/health", (_req, res) => res.json({ ok: true }));

  app.get("/oauth/start", (_req, res) => {
    const state = crypto.randomBytes(32).toString("hex");
    pendingStates.set(state, Date.now());
    // Expire unused state values after 10 minutes.
    for (const [key, created] of pendingStates) if (Date.now() - created > 10 * 60 * 1000) pendingStates.delete(key);
    const params = new URLSearchParams({
      client_id: CLIENT_ID,
      response_type: "code",
      redirect_uri: REDIRECT_URI,
      scope: "identify guilds.join",
      state,
      prompt: "consent"
    });
    res.redirect(`https://discord.com/oauth2/authorize?${params.toString()}`);
  });

  app.get("/oauth/callback", async (req, res) => {
    const { code, state, error } = req.query;
    if (error) return res.status(400).type("html").send(page("Authorization cancelled", "<p>You cancelled Discord authorization. No changes were made.</p>"));
    if (typeof state !== "string" || !pendingStates.has(state)) {
      return res.status(400).type("html").send(page("Invalid session", "<p>This authorization session is invalid or expired. Please start again from the bot's link.</p>"));
    }
    pendingStates.delete(state);
    if (typeof code !== "string") return res.status(400).type("html").send(page("Missing authorization code", "<p>Discord did not return an authorization code.</p>"));

    try {
      const tokenResponse = await fetch("https://discord.com/api/v10/oauth2/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: CLIENT_ID,
          client_secret: CLIENT_SECRET,
          grant_type: "authorization_code",
          code,
          redirect_uri: REDIRECT_URI
        })
      });
      const tokenData = await tokenResponse.json();
      if (!tokenResponse.ok || !tokenData.access_token) {
        console.error("OAuth token exchange failed:", tokenData.error || tokenResponse.status);
        return res.status(400).type("html").send(page("Authorization failed", "<p>Discord authorization could not be completed. Please try again.</p>"));
      }

      const meResponse = await fetch("https://discord.com/api/v10/users/@me", {
        headers: { Authorization: `Bearer ${tokenData.access_token}` }
      });
      const user = await meResponse.json();
      if (!meResponse.ok || !user.id) throw new Error("Could not verify authorized user.");

      // The bot must already be in this destination guild and have Create Instant Invite permission.
      const guild = await client.guilds.fetch(GUILD_ID);
      const me = await guild.members.fetchMe();
      if (!me.permissions.has("CreateInstantInvite")) {
        throw new Error("The bot needs Create Instant Invite permission in the configured destination server.");
      }

      const joinResponse = await fetch(`https://discord.com/api/v10/guilds/${GUILD_ID}/members/${user.id}`, {
        method: "PUT",
        headers: {
          Authorization: `Bot ${process.env.DISCORD_TOKEN}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ access_token: tokenData.access_token })
      });

      if (!joinResponse.ok && joinResponse.status !== 204) {
        const apiError = await joinResponse.json().catch(() => ({}));
        console.error("Add guild member failed:", joinResponse.status, apiError);
        return res.status(400).type("html").send(page("Could not join server", `
          <p>Discord did not allow the join to complete. You can join using the server's normal invite link instead.</p>
          <p>No password or token needs to be shared with anyone.</p>
        `));
      }

      const result = joinResponse.status === 204 ? "You're already a member of the destination server." : "Authorization succeeded and you've been added to the destination server.";
      // Token is intentionally not persisted. It remains only in this request's memory and expires per Discord's OAuth response.
      res.type("html").send(page("You're all set!", `<h2>Welcome!</h2><p>${escapeHtml(result)}</p><p>You can close this page and return to Discord.</p>`));
      console.log(`OAuth join flow completed for Discord user ID ${user.id}.`);
    } catch (err) {
      console.error("OAuth callback error:", err.message);
      res.status(500).type("html").send(page("Something went wrong", "<p>The server could not complete this request. Please try again later or use a normal invite link.</p>"));
    }
  });

  app.listen(PORT, "0.0.0.0", () => console.log(`OAuth web server listening on port ${PORT}`));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
function page(title, body) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title><style>
  body{font-family:system-ui,sans-serif;max-width:680px;margin:12vh auto;padding:24px;line-height:1.6;background:#111827;color:#f9fafb}
  main{background:#1f2937;padding:28px;border-radius:16px}a{color:#93c5fd}h1{margin-top:0}
  </style></head><body><main>${body}</main></body></html>`;
}
module.exports = { startOAuthServer };
