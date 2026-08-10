#!/usr/bin/env python3
"""
Persistent bot for Dynasty Warriors. Two jobs:

  1. Greet new members in #newbies and auto-assign the starter role.
  2. Keep a live board in #team-assignments: "Teams Taken" (from coach nicknames,
     rule #9) and "Teams Available" (everything in teams_fbs.json that isn't
     claimed). It refreshes whenever someone joins, changes nickname, gains/loses
     a role, or leaves.

Unlike the one-shot post_*.py scripts, this stays running (that's why it lives on
Railway). It only reacts while running.

Requirements:
  1. Enable the Server Members Intent:
     Developer Portal -> your app -> Bot -> Privileged Gateway Intents ->
     turn ON "Server Members Intent" -> Save.
  2. Env vars (Railway Variables, or your terminal for local testing):
       DISCORD_BOT_TOKEN, DISCORD_GUILD_ID
  3. Run: python3 welcome_bot.py
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import discord

WELCOME_CHANNEL = "newbies"
ROLE_ON_JOIN = "Coach"          # set to None to disable auto-role; also the role counted on the board
TEAMS_CHANNEL = "team-assignments"
TEAMS_FILE = Path(__file__).with_name("teams_fbs.json")

# Common shorthand -> official name (both get normalized before comparing).
ALIASES = {
    "bama": "Alabama",
    "uga": "Georgia",
    "pitt": "Pittsburgh",
    "umass": "Massachusetts",
    "appstate": "Appalachian State",
    "unc": "North Carolina",
    "miamioh": "Miami (OH)",
    "miamiohio": "Miami (OH)",
}

WELCOME_MESSAGE = (
    "🏈 Welcome to **{server}**, {mention}! Glad to have you in the league.\n\n"
    "Get started:\n"
    "• 📜 Read the rules in **#rules**\n"
    "• 🏫 Claim your team in **#how-to-join**\n"
    "• ✏️ Change your server nickname to the **school you're using** (rule #9) — "
    "this also updates the **#team-assignments** board automatically\n"
    "• 👋 Say what's up and introduce yourself right here\n\n"
    "It's just a game — play hard, have fun. Let's run it. 🏆"
)


def normalize(name: str) -> str:
    """Lowercase and strip everything but letters/numbers, then resolve aliases."""
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    if key in ALIASES:
        key = re.sub(r"[^a-z0-9]", "", ALIASES[key].lower())
    return key


def load_conferences() -> dict[str, list[str]]:
    with TEAMS_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def chunk(lines: list[str], limit: int = 1900) -> list[str]:
    """Pack lines into as few messages as possible, never splitting a line."""
    blocks, cur = [], ""
    for line in lines:
        if cur and len(cur) + 1 + len(line) > limit:
            blocks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        blocks.append(cur)
    return blocks


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit("Set DISCORD_BOT_TOKEN (Railway Variables, or your terminal).")

    conferences = load_conferences()
    intents = discord.Intents.default()
    intents.members = True  # requires the Server Members Intent (see header)
    client = discord.Client(intents=intents)
    roster_lock = asyncio.Lock()

    def build_board(guild: discord.Guild) -> list[str]:
        coach_role = discord.utils.get(guild.roles, name=ROLE_ON_JOIN) if ROLE_ON_JOIN else None
        taken, unset, taken_norm = [], [], set()
        for m in guild.members:
            if m.bot:
                continue
            if coach_role and coach_role not in m.roles:
                continue
            if m.nick:
                taken.append((m.nick, m.name))
                taken_norm.add(normalize(m.nick))
            else:
                unset.append(m.name)
        taken.sort(key=lambda t: t[0].lower())

        # --- Taken block ---
        lines = [
            "# 🏈 Teams Taken — Dynasty Warriors",
            "*Auto-updates as coaches set their nickname to their school (rule #9).*",
            "",
            f"**Taken ({len(taken)}):**",
        ]
        lines += [f"• **{team}** — {name}" for team, name in taken] or ["*None yet.*"]
        if unset:
            lines += ["", f"**Still need to set a nickname ({len(unset)}):**"]
            lines += [f"• {name} — ⚠️ rename yourself to your school" for name in unset]

        # --- Available block ---
        total_open = 0
        avail_lines = []
        for conf, teams in conferences.items():
            open_teams = [t for t in teams if normalize(t) not in taken_norm]
            total_open += len(open_teams)
            if open_teams:
                avail_lines.append(f"**{conf} ({len(open_teams)}):** " + ", ".join(open_teams))
        avail_header = [f"# ✅ Teams Available ({total_open})", "*Pick an open school and set it as your nickname to claim it.*", ""]

        return chunk(lines) + chunk(avail_header + avail_lines)

    async def refresh_board(guild: discord.Guild) -> None:
        channel = discord.utils.get(guild.text_channels, name=TEAMS_CHANNEL)
        if channel is None:
            return
        blocks = build_board(guild)
        no_pings = discord.AllowedMentions.none()
        async with roster_lock:
            existing = [m async for m in channel.history(limit=100) if m.author.id == client.user.id]
            existing.reverse()  # oldest first, to match block order
            for i, content in enumerate(blocks):
                if i < len(existing):
                    if existing[i].content != content:
                        await existing[i].edit(content=content, allowed_mentions=no_pings)
                else:
                    await channel.send(content, allowed_mentions=no_pings)
            for extra in existing[len(blocks):]:
                await extra.delete()

    @client.event
    async def on_ready() -> None:
        print(f"Bot online as {client.user}. Greeting members and tracking teams...")
        for guild in client.guilds:
            await refresh_board(guild)

    @client.event
    async def on_member_join(member: discord.Member) -> None:
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

        channel = discord.utils.get(member.guild.text_channels, name=WELCOME_CHANNEL)
        if channel is not None:
            await channel.send(
                WELCOME_MESSAGE.format(mention=member.mention, server=member.guild.name)
            )
            print(f"Greeted {member} in #{WELCOME_CHANNEL}.")
        else:
            print(f"No #{WELCOME_CHANNEL} channel; skipped greeting.")

        await refresh_board(member.guild)

    @client.event
    async def on_member_update(before: discord.Member, after: discord.Member) -> None:
        if before.nick != after.nick or set(before.roles) != set(after.roles):
            await refresh_board(after.guild)

    @client.event
    async def on_member_remove(member: discord.Member) -> None:
        await refresh_board(member.guild)

    client.run(token)


if __name__ == "__main__":
    main()
