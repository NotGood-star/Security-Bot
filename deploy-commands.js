require("dotenv").config();
const { REST, Routes, SlashCommandBuilder, PermissionFlagsBits } = require("discord.js");

const commands = [
  new SlashCommandBuilder().setName("opt-in").setDescription("Opt in to receive server announcement DMs."),
  new SlashCommandBuilder().setName("opt-out").setDescription("Stop receiving announcement DMs."),
  new SlashCommandBuilder()
    .setName("dm-all").setDescription("Send an announcement only to members who opted in.")
    .addStringOption(o => o.setName("message").setDescription("Announcement text").setRequired(true).setMaxLength(1800))
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild),
  new SlashCommandBuilder()
    .setName("invite").setDescription("Create a voluntary invite to this server.")
    .addIntegerOption(o => o.setName("max_uses").setDescription("0 = unlimited").setMinValue(0).setMaxValue(100))
    .addIntegerOption(o => o.setName("max_age_hours").setDescription("0 = never expires").setMinValue(0).setMaxValue(168)),
  new SlashCommandBuilder()
    .setName("invite-info").setDescription("View information about an invite.")
    .addStringOption(o => o.setName("code").setDescription("Invite URL or code").setRequired(true).setMaxLength(100)),
  new SlashCommandBuilder().setName("announcement-status").setDescription("Show the opt-in recipient count.")
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild),
  new SlashCommandBuilder().setName("add-members").setDescription("Create a consent-based OAuth2 link for joining this destination server.")
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
].map(c => c.toJSON());

(async () => {
  const { DISCORD_TOKEN, CLIENT_ID, GUILD_ID } = process.env;
  if (!DISCORD_TOKEN || !CLIENT_ID || !GUILD_ID) throw new Error("Set DISCORD_TOKEN, CLIENT_ID, and GUILD_ID first.");
  await new REST({ version: "10" }).setToken(DISCORD_TOKEN)
    .put(Routes.applicationGuildCommands(CLIENT_ID, GUILD_ID), { body: commands });
  console.log(`Registered ${commands.length} slash commands.`);
})().catch(e => { console.error(e); process.exit(1); });