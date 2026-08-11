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
import base64
import json
import os
import random
import re
import sys
import unicodedata
from pathlib import Path

import discord
from discord.ext import tasks

try:
    import anthropic  # optional: enables AI box-score recaps in #league-news
except ImportError:
    anthropic = None

RECAP_MODEL = "claude-opus-5"  # Claude vision model that reads the box-score photos

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
NEWS_CHANNEL = "league-news"                 # where the weekly recap ("newspaper") posts
SCORES_CHANNEL = "score-reporting"           # read for results to build the recap
RECRUITING_CHANNEL = "recruiting"            # read for recruiting screenshots (commits, portal, rankings)
COMMISH_ROLES = ("Commissioner", "Co-Commissioner")  # who may run !advance
QUIET_MODE = False  # LIVE: matchup posts and active checks ping coaches.
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


def _img_block(media_type: str, data: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode(),
        },
    }


async def ai_recap(week: int, games: list, images: list, recruit_images: list | None = None) -> str | None:
    """Send box-score (and recruiting) photos to Claude (vision) for a written recap, or None."""
    recruit_images = recruit_images or []
    if anthropic is None or not os.environ.get("ANTHROPIC_API_KEY") or not (images or recruit_images):
        return None
    content: list = []
    if images:
        content.append({"type": "text", "text": "=== BOX-SCORE PHOTOS (game results) ==="})
        content += [_img_block(mt, d) for mt, d in images]
    if recruit_images:
        content.append({"type": "text", "text": "=== RECRUITING SCREENSHOTS (commits, transfer portal, rankings) ==="})
        content += [_img_block(mt, d) for mt, d in recruit_images]
    matchups = "\n".join(f"- {a} at {h}" for h, a in games) or "(none)"
    content.append({
        "type": "text",
        "text": (
            "You are the beat writer for 'The Dynasty Dispatch', the newspaper of a College Football 27 "
            f"online dynasty. Two kinds of photos may be attached above, each under its own labeled header: "
            "box-score photos (often phone photos of a TV) and recruiting screenshots (commits, transfer "
            f"portal moves, recruiting-class rankings).\n\nThis week's user-vs-user matchups are:\n{matchups}\n\n"
            f"Write a short, lively recap in Discord markdown starting with '# 📰 The Dynasty Dispatch — Week {week}'.\n"
            "**Game results** — under a '## 🏈 On the Field' header, read each box score, match it to one of the "
            "matchups above, and give one bullet per game: winner in **bold**, the final score, and one sentence of "
            "color using real stats (yards, turnovers, big plays). List any matchup with no readable box score on a "
            "short 'Still to report' line. Only cover the matchups listed above.\n"
            "**Recruiting** — if any recruiting screenshots are attached, add a '## 📈 Recruiting Trail' header and "
            "2-5 bullets summarizing what they show (school landing a commit with the recruit's name/stars/position, "
            "portal adds or losses, class-ranking movement). Bold the school. If no recruiting screenshots are "
            "attached, omit this section entirely — do not invent recruiting news.\n"
            "Keep the whole thing under 1900 characters."
        ),
    })
    try:
        client = anthropic.AsyncAnthropic()
        msg = await client.messages.create(
            model=RECAP_MODEL, max_tokens=4000, messages=[{"role": "user", "content": content}]
        )
        if msg.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        return text or None
    except Exception as exc:  # network / auth / rate limit — fall back to the text recap
        print(f"AI recap failed: {exc}")
        return None


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

    raw_aliases = {}
    if ALIASES_FILE.exists():
        with ALIASES_FILE.open(encoding="utf-8") as f:
            raw_aliases = {k: v for k, v in json.load(f).items() if not str(k).startswith("_")}
    # search_terms: team-norm -> lowercase strings to find that team inside freeform score text
    search_terms: dict[str, list[str]] = {}
    for _conf, _teams in conferences.items():
        for _t in _teams:
            search_terms.setdefault(base_norm(_t), []).append(_t.lower())
    for _alias, _official in raw_aliases.items():
        _tn = base_norm(_official)
        if _tn in team_by_norm:
            search_terms.setdefault(_tn, []).append(str(_alias).lower())

    intents = discord.Intents.default()
    intents.members = True           # requires the Server Members Intent (see header)
    intents.message_content = True   # requires the Message Content Intent (for !advance + reading scores)
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
        coach_role = discord.utils.get(guild.roles, name=ROLE_ON_JOIN) if ROLE_ON_JOIN else None
        for m in guild.members:
            if m.bot:
                continue
            # Anyone whose nickname resolves to a team counts — the Coach role is NOT required.
            tn = resolve_team(m.display_name)
            if tn is not None:
                holders.setdefault(tn, []).append((team_by_norm[tn], m.mention))
            elif coach_role is not None and coach_role in m.roles:
                unset.append(m.mention)  # has the Coach role but hasn't set a team nickname
        for team, owner in reserved.items():
            if base_norm(team) not in holders:  # a real member's nickname wins over the reserved entry
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

    def team_tag(guild: discord.Guild, team: str) -> str:
        """Display for a team in a matchup: coach @mention, else reserved owner, else bold name."""
        target = normalize(team)
        for m in guild.members:  # 1. any member whose nickname is this team (role not required)
            if not m.bot and resolve_team(m.display_name) == target:
                return m.mention
        for rteam, owner in reserved.items():  # 2. a reserved owner for this team
            if normalize(rteam) == target:
                return owner_to_display(guild, owner)
        return f"**{team}**"  # 3. nobody claims it yet

    async def post_week(guild: discord.Guild, channel: discord.TextChannel, week: int, games: list) -> None:
        lines = [
            f"{MATCHUPS_MARKER} {week} — User Games 🏈",
            f"Get your games in within {ADVANCE_HOURS} hours. @ your opponent to schedule, "
            "report in #score-reporting, and 👍 the active check.",
            "",
        ]
        for game in games:
            home, away = game[0], game[1]
            lines.append(f"• {team_tag(guild, away)} ({away})  @  {team_tag(guild, home)} ({home})")
        if len(games) == 0:
            lines.append("*No user-vs-user games this week.*")
        allowed = (
            discord.AllowedMentions.none() if QUIET_MODE
            else discord.AllowedMentions(users=True, roles=True)
        )
        await channel.send("\n".join(lines), allowed_mentions=allowed)

    def find_team_pos(text_lower: str, team: str):
        """Character index where `team` (name or alias) first appears in the text, or None."""
        best = None
        for term in search_terms.get(normalize(team), [team.lower()]):
            i = text_lower.find(term)
            if i >= 0 and (best is None or i < best):
                best = i
        return best

    def parse_result(text: str, home: str, away: str):
        """Best-effort (home_score, away_score) from a freeform score message, else None."""
        low = text.lower()
        hp, ap = find_team_pos(low, home), find_team_pos(low, away)
        if hp is None or ap is None:
            return None
        nums = [(mt.start(), int(mt.group())) for mt in re.finditer(r"\d{1,3}", text)]
        if len(nums) < 2:
            return None

        def score_for(pos: int) -> int:
            after = [n for n in nums if n[0] > pos]  # a team's score follows its name
            pool = after or nums
            return min(pool, key=lambda n: abs(n[0] - pos))[1]

        return score_for(hp), score_for(ap)

    async def build_newspaper(guild: discord.Guild, week: int, since) -> str:
        scores_ch = discord.utils.get(guild.text_channels, name=SCORES_CHANNEL)
        reports = []
        if scores_ch is not None:
            reports = [m async for m in scores_ch.history(limit=300, after=since)]
        games = load_schedule().get(str(week), [])

        # Preferred path: let Claude read the box-score photos and write the recap.
        async def collect_images(channel_name: str, cap: int) -> list:
            ch = discord.utils.get(guild.text_channels, name=channel_name)
            out = []
            if ch is None:
                return out
            async for m in ch.history(limit=300, after=since):
                for att in m.attachments:
                    if att.content_type and att.content_type.startswith("image/") and len(out) < cap:
                        try:
                            out.append((att.content_type, await att.read()))
                        except Exception:
                            pass
            return out

        images = []
        for m in reports:
            for att in m.attachments:
                if att.content_type and att.content_type.startswith("image/") and len(images) < 12:
                    try:
                        images.append((att.content_type, await att.read()))
                    except Exception:
                        pass
        recruit_images = await collect_images(RECRUITING_CHANNEL, 8)
        ai = await ai_recap(week, games, images, recruit_images)
        if ai:
            return ai
        # Fallback: parse text score lines from #score-reporting.
        done, pending = [], []
        for home, away in games:
            res = next((r for m in reports if (r := parse_result(m.content, home, away))), None)
            (done if res else pending).append((home, away, res))

        lines = [f"# 📰 The Dynasty Dispatch — Week {week}", "*Around the league this week*", ""]
        if done:
            for home, away, (hs, aw) in done:
                if hs == aw:
                    lines.append(f"• **{home}** and **{away}** tied {hs}-{aw}")
                else:
                    w, ws, l, ls = (home, hs, away, aw) if hs > aw else (away, aw, home, hs)
                    margin = ws - ls
                    verb = "edged" if margin <= 3 else "held off" if margin <= 7 else "beat" if margin <= 17 else "rolled"
                    lines.append(f"• **{w}** {verb} {l}, **{ws}-{ls}**")
        else:
            lines.append("*No results reported yet.*")
        if pending:
            lines.append("")
            lines.append("📋 Still to report: " + ", ".join(f"{a} @ {h}" for h, a, _ in pending))
        return "\n".join(lines)

    async def do_advance(guild: discord.Guild, force: bool) -> str:
        """Post last week's recap (from scores) + the next week's matchups. Returns a status line."""
        uch = discord.utils.get(guild.text_channels, name=MATCHUPS_CHANNEL)
        if uch is None:
            return "no #user-game-coordination channel"
        schedule = load_schedule()
        weeks = sorted(int(w) for w in schedule if w.isdigit())
        if not weeks:
            return "schedule is empty — nothing to advance"

        last_week, last_time = None, None
        async for m in uch.history(limit=100):
            if m.author.id == client.user.id and m.content.startswith(MATCHUPS_MARKER):
                mt = re.match(rf"{re.escape(MATCHUPS_MARKER)} (\d+)", m.content)
                if mt:
                    last_week, last_time = int(mt.group(1)), m.created_at
                    break

        if last_week is None:  # season kickoff — no prior week to recap
            await post_week(guild, uch, weeks[0], schedule[str(weeks[0])])
            return f"kicked off — posted Week {weeks[0]}"
        if not force and last_time and (discord.utils.utcnow() - last_time).total_seconds() < ADVANCE_HOURS * 3600:
            return "too soon since the last advance"

        news_ch = discord.utils.get(guild.text_channels, name=NEWS_CHANNEL)
        if news_ch is not None:
            paper = await build_newspaper(guild, last_week, last_time)
            await news_ch.send(paper, allowed_mentions=discord.AllowedMentions.none())

        upcoming = [w for w in weeks if w > last_week]
        if not upcoming:
            return f"posted Week {last_week} recap — season complete, no more weeks"
        await post_week(guild, uch, upcoming[0], schedule[str(upcoming[0])])
        return f"Week {last_week} recap posted + Week {upcoming[0]} matchups are up"

    @tasks.loop(hours=3)
    async def advance_loop() -> None:
        for guild in client.guilds:
            await do_advance(guild, force=False)

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
                allowed_mentions=(
                    discord.AllowedMentions.none() if QUIET_MODE
                    else discord.AllowedMentions(everyone=True, roles=True)
                ),
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

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.content.strip().lower() != "!advance":
            return
        roles = getattr(message.author, "roles", [])
        if not any(r.name in COMMISH_ROLES for r in roles):
            await message.reply("Only commissioners can run `!advance`.", mention_author=False)
            return
        async with roster_lock:  # serialize with board refreshes
            status = await do_advance(message.guild, force=True)
        await message.reply(f"⏩ {status}", mention_author=False)

    client.run(token)


if __name__ == "__main__":
    main()
