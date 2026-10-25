require("dotenv").config();
const fs = require("node:fs");
const path = require("node:path");
const { Client, GatewayIntentBits, Events, PermissionFlagsBits, EmbedBuilder, MessageFlags } = require("discord.js");
const { startOAuthServer } = require("./oauth-server");

const required = ["DISCORD_TOKEN", "CLIENT_ID", "CLIENT_SECRET", "GUILD_ID", "ANNOUNCEMENT_CHANNEL_ID", "BASE_URL", "OAUTH_REDIRECT_URI"];
for (const key of required) if (!process.env[key]) {
  console.error(`Missing environment variable: ${key}`);
  process.exit(1);
}

const DATA_DIR = path.join(__dirname, "..", "data");
fs.mkdirSync(DATA_DIR, { recursive: true });
const OPT_IN_FILE = path.join(DATA_DIR, "opted-in.json");
function readOptIns() {
  try {
    const data = JSON.parse(fs.readFileSync(OPT_IN_FILE, "utf8"));
    return new Set(Array.isArray(data) ? data.filter(v => typeof v === "string") : []);
  } catch { return new Set(); }
}
const optedIn = readOptIns();
function saveOptIns() { fs.writeFileSync(OPT_IN_FILE, JSON.stringify([...optedIn], null, 2)); }
function isAdmin(i) {
  const ids = (process.env.ADMIN_USER_IDS || "").split(",").map(s => s.trim()).filter(Boolean);
  return ids.includes(i.user.id) || Boolean(i.memberPermissions?.has(PermissionFlagsBits.ManageGuild));
}
async function logAction(client, message) {
  if (!process.env.LOG_CHANNEL_ID) return;
  try {
    const ch = await client.channels.fetch(process.env.LOG_CHANNEL_ID);
    if (ch?.isTextBased()) await ch.send({ content: message });
  } catch (e) { console.warn("Log failed:", e.message); }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
const client = new Client({ intents: [GatewayIntentBits.Guilds] });

client.once(Events.ClientReady, c => console.log(`Logged in as ${c.user.tag}; ${optedIn.size} opted-in recipient(s).`));

client.on(Events.InteractionCreate, async i => {
  if (!i.isChatInputCommand()) return;
  try {
    if (i.commandName === "opt-in") {
      optedIn.add(i.user.id); saveOptIns();
      return i.reply({ content: "✅ You're opted in to announcement DMs. Use `/opt-out` any time to stop.", flags: MessageFlags.Ephemeral });
    }
    if (i.commandName === "opt-out") {
      const existed = optedIn.delete(i.user.id); saveOptIns();
      return i.reply({ content: existed ? "✅ You're opted out of future announcement DMs." : "You're not currently opted in.", flags: MessageFlags.Ephemeral });
    }
    if (i.commandName === "dm-all") {
      if (!i.inGuild() || !isAdmin(i)) return i.reply({ content: "Manage Server permission is required.", flags: MessageFlags.Ephemeral });
      const message = i.options.getString("message", true).trim();
      if (!message) return i.reply({ content: "Please provide a message.", flags: MessageFlags.Ephemeral });
      await i.deferReply({ flags: MessageFlags.Ephemeral });
      const ids = [...optedIn];
      if (!ids.length) return i.editReply("Nobody has opted in yet. Members can use `/opt-in`.");
      const batch = Math.max(1, Math.min(10, Number(process.env.DM_BATCH_SIZE) || 5));
      const delay = Math.max(1000, Number(process.env.DM_BATCH_DELAY_MS) || 3000);
      const embed = new EmbedBuilder().setColor(0x5865F2).setTitle("📢 Server Announcement")
        .setDescription(message).setFooter({ text: `${i.guild.name} • Use /opt-out to stop DMs` }).setTimestamp();
      let sent = 0, failed = 0;
      for (let p = 0; p < ids.length; p += batch) {
        for (const id of ids.slice(p, p + batch)) {
          try { await (await client.users.fetch(id)).send({ embeds: [embed] }); sent++; }
          catch { failed++; }
        }
        if (p + batch < ids.length) await sleep(delay);
      }
      await i.editReply(`Announcement complete. Delivered: **${sent}** · Could not deliver: **${failed}** · Opted-in recipients: **${ids.length}**`);
      await logAction(client, `Announcement by ${i.user.tag}: delivered ${sent}, failed ${failed}.`);
      return;
    }
    if (i.commandName === "invite") {
      if (!i.inGuild()) return i.reply({ content: "Use this command in a server.", flags: MessageFlags.Ephemeral });
      const ch = i.channel;
      if (!ch?.isTextBased() || !ch.createInvite) return i.reply({ content: "I can't create an invite in this channel.", flags: MessageFlags.Ephemeral });
      const uses = i.options.getInteger("max_uses") ?? 0;
      const hours = i.options.getInteger("max_age_hours") ?? 24;
      const invite = await ch.createInvite({ maxUses: uses, maxAge: hours * 3600, unique: true, reason: `Created by ${i.user.tag}` });
      return i.reply({ content: `🔗 ${invite.url}\nUses: ${uses || "Unlimited"} · Expiry: ${hours ? `${hours} hour(s)` : "Never"}\nPeople join voluntarily using this link.`, flags: MessageFlags.Ephemeral });
    }
    if (i.commandName === "invite-info") {
      const raw = i.options.getString("code", true).trim();
      const code = raw.match(/(?:discord\.gg\/|discord(?:app)?\.com\/invite\/)([A-Za-z0-9-]+)/i)?.[1] || raw;
      try {
        const inv = await client.fetchInvite(code, { withCounts: true });
        return i.reply({ embeds: [new EmbedBuilder().setColor(0x5865F2).setTitle("Invite Information")
          .addFields({ name: "Server", value: inv.guild?.name || "Unknown", inline: true },
            { name: "Code", value: inv.code, inline: true },
            { name: "Approx. members", value: String(inv.memberCount ?? "Unavailable"), inline: true },
            { name: "Approx. online", value: String(inv.presenceCount ?? "Unavailable"), inline: true },
            { name: "URL", value: inv.url })], flags: MessageFlags.Ephemeral });
      } catch { return i.reply({ content: "Invite not found, expired, or inaccessible.", flags: MessageFlags.Ephemeral }); }
    }
    if (i.commandName === "announcement-status") {
      if (!isAdmin(i)) return i.reply({ content: "Manage Server permission is required.", flags: MessageFlags.Ephemeral });
      return i.reply({ content: `📬 **${optedIn.size}** user(s) opted in.`, flags: MessageFlags.Ephemeral });
    }
    if (i.commandName === "add-members") {
      if (!i.inGuild() || !isAdmin(i)) return i.reply({ content: "Manage Server permission is required.", flags: MessageFlags.Ephemeral });
      const url = `${process.env.BASE_URL.replace(/\/$/, "")}/oauth/start`;
      return i.reply({ content: `🔗 **Consent-based join link**\n${url}\nShare this link with people who choose to authorize joining **${i.guild.name}**. Each person must sign in and approve the request.`, flags: MessageFlags.Ephemeral });
    }
  } catch (e) {
    console.error("Command error:", e);
    const msg = "Something went wrong. Check Render logs and bot permissions.";
    if (i.deferred || i.replied) await i.editReply(msg).catch(() => {});
    else await i.reply({ content: msg, flags: MessageFlags.Ephemeral }).catch(() => {});
  }
});

startOAuthServer(client);
client.login(process.env.DISCORD_TOKEN);
