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
import random
import re
import sys
import unicodedata
from pathlib import Path

import discord
from discord.ext import tasks

WELCOME_CHANNEL = "newbies"
ROLE_ON_JOIN = "Coach"           # set to None to disable auto-role; also the role counted on the boards
TAKEN_CHANNEL = "team-assignments"
AVAILABLE_CHANNEL = "teams-available"
LOSERS_CHANNEL = "later-losers"
ACTIVE_CHECK_CHANNEL = "active-checks"    # where the active check is posted
ACTIVE_CHECK_DAYS = 3                     # how often it goes out
ACTIVE_CHECK_EMOJI = "👍"                  # reaction coaches use to confirm
ACTIVE_CHECK_MARKER = "🚨 ACTIVE CHECK"    # used to find the previous check
MATCHUPS_CHANNEL = "user-game-coordination"  # where weekly user games are posted
ADVANCE_HOURS = 48                           # post the next week every 48h (the force-advance cadence)
MATCHUPS_MARKER = "🏈 Week"                    # used to find the last posted week
TEAMS_FILE = Path(__file__).with_name("teams_fbs.json")
RESERVED_FILE = Path(__file__).with_name("reserved.json")
ALIASES_FILE = Path(__file__).with_name("aliases.json")
SCHEDULE_FILE = Path(__file__).with_name("schedule.json")

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

# Rotating farewells for #later-losers. {name} = who left, {team} = the school they abandoned.
FAREWELLS_WITH_TEAM = [
    "🪦 **{name}** rage quit the dynasty. **{team}** is back on the market in #teams-available. Press F. 👋",
    "🚪 **{name}** couldn't handle the smoke and bounced. **{team}** is up for grabs in #teams-available.",
    "📉 Another one bites the dust — **{name}** abandoned **{team}**. It's open again in #teams-available.",
    "🏳️ **{name}** waved the white flag. **{team}** returns to the board in #teams-available. Later, loser.",
    "👻 **{name}** ghosted the league. **{team}** is a free agent now — claim it in #teams-available.",
    "🧢 Turns out **{name}** was all cap. **{team}** is open again in #teams-available.",
    "💀 **{name}** got exposed and dipped. **{team}** is available in #teams-available.",
    "🫡 **{name}** has left the building. **{team}** is back on the board in #teams-available.",
]
FAREWELLS_NO_TEAM = [
    "🪦 **{name}** left before even picking a team. Bold strategy. Press F. 👋",
    "🚪 **{name}** dipped out early. We hardly knew ya.",
    "👻 **{name}** ghosted the league before claiming a school. Later, loser.",
    "🫥 **{name}** vanished without ever repping a team. F.",
]

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


