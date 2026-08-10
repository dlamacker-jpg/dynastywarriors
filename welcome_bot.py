#!/usr/bin/env python3
"""
Persistent bot for Dynasty Warriors. Two jobs:

  1. Greet new members in #newbies and auto-assign the starter role.
  2. Keep two live boards, both driven by coach nicknames (rule #9):
       - #team-assignments : "Teams Taken"
       - #teams-available  : "Teams Available" (everything in teams_fbs.json not
                              claimed), one team per line, grouped by conference.
     Both refresh whenever someone joins, changes nickname, gains/loses a role,
     or leaves.

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
import unicodedata
from pathlib import Path

import discord

WELCOME_CHANNEL = "newbies"
ROLE_ON_JOIN = "Coach"           # set to None to disable auto-role; also the role counted on the boards
TAKEN_CHANNEL = "team-assignments"
AVAILABLE_CHANNEL = "teams-available"
TEAMS_FILE = Path(__file__).with_name("teams_fbs.json")
RESERVED_FILE = Path(__file__).with_name("reserved.json")
ALIASES_FILE = Path(__file__).with_name("aliases.json")

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
    "• 🏫 Claim your team in **#how-to-join** (browse open schools in **#teams-available**)\n"
    "• ✏️ Change your server nickname to the **school you're using** (rule #9) — "
    "this updates the team boards automatically\n"
    "• 👋 Say what's up and introduce yourself right here\n\n"
    "It's just a game — play hard, have fun. Let's run it. 🏆"
)


def base_norm(name: str) -> str:
    """Reduce a name to comparable letters only.

    - NFKD unicode folding turns fancy fonts (𝕬𝖑𝖆𝖇𝖆𝖒𝖆) and accents into plain ASCII
    - keep letters a-z only, dropping emojis, digits/years (Bama 2027), spaces, punctuation
    """
    decomposed = unicodedata.normalize("NFKD", name)
    return re.sub(r"[^a-z]", "", decomposed.lower())


def normalize(name: str) -> str:
    """Normalize a name, then resolve nickname/mascot aliases to the official school."""
    key = base_norm(name)
    if key in ALIASES:
        key = base_norm(ALIASES[key])
    return key


def load_conferences() -> dict[str, list[str]]:
    with TEAMS_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_reserved() -> dict[str, str]:
    """Manually pre-claimed teams: {team: owner label}. Missing file = none."""
    if not RESERVED_FILE.exists():
        return {}
    with RESERVED_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_aliases() -> dict[str, str]:
    """{normalized nickname/mascot: official school name}. Missing file = none."""
    if not ALIASES_FILE.exists():
        return {}
    with ALIASES_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    for alias, official in data.items():
        if alias.startswith("_"):
            continue
        out[base_norm(alias)] = official
    return out


def pack(items: list[str], limit: int = 1900) -> list[str]:
    """Pack whole items into as few messages as possible, joined by blank lines."""
    blocks, cur = [], ""
    for item in items:
        if cur and len(cur) + 2 + len(item) > limit:
            blocks.append(cur)
            cur = item
        else:
            cur = f"{cur}\n\n{item}" if cur else item
    if cur:
        blocks.append(cur)
    return blocks


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit("Set DISCORD_BOT_TOKEN (Railway Variables, or your terminal).")

    conferences = load_conferences()
    reserved = load_reserved()
    ALIASES.update(load_aliases())  # merge file aliases over the built-in defaults
    intents = discord.Intents.default()
    intents.members = True  # requires the Server Members Intent (see header)
    client = discord.Client(intents=intents)
    roster_lock = asyncio.Lock()

    def coach_members(guild: discord.Guild):
        coach_role = discord.utils.get(guild.roles, name=ROLE_ON_JOIN) if ROLE_ON_JOIN else None
        for m in guild.members:
            if m.bot:
                continue
            if coach_role and coach_role not in m.roles:
                continue
            yield m

    def gather_teams(guild: discord.Guild):
        """Return (taken, conflicts, unset, taken_norm).

        A team held by exactly one coach/reserved entry -> taken. A team held by
        two or more -> a conflict (duplicate) that needs resolving. Every held
        team (including conflicts) counts toward taken_norm so it leaves #teams-available.
        """
        holders: dict[str, list[tuple[str, str]]] = {}  # norm -> [(display, owner), ...]
        unset = []
        for m in coach_members(guild):
            if m.nick:
                holders.setdefault(normalize(m.nick), []).append((m.nick, m.name))
            else:
                unset.append(m.name)
        for team, owner in reserved.items():
            holders.setdefault(normalize(team), []).append((team, owner))

        taken, conflicts = [], []
        for entries in holders.values():
            if len(entries) == 1:
                taken.append(entries[0])
            else:
                conflicts.append((entries[0][0], [owner for _, owner in entries]))
        taken.sort(key=lambda t: t[0].lower())
        conflicts.sort(key=lambda c: c[0].lower())
        return taken, conflicts, unset, set(holders.keys())

    def build_taken_blocks(guild: discord.Guild) -> list[str]:
        taken, conflicts, unset, _ = gather_teams(guild)

        lines = [
            "# 🏈 Teams Taken — Dynasty Warriors",
            "*Auto-updates as coaches set their nickname to their school (rule #9). "
            "Open schools are in #teams-available.*",
            "",
            f"**Taken ({len(taken) + len(conflicts)}):**",
        ]
        if taken:
            lines += [f"• **{team}** — {owner}" for team, owner in taken]
        elif not conflicts:
            lines += ["*None yet.*"]
        if conflicts:
            lines += ["", f"**⚠️ Conflicts — same team claimed twice ({len(conflicts)}) — resolve!:**"]
            lines += [
                f"• **{team}** — {', '.join(owners)}  ← duplicate! all but one must pick another"
                for team, owners in conflicts
            ]
        if unset:
            lines += ["", f"**Still need to set a nickname ({len(unset)}):**"]
            lines += [f"• {name} — ⚠️ rename yourself to your school" for name in unset]
        return pack(["\n".join(lines)])  # one section; pack only splits if it exceeds the limit

    def build_available_blocks(guild: discord.Guild) -> list[str]:
        *_, taken_norm = gather_teams(guild)
        sections, total = [], 0
        for conf, teams in conferences.items():
            open_teams = [t for t in teams if normalize(t) not in taken_norm]
            total += len(open_teams)
            if open_teams:
                body = "\n".join(f"• {t}" for t in open_teams)
                sections.append(f"## {conf} ({len(open_teams)})\n{body}")
        header = (
            f"# ✅ Teams Available ({total})\n"
            "*Pick an open school below, then set it as your server nickname (rule #9) to claim it. "
            "It'll move to #team-assignments automatically.*"
        )
        return pack([header] + sections)

    async def sync_channel(channel: discord.TextChannel, blocks: list[str]) -> None:
        no_pings = discord.AllowedMentions.none()
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

    async def refresh_all(guild: discord.Guild) -> None:
        async with roster_lock:
            taken_ch = discord.utils.get(guild.text_channels, name=TAKEN_CHANNEL)
            avail_ch = discord.utils.get(guild.text_channels, name=AVAILABLE_CHANNEL)
            if taken_ch is not None:
                await sync_channel(taken_ch, build_taken_blocks(guild))
            if avail_ch is not None:
                await sync_channel(avail_ch, build_available_blocks(guild))

    @client.event
    async def on_ready() -> None:
        print(f"Bot online as {client.user}. Greeting members and tracking teams...")
        for guild in client.guilds:
            await refresh_all(guild)

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

        await refresh_all(member.guild)

    @client.event
    async def on_member_update(before: discord.Member, after: discord.Member) -> None:
        nick_changed = before.nick != after.nick
        if not (nick_changed or set(before.roles) != set(after.roles)):
            return

        # If they just picked a team another coach already holds, warn them.
        if nick_changed and after.nick:
            norm_new = normalize(after.nick)
            clash = any(
                m.id != after.id and m.nick and normalize(m.nick) == norm_new
                for m in coach_members(after.guild)
            )
            if clash:
                try:
                    await after.send(
                        f"⚠️ Heads up — **{after.nick}** is already taken in "
                        f"{after.guild.name}. No duplicate teams allowed, so please pick a "
                        "different school. Open teams are listed in #teams-available."
                    )
                except discord.Forbidden:
                    pass  # their DMs are closed; the board still flags the conflict

        await refresh_all(after.guild)

    @client.event
    async def on_member_remove(member: discord.Member) -> None:
        await refresh_all(member.guild)

    client.run(token)


if __name__ == "__main__":
    main()
