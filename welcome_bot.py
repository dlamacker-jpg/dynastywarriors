#!/usr/bin/env python3
"""
Persistent bot for Dynasty Warriors. Two jobs:

  1. Greet new members in #newbies and auto-assign the starter role.
  2. Keep a live "Teams Taken" roster in #team-assignments, auto-updated from
     coaches' nicknames (rule #9: everyone renames to their school). It refreshes
     whenever someone joins, changes nickname, gains/loses a role, or leaves.

Unlike the one-shot post_*.py scripts, this stays running (that's why it lives on
Railway). It only reacts while running.

Requirements:
  1. Enable the Server Members Intent:
     Developer Portal -> your app -> Bot -> Privileged Gateway Intents ->
     turn ON "Server Members Intent" -> Save.
  2. Env vars (set in Railway's Variables, or your terminal for local testing):
       DISCORD_BOT_TOKEN, DISCORD_GUILD_ID
  3. Run: python3 welcome_bot.py
"""

import os
import sys

import discord

WELCOME_CHANNEL = "newbies"
ROLE_ON_JOIN = "Coach"          # set to None to disable auto-role; also the role counted in the roster
TEAMS_CHANNEL = "team-assignments"
ROSTER_HEADER = "# 🏈 Teams Taken — Dynasty Warriors"

WELCOME_MESSAGE = (
    "🏈 Welcome to **{server}**, {mention}! Glad to have you in the league.\n\n"
    "Get started:\n"
    "• 📜 Read the rules in **#rules**\n"
    "• 🏫 Claim your team in **#how-to-join**\n"
    "• ✏️ Change your server nickname to the **school you're using** (rule #9) — "
    "this also adds you to the **#team-assignments** board automatically\n"
    "• 👋 Say what's up and introduce yourself right here\n\n"
    "It's just a game — play hard, have fun. Let's run it. 🏆"
)


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit("Set DISCORD_BOT_TOKEN (Railway Variables, or your terminal).")

    intents = discord.Intents.default()
    intents.members = True  # requires the Server Members Intent (see header)
    client = discord.Client(intents=intents)
    roster_cache: dict[int, discord.Message] = {}  # channel id -> the roster message

    async def refresh_roster(guild: discord.Guild) -> None:
        """Rebuild the Teams Taken message from current Coach nicknames."""
        channel = discord.utils.get(guild.text_channels, name=TEAMS_CHANNEL)
        if channel is None:
            return
        coach_role = discord.utils.get(guild.roles, name=ROLE_ON_JOIN) if ROLE_ON_JOIN else None

        taken, unset = [], []
        for m in guild.members:
            if m.bot:
                continue
            is_coach = coach_role in m.roles if coach_role else True
            if not is_coach:
                continue
            if m.nick:  # nickname set == their school
                taken.append((m.nick, m.name))
            else:
                unset.append(m.name)
        taken.sort(key=lambda t: t[0].lower())

        lines = [
            ROSTER_HEADER,
            "*Auto-updates as coaches set their nickname to their school (rule #9).*",
            "",
            f"**Taken ({len(taken)}):**",
        ]
        lines += [f"• **{team}** — {name}" for team, name in taken] or ["*None yet.*"]
        if unset:
            lines += ["", f"**Still need to set a nickname ({len(unset)}):**"]
            lines += [f"• {name} — ⚠️ rename yourself to your school" for name in unset]
        content = "\n".join(lines)
        if len(content) > 1990:  # Discord's 2000-char message limit
            content = content[:1970].rsplit("\n", 1)[0] + "\n…(list truncated)"

        no_pings = discord.AllowedMentions.none()
        msg = roster_cache.get(channel.id)
        if msg is None:  # find an existing roster message on first run
            async for old in channel.history(limit=50):
                if old.author.id == client.user.id and old.content.startswith(ROSTER_HEADER):
                    msg = old
                    break
        if msg is None:
            msg = await channel.send(content, allowed_mentions=no_pings)
        else:
            await msg.edit(content=content, allowed_mentions=no_pings)
        roster_cache[channel.id] = msg

    @client.event
    async def on_ready() -> None:
        print(f"Bot online as {client.user}. Greeting members and tracking teams...")
        for guild in client.guilds:
            await refresh_roster(guild)

    @client.event
    async def on_member_join(member: discord.Member) -> None:
        # Auto-assign the starter role.
        if ROLE_ON_JOIN:
            role = discord.utils.get(member.guild.roles, name=ROLE_ON_JOIN)
            if role is None:
                print(f"Role '{ROLE_ON_JOIN}' not found; skipping auto-role.")
            else:
                try:
                    await member.add_roles(role, reason="Auto-role on join")
                    print(f"Gave {member} the {ROLE_ON_JOIN} role.")
                except discord.Forbidden:
                    print(
                        f"Couldn't assign {ROLE_ON_JOIN} to {member}: check that the bot's "
                        "role sits ABOVE it in Server Settings -> Roles."
                    )

        # Greet them.
        channel = discord.utils.get(member.guild.text_channels, name=WELCOME_CHANNEL)
        if channel is not None:
            await channel.send(
                WELCOME_MESSAGE.format(mention=member.mention, server=member.guild.name)
            )
            print(f"Greeted {member} in #{WELCOME_CHANNEL}.")
        else:
            print(f"No #{WELCOME_CHANNEL} channel; skipped greeting.")

        await refresh_roster(member.guild)

    @client.event
    async def on_member_update(before: discord.Member, after: discord.Member) -> None:
        if before.nick != after.nick or set(before.roles) != set(after.roles):
            await refresh_roster(after.guild)

    @client.event
    async def on_member_remove(member: discord.Member) -> None:
        await refresh_roster(member.guild)

    client.run(token)


if __name__ == "__main__":
    main()