def load_schedule() -> dict[str, list]:
    """Season schedule: {week: [[home, away], ...]} of user-vs-user games. Missing = none."""
    if not SCHEDULE_FILE.exists():
        return {}
    with SCHEDULE_FILE.open(encoding="utf-8") as f:
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
    team_by_norm = {base_norm(t): t for teams in conferences.values() for t in teams}
    team_conf = {base_norm(t): conf for conf, teams in conferences.items() for t in teams}

    # For the substring fallback: map every team-norm and alias key to its team-norm,
    # keeping only keys >= 5 chars (shorter ones like "usc"/"cal" would false-match inside
    # unrelated names). Sorted longest-first so the longest match wins.
    key_to_team = {tn: tn for tn in team_by_norm}
    for akey, official in ALIASES.items():
        tn = base_norm(official)
        if tn in team_by_norm:
            key_to_team.setdefault(akey, tn)
    substr_keys = sorted((k for k in key_to_team if len(k) >= 5), key=len, reverse=True)

    intents = discord.Intents.default()
    intents.members = True  # requires the Server Members Intent (see header)
    client = discord.Client(intents=intents)
    roster_lock = asyncio.Lock()

    def resolve_team(display: str) -> str | None:
        """Return the team-norm a coach's display name claims, or None.

        1. exact match (with alias resolution): 'Bama' -> alabama
        2. substring fallback for school+mascot mashups: 'Texas🟠longhorn' -> texas,
           longest match wins so 'TexasTechRaiders' -> texastech, not texas.
        """
        norm = normalize(display)
        if norm in team_by_norm:
            return norm
        for key in substr_keys:
            if key in norm:
                return key_to_team[key]
        return None

    def owner_to_display(guild: discord.Guild, label: str) -> str:
        """Tag a reserved-owner label: match a member (username/display/nick) or a role name."""
        key = label.strip().lstrip("@").lower()
        for m in guild.members:
            names = {m.name.lower()}
            if m.global_name:
                names.add(m.global_name.lower())
            if m.nick:
                names.add(m.nick.lower())
            if key in names:
                return m.mention
        for r in guild.roles:
            if r.name.lower() == key:
                return r.mention  # e.g. "Commissioner" -> @Commissioner
        return label  # no match — leave as plain text

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
        holders: dict[str, list[tuple[str, str]]] = {}  # norm -> [(school, owner), ...]
        unset = []
        for m in coach_members(guild):
            # display_name = server nickname, else account display name, else username.
            tn = resolve_team(m.display_name)
            if tn is not None:
                holders.setdefault(tn, []).append((team_by_norm[tn], m.mention))
            else:
                unset.append(m.mention)
        for team, owner in reserved.items():
            holders.setdefault(base_norm(team), []).append((team, owner_to_display(guild, owner)))

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

        items = [
            "# 🏈 Teams Taken — Dynasty Warriors\n"
            "*Auto-updates as coaches set their nickname to their school (rule #9). "
            "Open schools are in #teams-available.*\n\n"
            f"**Taken ({len(taken) + len(conflicts)}):**"
        ]
        if taken:
            by_conf: dict[str, list[tuple[str, str]]] = {}
            for team, owner in taken:
                by_conf.setdefault(team_conf.get(base_norm(team), "Other"), []).append((team, owner))
            for conf in list(conferences) + ["Other"]:
                if conf in by_conf:
                    entries = sorted(by_conf[conf], key=lambda t: t[0].lower())
                    body = "\n".join(f"• **{team}** — {owner}" for team, owner in entries)
                    items.append(f"## {conf} ({len(entries)})\n{body}")
        elif not conflicts:
            items.append("*None yet.*")
        if conflicts:
            body = "\n".join(
                f"• **{team}** — {', '.join(owners)}  ← duplicate! all but one must pick another"
                for team, owners in conflicts
            )
            items.append(f"**⚠️ Conflicts — same team claimed twice ({len(conflicts)}) — resolve!:**\n{body}")
        if unset:
            body = "\n".join(f"• {name} — ⚠️ rename yourself to your school" for name in unset)
            items.append(f"**Still need to set a nickname ({len(unset)}):**\n{body}")
        return pack(items)

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

    def coach_for_team(guild: discord.Guild, team: str) -> discord.Member | None:
        """Find the coach whose nickname claims `team` (so we can @tag them)."""
        target = normalize(team)
        for m in coach_members(guild):
            if resolve_team(m.display_name) == target:
                return m
        return None

    async def post_week(guild: discord.Guild, channel: discord.TextChannel, week: int, games: list) -> None:
        lines = [
            f"{MATCHUPS_MARKER} {week} — User Games 🏈",
            f"Get your games in within {ADVANCE_HOURS} hours. @ your opponent to schedule, "
            "report in #score-reporting, and 👍 the active check.",
            "",
        ]
        for game in games:
            home, away = game[0], game[1]
            hm, am = coach_for_team(guild, home), coach_for_team(guild, away)
            h = hm.mention if hm else f"**{home}**"
            a = am.mention if am else f"**{away}**"
            lines.append(f"• {a} ({away})  @  {h} ({home})")
        if len(games) == 0:
            lines.append("*No user-vs-user games this week.*")
        await channel.send("\n".join(lines), allowed_mentions=discord.AllowedMentions(users=True))

    @tasks.loop(hours=3)
    async def advance_loop() -> None:
        """Post the next week's matchups once 48h have passed since the last week's post."""
        schedule = load_schedule()
        weeks = sorted(int(w) for w in schedule if w.isdigit())
        if not weeks:
            return
        for guild in client.guilds:
            channel = discord.utils.get(guild.text_channels, name=MATCHUPS_CHANNEL)
            if channel is None:
                continue
            last_week, last_time = 0, None
            async for m in channel.history(limit=100):
                if m.author.id == client.user.id and m.content.startswith(MATCHUPS_MARKER):
                    mt = re.match(rf"{re.escape(MATCHUPS_MARKER)} (\d+)", m.content)
                    if mt:
                        last_week, last_time = int(mt.group(1)), m.created_at
                        break
            if last_week == 0:
                next_week = weeks[0]  # season kickoff: post the first week
            else:
                if last_time and (discord.utils.utcnow() - last_time).total_seconds() < ADVANCE_HOURS * 3600:
                    continue
                upcoming = [w for w in weeks if w > last_week]
                if not upcoming:
                    continue  # season complete
                next_week = upcoming[0]
            await post_week(guild, channel, next_week, schedule[str(next_week)])
            print(f"Posted Week {next_week} matchups in #{MATCHUPS_CHANNEL} ({guild.name}).")

    @tasks.loop(hours=6)
    async def active_check_loop() -> None:
        """Post an active check if the last one is older than ACTIVE_CHECK_DAYS.

        Checking the last post's age (instead of a naive timer) makes the cadence
        survive restarts/redeploys — no reset, no double-posting.
        """
        for guild in client.guilds:
            channel = discord.utils.get(guild.text_channels, name=ACTIVE_CHECK_CHANNEL)
            if channel is None:
                continue
            last = None
            async for m in channel.history(limit=50):
                if m.author.id == client.user.id and m.content.startswith(ACTIVE_CHECK_MARKER):
                    last = m
                    break
            if last is not None:
                age_days = (discord.utils.utcnow() - last.created_at).total_seconds() / 86400
                if age_days < ACTIVE_CHECK_DAYS:
                    continue

            msg = await channel.send(
                f"{ACTIVE_CHECK_MARKER} — @everyone\n"
                f"Hit {ACTIVE_CHECK_EMOJI} within 48 hours to confirm you're still active.\n"
                "Per rule #6, repeated no-shows risk getting booted from the league.",
                allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
            )
            await msg.add_reaction(ACTIVE_CHECK_EMOJI)
            print(f"Posted active check in #{ACTIVE_CHECK_CHANNEL} ({guild.name}).")

    @client.event
    async def on_ready() -> None:
        print(f"Bot online as {client.user}. Greeting members and tracking teams...")
        for guild in client.guilds:
            await refresh_all(guild)
        if not active_check_loop.is_running():
            active_check_loop.start()
        if not advance_loop.is_running():
            advance_loop.start()

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
        if nick_changed:
            tn_new = resolve_team(after.display_name)
            if tn_new is not None:
                clash = any(
                    m.id != after.id and resolve_team(m.display_name) == tn_new
                    for m in coach_members(after.guild)
                )
                if clash:
                    try:
                        await after.send(
                            f"⚠️ Heads up — **{team_by_norm[tn_new]}** is already taken in "
                            f"{after.guild.name}. No duplicate teams allowed, so please pick a "
                            "different school. Open teams are listed in #teams-available."
                        )
                    except discord.Forbidden:
                        pass  # their DMs are closed; the board still flags the conflict

        await refresh_all(after.guild)

    @client.event
    async def on_user_update(before: discord.User, after: discord.User) -> None:
        # Account-level display-name changes (not server nicknames) fire here.
        if before.name != after.name or getattr(before, "global_name", None) != getattr(after, "global_name", None):
            for guild in client.guilds:
                if guild.get_member(after.id) is not None:
                    await refresh_all(guild)

    @client.event
    async def on_member_remove(member: discord.Member) -> None:
        # Farewell post for departing coaches (frees their team back to the board).
        losers = discord.utils.get(member.guild.text_channels, name=LOSERS_CHANNEL)
        coach_role = discord.utils.get(member.guild.roles, name=ROLE_ON_JOIN) if ROLE_ON_JOIN else None
        was_coach = coach_role is None or coach_role in member.roles
        if losers is not None and not member.bot and was_coach:
            tn = resolve_team(member.display_name)
            if tn is not None:
                msg = random.choice(FAREWELLS_WITH_TEAM).format(name=member.name, team=team_by_norm[tn])
            else:
                msg = random.choice(FAREWELLS_NO_TEAM).format(name=member.name)
            await losers.send(msg, allowed_mentions=discord.AllowedMentions.none())

        await refresh_all(member.guild)

    client.run(token)


if __name__ == "__main__":
    main()
