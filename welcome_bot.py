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
import io
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

try:
    import openai  # optional: enables the AI-generated background for the Dispatch graphic
except ImportError:
    openai = None

try:
    import card  # Pillow compositor: stamps exact matchup data over the background
except Exception:
    card = None

try:
    import dispatch_render  # HTML newspaper template + headless-Chromium render
except Exception:
    dispatch_render = None

import standings  # season W/L tracker (pure stdlib — safe to import unconditionally)

RECAP_MODEL = "claude-opus-5"    # Claude vision model that reads the box-score photos
IMAGE_MODEL = "gpt-image-1"      # OpenAI image model that renders the Dispatch graphic

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
GAME_ROOMS_CATEGORY = "This Week's Games"     # temp per-matchup channels live here; cleared each advance
ADVANCE_HOURS = 48                           # post the next week every 48h (the force-advance cadence)
MATCHUPS_MARKER = "🏈 Week"                    # used to find the last posted week
NEWS_CHANNEL = "league-news"                 # where the weekly recap ("newspaper") posts
SCORES_CHANNEL = "score-reporting"           # read for results to build the recap
RECRUITING_CHANNEL = "recruiting"            # read for recruiting screenshots (commits, portal, rankings)
HEISMAN_CHANNEL = "heisman"                   # read for Heisman-race / stat-leader screenshots
TRASH_CHANNELS = ("general", "user-game-coordination")  # scanned for quotable trash talk
COMMISH_ROLES = ("Commissioner", "Co-Commissioner")  # who may run !advance
QUIET_MODE = True  # QUIET: no pings anywhere (matchups, active checks, league-news @everyone, game rooms).
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


_last_ai_error = ""  # human-readable reason the last AI recap/preseason call didn't produce text


def _is_image(att) -> bool:
    """True if a Discord attachment is an image — by content_type OR filename (content_type can be None)."""
    ct = (att.content_type or "").lower()
    if ct.startswith("image/"):
        return True
    name = (att.filename or "").lower()
    # Only formats Claude vision accepts (png/jpeg/webp/gif) — HEIC/others would be rejected.
    return name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


def _img_media_type(att) -> str:
    ct = (att.content_type or "").lower()
    if ct.startswith("image/"):
        return ct.split(";")[0]
    name = (att.filename or "").lower()
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".gif"):
        return "image/gif"
    return "image/png"  # default; Claude vision accepts png/jpeg/webp/gif


def _img_block(media_type: str, data: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode(),
        },
    }


async def ai_recap(week: int, games: list, images: list, recruit_images: list | None = None,
                   heisman_images: list | None = None, banter: str = "",
                   next_week: int | None = None, next_games: list | None = None) -> tuple:
    """Send photos (+ banter) to Claude; return (recap_markdown | None, results_list)."""
    recruit_images = recruit_images or []
    heisman_images = heisman_images or []
    next_games = next_games or []
    if anthropic is None or not os.environ.get("ANTHROPIC_API_KEY"):
        return None, []
    if not (images or recruit_images or heisman_images or banter):
        return None, []
    content: list = []
    if images:
        content.append({"type": "text", "text": "=== BOX-SCORE PHOTOS (game results) ==="})
        content += [_img_block(mt, d) for mt, d in images]
    if recruit_images:
        content.append({"type": "text", "text": "=== RECRUITING SCREENSHOTS (commits, transfer portal, rankings) ==="})
        content += [_img_block(mt, d) for mt, d in recruit_images]
    if heisman_images:
        content.append({"type": "text", "text": "=== HEISMAN SCREENSHOTS (Heisman race / player stat leaders) ==="})
        content += [_img_block(mt, d) for mt, d in heisman_images]
    matchups = "\n".join(f"- {a} at {h}" for h, a in games) or "(none)"
    banter_block = (
        f"\n\n=== CHAT MESSAGES THIS WEEK (coach: message) — mine these for trash talk ===\n{banter}"
        if banter else ""
    )
    next_block = ""
    if next_games:
        nm = "\n".join(f"- {a} at {h}" for h, a in next_games)
        next_block = f"\n\nNEXT WEEK (Week {next_week}) user-vs-user matchups to preview:\n{nm}"
    content.append({
        "type": "text",
        "text": (
            "You are the beat writer for 'The Dynasty Dispatch', the newspaper of a College Football 27 "
            "online dynasty. Attached above (each under its own labeled header) may be box-score photos, "
            "recruiting screenshots, and Heisman/stat-leader screenshots. A coach's Discord name is usually "
            f"their school.{banter_block}{next_block}\n\nThis week's user-vs-user matchups are:\n{matchups}\n\n"
            f"Write a short, lively recap in Discord markdown starting with '# 📰 The Dynasty Dispatch — Week {week}'.\n"
            "**Game results** — under a '## 🏈 On the Field' header, read each box score, match it to one of the "
            "matchups above, and give one bullet per game: winner in **bold**, the final score, and one sentence of "
            "color using real stats (yards, turnovers, big plays). List any matchup with no readable box score on a "
            "short 'Still to report' line. Only cover the matchups listed above.\n"
            "**Recruiting** — if recruiting screenshots are attached, add a '## 📈 Recruiting Trail' header with "
            "2-5 bullets (school landing a commit with the recruit's name/stars/position, portal moves, class-rank "
            "movement). Bold the school. Omit the section entirely if no recruiting screenshots — never invent it.\n"
            "**Heisman** — if Heisman/stat screenshots are attached, add a '## 🏆 Heisman Watch' header with a short "
            "ranked list of the top contenders (player, team, key stat). Omit if none attached — never invent it.\n"
            "**Trash talk** — if chat messages are provided, add a '## 🗣️ Bulletin Board' header with 2-4 of the "
            "best, punchiest trash-talk quotes, each in quotation marks and attributed to the coach (**— Team**). "
            "Quote real messages only; light smack talk is the vibe, but skip anything genuinely nasty, personal, "
            "or hateful. Omit the section if there's nothing good — don't fabricate quotes.\n"
            "**Key Matchups** — if a NEXT WEEK slate is provided above, END the paper with a '## 🔥 Key Matchups' "
            "header previewing the 2-3 biggest games of that upcoming week (bold both teams, one line of hype each — "
            "lean on marquee programs, rivalries, and how teams looked this week). Only use the next-week games "
            "listed. Omit if no next-week slate was provided.\n"
            "Keep the recap under 1900 characters.\n\n"
            "AFTER the recap, on its own final line output the exact marker <<<RESULTS>>> followed by a JSON "
            "array of the games you could actually read a final score for, each object "
            '{"winner": <team>, "loser": <team>} using the EXACT team names from the matchup list. Omit games '
            "you could not read. This machine-readable block is used for standings and won't be shown to readers."
        ),
    })
    try:
        client = anthropic.AsyncAnthropic()
        msg = await client.messages.create(
            model=RECAP_MODEL, max_tokens=4000, messages=[{"role": "user", "content": content}]
        )
        if msg.stop_reason == "refusal":
            return None, []
        raw = "".join(b.text for b in msg.content if b.type == "text").strip()
        text, results = raw, []
        if "<<<RESULTS>>>" in raw:
            text, _, tail = raw.partition("<<<RESULTS>>>")
            text = text.strip()
            m = re.search(r"\[.*\]", tail, re.S)
            if m:
                try:
                    results = json.loads(m.group(0))
                except Exception:
                    results = []
        return (text or None), results
    except Exception as exc:  # network / auth / rate limit — fall back to the text recap
        print(f"AI recap failed: {exc}")
        return None, []


async def ai_preseason(recruit_images: list, heisman_images: list, banter: str,
                       season: list) -> str | None:
    """Preseason edition — no game results yet. Recruiting/watch/hype + full-season storylines.

    `season` is an ordered list of (week:int, games:list[[home, away]]).
    """
    global _last_ai_error
    if anthropic is None:
        _last_ai_error = "anthropic library not installed on the host (requirements didn't build)"
        print(f"AI preseason skipped: {_last_ai_error}")
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _last_ai_error = "ANTHROPIC_API_KEY not set in the running process"
        print(f"AI preseason skipped: {_last_ai_error}")
        return None
    if not (recruit_images or heisman_images or banter or season):
        return None
    _last_ai_error = ""
    print(f"AI preseason: {len(recruit_images)} recruiting img, {len(heisman_images)} heisman img, "
          f"{len(banter.splitlines())} chat lines, {len(season)} weeks.")
    content: list = []
    if recruit_images:
        content.append({"type": "text", "text": "=== RECRUITING SCREENSHOTS (classes, commits, portal) ==="})
        content += [_img_block(mt, d) for mt, d in recruit_images]
    if heisman_images:
        content.append({"type": "text", "text": "=== PRESEASON WATCH SCREENSHOTS (award watch / rankings) ==="})
        content += [_img_block(mt, d) for mt, d in heisman_images]
    banter_block = (
        f"\n\n=== CHAT MESSAGES (coach: message) — mine for preseason trash talk ===\n{banter}"
        if banter else ""
    )
    season_block = ""
    opener_week = season[0][0] if season else None
    if season:
        wk_lines = [f"Week {wk}: " + "; ".join(f"{a} at {h}" for h, a in games) for wk, games in season]
        season_block = ("\n\nFULL SEASON user-vs-user slate (every week; use ONLY these games and weeks):\n"
                        + "\n".join(wk_lines))
    content.append({
        "type": "text",
        "text": (
            "You are the beat writer for 'The Dynasty Dispatch', the newspaper of a College Football 27 online "
            "dynasty. This is the PRESEASON edition — NO games have been played yet, so do NOT report or invent any "
            "scores or results. A coach's Discord name is usually their school."
            f"{banter_block}{season_block}\n\n"
            "Write a lively preseason edition in Discord markdown starting with "
            "'# 📰 The Dynasty Dispatch — Preseason'.\n"
            "**Season storylines** — this is the centerpiece. Under a '## 🍿 Storylines to Watch' header, scan the "
            "ENTIRE season slate above and call out 4-6 storylines: marquee heavyweight clashes, rivalry games, "
            "revenge angles, brutal gauntlet stretches for a team, and circle-the-calendar matchups. Cite the "
            "specific week for each (e.g. 'Week 13: **Ohio State** vs **Michigan**'). Bold the teams. Use only the "
            "games and weeks listed above — never invent a matchup.\n"
            "**Recruiting** — if recruiting screenshots are attached, add a '## 📈 Recruiting Trail' header with 2-5 "
            "bullets (top classes, notable commits with name/stars/position, portal moves). Bold the school. Omit if "
            "no recruiting screenshots.\n"
            "**Preseason Watch** — if award/ranking screenshots are attached, add a '## 🏆 Preseason Watch' header "
            "with a short ranked list (player, team, position/stat, or ranked teams). Omit if none attached.\n"
            "**Trash talk** — if chat messages are provided, add a '## 🗣️ Bulletin Board' header with 2-4 of the "
            "punchiest quotes, in quotation marks, attributed to the coach (**— Team**). Real messages only; light "
            "smack is the vibe, skip anything genuinely nasty or personal. Omit if nothing good.\n"
            "**Openers** — if a season slate is provided, END with a '## 🔥 Season Openers' header previewing the "
            "2-3 biggest Week " + (str(opener_week) if opener_week is not None else "0") + " games (bold both teams, "
            "one line of hype each). Only use the games listed.\n"
            "Never fabricate a section with no source material. Keep it under 2500 characters."
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
    except Exception as exc:
        _last_ai_error = f"API error: {type(exc).__name__}: {exc}"
        print(f"AI preseason failed: {exc}")
        return None


async def ai_background(week_label: str) -> bytes | None:
    """Generate a TEXTLESS broadcast background (stadium/energy art) via OpenAI, or None."""
    global _last_ai_error
    if openai is None:
        _last_ai_error = "openai library not installed on the host"
        print(f"Dispatch image skipped: {_last_ai_error}")
        return None
    if not os.environ.get("OPENAI_API_KEY"):
        _last_ai_error = "OPENAI_API_KEY not set in the running process"
        print(f"Dispatch image skipped: {_last_ai_error}")
        return None
    prompt = (
        "A dramatic, cinematic college football stadium background for a sports-broadcast graphic: "
        "packed night stadium, moody field lighting, lens flares, deep shadows, energetic atmosphere, "
        "wide 3:2 composition with darker top and bottom edges. IMPORTANT: absolutely NO text, NO "
        "letters, NO numbers, NO logos, NO scoreboards, NO watermarks, and no readable signage — just "
        "the atmospheric background. Photorealistic, high detail."
    )
    try:
        client = openai.AsyncOpenAI()
        res = await client.images.generate(
            model=IMAGE_MODEL, prompt=prompt, size="1536x1024", quality="high", n=1
        )
        return base64.b64decode(res.data[0].b64_json)
    except Exception as exc:
        _last_ai_error = f"image API error: {type(exc).__name__}: {exc}"
        print(f"Dispatch image failed: {exc}")
        return None


GOW_SCHEMA_HINT = (
    '{"away": "<away team>", "home": "<home team>", "subtitle": "<short hook, e.g. \'Top-5 clash in '
    'Columbus\'>", "keys_away": ["k1", "k2", "k3"], "keys_home": ["k1", "k2", "k3"]}'
)


async def ai_gameofweek(week_label: str, games: list, context: str) -> dict | None:
    """Pick the marquee matchup from `games` and write Keys to Win. Returns a dict or None."""
    if anthropic is None or not os.environ.get("ANTHROPIC_API_KEY") or not games:
        return None
    slate = "\n".join(f"- {a} at {h}" for h, a in games)
    prompt = (
        f"You are the editor of The Dynasty Dispatch ({week_label}) for a College Football 27 dynasty. "
        f"From THIS slate of user-vs-user games, pick the single biggest 'Game of the Week':\n{slate}\n\n"
        "Then write three short 'keys to win' for each side (max ~5 words each) and a punchy one-line "
        "subtitle. Base it on the teams' profiles and this recap context if useful:\n"
        f"{context[:1200]}\n\n"
        "Respond with ONLY a JSON object, no prose, exactly this shape:\n" + GOW_SCHEMA_HINT +
        "\nUse the exact team names from the slate."
    )
    try:
        client = anthropic.AsyncAnthropic()
        msg = await client.messages.create(
            model=RECAP_MODEL, max_tokens=1200, messages=[{"role": "user", "content": prompt}]
        )
        raw = "".join(b.text for b in msg.content if b.type == "text")
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as exc:
        print(f"Game-of-week pick failed: {exc}")
        return None


async def make_matchup_graphic(week, games: list, context: str) -> bytes | None:
    """Build the hybrid Game-of-the-Week card: AI background + exact overlay. PNG bytes or None."""
    if card is None or not games:
        return None
    week_label = f"Week {week}" if str(week).strip() not in ("", "Preseason") else "Preseason"
    pick = await ai_gameofweek(week_label, games, context) or {}
    valid = {(h, a) for h, a in games}
    home, away = pick.get("home"), pick.get("away")
    if (home, away) not in valid:  # bad/duplicate pick — headline the first game
        home, away = games[0]
        pick = {}
    around = [(h, a) for h, a in games if (h, a) != (home, away)]
    bg = await ai_background(week_label)  # None -> card falls back to flat team-color panels
    try:
        return card.matchup_card(
            bg, week=week, away=away, home=home,
            away_sub=standings.record_str(away), home_sub=standings.record_str(home),
            subtitle=pick.get("subtitle", ""),
            keys_away=pick.get("keys_away") or [], keys_home=pick.get("keys_home") or [],
            around=around,
        )
    except Exception as exc:
        print(f"Card render failed: {exc}")
        return None


PAPER_SCHEMA = """{
 "headline": "<4-7 word front-page headline>",
 "kicker": "<3-5 word eyebrow above the headline>",
 "dek": "<1-2 sentence standfirst; may bold **teams** with markdown-style ** **>",
 "results": [{"winner": "<team>", "loser": "<team>"}],
 "power_rankings": [{"team": "<team>", "conf": "<SEC|Big Ten|ACC>", "blurb": "<one witty line>"}],
 "storylines": [{"title": "<label>", "body": "<1-2 sentences>"}],
 "cfp_watch": [{"title": "<label>", "body": "<1-2 sentences>"}],
 "top25": [{"rk": 1, "team": "<team>", "rec": "<from standings or 0-0>"}],
 "heisman": [{"name": "<player>", "tag": "<POS · TEAM>", "note": "<short>"}],
 "recruiting": [{"title": "<label>", "body": "<1-2 sentences>"}],
 "line": [{"game": "<Away at Home>", "loc": "<stadium/city>", "odds": "<TEAM -X.X>"}],
 "media_quote": "<one editorial sign-off line>"
}"""


async def ai_paper(week_label: str, matchups: list, next_matchups: list, standings_summary: str,
                   banter: str, images: list, recruit_images: list, heisman_images: list) -> tuple:
    """Build the full newspaper as structured JSON (+ results for standings). (dict|None, results)."""
    global _last_ai_error
    if anthropic is None:
        _last_ai_error = "anthropic library not installed on the host"
        return None, []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _last_ai_error = "ANTHROPIC_API_KEY not set in the running process"
        return None, []
    content: list = []
    if images:
        content.append({"type": "text", "text": "=== BOX-SCORE PHOTOS (real game results — read scores) ==="})
        content += [_img_block(mt, d) for mt, d in images]
    if recruit_images:
        content.append({"type": "text", "text": "=== RECRUITING SCREENSHOTS ==="})
        content += [_img_block(mt, d) for mt, d in recruit_images]
    if heisman_images:
        content.append({"type": "text", "text": "=== HEISMAN / STAT SCREENSHOTS ==="})
        content += [_img_block(mt, d) for mt, d in heisman_images]
    this_slate = "\n".join(f"- {a} at {h}" for h, a in matchups) or "(none)"
    next_slate = "\n".join(f"- {a} at {h}" for h, a in next_matchups) or "(none)"
    content.append({"type": "text", "text": (
        f"You are the editor of THE DYNASTY DISPATCH ({week_label}), a gritty broadsheet for a College "
        "Football 27 online dynasty (20 user-coached teams across the SEC, Big Ten, and ACC). Produce the "
        "front page as STRICT JSON matching this schema (no prose, no code fences):\n" + PAPER_SCHEMA +
        f"\n\nTHIS WEEK's user matchups:\n{this_slate}\n\nNEXT WEEK's user matchups (for The Line):\n{next_slate}"
        f"\n\nCurrent standings (authoritative — use verbatim for records):\n{standings_summary or '(0-0 across the board)'}"
        f"\n\nCoach chat this week (for tone/quotes only):\n{banter[:1500] or '(none)'}\n\n"
        "HARD RULES:\n"
        "• NEVER invent a final score, a win/loss record, a stat, a commit, or a Heisman name. Results come "
        "ONLY from the box-score photos; records come ONLY from the standings above; recruiting and heisman "
        "come ONLY from those screenshots. If a source isn't provided, return an EMPTY list for that section.\n"
        "• 'results' = only games you can actually read a final score for in the photos (winner/loser by exact "
        "team name). Empty list if none.\n"
        "• power_rankings, cfp_watch, storylines, top25 order, and line are EDITORIAL opinion/prediction — allowed, "
        "but they must not state any fabricated final score or record. Rank the 20 user teams; 8-10 power_rankings, "
        "up to 25 top25 (fill 'rec' from standings, else 0-0).\n"
        "• 'line' previews NEXT WEEK's games only, from the slate above.\n"
        "• Keep every blurb tight. Output ONLY the JSON object."
    )})
    try:
        client = anthropic.AsyncAnthropic()
        msg = await client.messages.create(
            model=RECAP_MODEL, max_tokens=6000, messages=[{"role": "user", "content": content}]
        )
        if msg.stop_reason == "refusal":
            _last_ai_error = "model refused"
            return None, []
        raw = "".join(b.text for b in msg.content if b.type == "text")
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            _last_ai_error = "no JSON in paper response"
            return None, []
        data = json.loads(m.group(0))
        _last_ai_error = ""
        return data, data.get("results", []) or []
    except Exception as exc:
        _last_ai_error = f"paper API error: {type(exc).__name__}: {exc}"
        print(f"AI paper failed: {exc}")
        return None, []


def _dek_html(s: str) -> str:
    """Render **bold** markdown to <b> for the dek, escaping the rest."""
    import html as _h
    out, i = [], 0
    for j, part in enumerate(str(s).split("**")):
        out.append(f"<b>{_h.escape(part)}</b>" if j % 2 else _h.escape(part))
    return "".join(out)


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

    def member_for_team(guild: discord.Guild, team: str):
        target = normalize(team)
        for m in guild.members:
            if not m.bot and resolve_team(m.display_name) == target:
                return m
        return None

    def _room_name(week, away: str, home: str) -> str:
        slug = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
        return f"wk{week}-{slug(away)}-at-{slug(home)}"[:95]

    async def clear_game_rooms(guild: discord.Guild) -> None:
        """Delete last week's per-matchup channels (they die at the next advance)."""
        cat = discord.utils.get(guild.categories, name=GAME_ROOMS_CATEGORY)
        if cat is None:
            return
        for ch in list(cat.channels):
            try:
                await ch.delete(reason="Advance — last week's game rooms expire")
            except Exception:
                pass

    async def create_game_rooms(guild: discord.Guild, week, games: list) -> None:
        """Spin up a private channel per user-vs-user matchup for coordination."""
        pairs = [(h, a, member_for_team(guild, h), member_for_team(guild, a)) for h, a in games]
        pairs = [(h, a, hm, am) for h, a, hm, am in pairs if hm is not None and am is not None]
        if not pairs:
            return
        cat = discord.utils.get(guild.categories, name=GAME_ROOMS_CATEGORY)
        if cat is None:
            try:
                cat = await guild.create_category(GAME_ROOMS_CATEGORY)
            except Exception:
                return
        commish = [r for r in guild.roles if r.name in COMMISH_ROLES]
        me = guild.me
        for home, away, hm, am in pairs:
            ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
            if me is not None:
                ow[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
            for r in commish:
                ow[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            for mem in (hm, am):
                ow[mem] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            try:
                ch = await guild.create_text_channel(
                    _room_name(week, away, home), category=cat, overwrites=ow,
                    topic=f"Week {week}: {away} @ {home} — coordinate here. Closes at the next advance.",
                )
                await ch.send(
                    f"🏈 **Week {week}: {away} @ {home}**\n{am.mention} vs {hm.mention} — lock in a time for your "
                    "game here. Report the final score + box score in #score-reporting. "
                    "This room closes when the commish advances.",
                    allowed_mentions=(discord.AllowedMentions.none() if QUIET_MODE
                                      else discord.AllowedMentions(users=True)),
                )
            except Exception as exc:
                print(f"Game room create failed ({away}@{home}): {exc}")

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

    async def build_newspaper(guild: discord.Guild, week: int, since,
                              next_week: int | None = None, next_games: list | None = None) -> str:
        next_games = next_games or []
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
                    if _is_image(att) and len(out) < cap:
                        try:
                            out.append((_img_media_type(att), await att.read()))
                        except Exception:
                            pass
            return out

        images = []
        for m in reports:
            for att in m.attachments:
                if _is_image(att) and len(images) < 12:
                    try:
                        images.append((_img_media_type(att), await att.read()))
                    except Exception:
                        pass
        recruit_images = await collect_images(RECRUITING_CHANNEL, 8)
        heisman_images = await collect_images(HEISMAN_CHANNEL, 6)

        # Scan chat channels for quotable trash talk (text only).
        banter_lines = []
        for cname in TRASH_CHANNELS:
            ch = discord.utils.get(guild.text_channels, name=cname)
            if ch is None:
                continue
            async for m in ch.history(limit=200, after=since):
                if m.author.bot:
                    continue
                txt = " ".join(m.content.split())
                if 3 <= len(txt) <= 300:
                    banter_lines.append(f"{m.author.display_name}: {txt}")
        banter = "\n".join(banter_lines[-80:])

        ai_text, ai_results = await ai_recap(week, games, images, recruit_images, heisman_images,
                                             banter, next_week, next_games)
        if ai_text:
            return ai_text, ai_results
        # Fallback: parse text score lines from #score-reporting.
        done, pending = [], []
        for home, away in games:
            res = next((r for m in reports if (r := parse_result(m.content, home, away))), None)
            (done if res else pending).append((home, away, res))

        lines = [f"# 📰 The Dynasty Dispatch — Week {week}", "*Around the league this week*", ""]
        results = []
        if done:
            for home, away, (hs, aw) in done:
                if hs == aw:
                    lines.append(f"• **{home}** and **{away}** tied {hs}-{aw}")
                else:
                    w, ws, l, ls = (home, hs, away, aw) if hs > aw else (away, aw, home, hs)
                    results.append({"winner": w, "loser": l})
                    margin = ws - ls
                    verb = "edged" if margin <= 3 else "held off" if margin <= 7 else "beat" if margin <= 17 else "rolled"
                    lines.append(f"• **{w}** {verb} {l}, **{ws}-{ls}**")
        else:
            lines.append("*No results reported yet.*")
        if pending:
            lines.append("")
            lines.append("📋 Still to report: " + ", ".join(f"{a} @ {h}" for h, a, _ in pending))
        if next_games:
            lines.append("")
            lines.append(f"## 🔥 Key Matchups — Week {next_week}")
            for home, away in next_games[:3]:
                lines.append(f"• **{away}** at **{home}**")
        return "\n".join(lines), results

    async def build_preseason(guild: discord.Guild) -> str:
        """Preseason paper — scans recent recruiting/heisman/chat (no results) + the opening slate."""
        async def collect_images(channel_name: str, cap: int) -> list:
            ch = discord.utils.get(guild.text_channels, name=channel_name)
            out = []
            if ch is None:
                return out
            async for m in ch.history(limit=200):
                for att in m.attachments:
                    if _is_image(att) and len(out) < cap:
                        try:
                            out.append((_img_media_type(att), await att.read()))
                        except Exception:
                            pass
            return out

        recruit_images = await collect_images(RECRUITING_CHANNEL, 8)
        heisman_images = await collect_images(HEISMAN_CHANNEL, 6)

        banter_lines = []
        for cname in TRASH_CHANNELS:
            ch = discord.utils.get(guild.text_channels, name=cname)
            if ch is None:
                continue
            async for m in ch.history(limit=200):
                if m.author.bot:
                    continue
                txt = " ".join(m.content.split())
                if 3 <= len(txt) <= 300:
                    banter_lines.append(f"{m.author.display_name}: {txt}")
        banter = "\n".join(banter_lines[-80:])

        schedule = load_schedule()
        weeks = sorted(int(w) for w in schedule if w.isdigit())
        season = [(w, schedule[str(w)]) for w in weeks]
        opener_week = weeks[0] if weeks else None
        opener_games = schedule[str(opener_week)] if opener_week is not None else []

        ai = await ai_preseason(recruit_images, heisman_images, banter, season)
        if ai:
            return ai
        # Fallback text edition — full-season slate, no AI.
        lines = ["# 📰 The Dynasty Dispatch — Preseason", "*The season's almost here.*", ""]
        if season:
            lines.append("## 🍿 The Full Slate")
            for wk, games in season:
                lines.append(f"**Week {wk}:** " + ", ".join(f"{a} @ {h}" for h, a in games))
        else:
            lines.append("*Set your rosters — kickoff is coming.*")
        return "\n".join(lines)

    async def send_paper(channel: discord.TextChannel, text: str, ping: bool = False) -> None:
        """Send a paper to a channel, splitting on line breaks to respect Discord's 2000-char limit.

        When ping=True the first chunk @everyone's the league.
        """
        chunks, buf = [], ""
        for line in text.split("\n"):
            if len(buf) + len(line) + 1 > 1990:
                if buf:
                    chunks.append(buf)
                buf = line
            else:
                buf = f"{buf}\n{line}" if buf else line
        if buf:
            chunks.append(buf)
        for i, chunk in enumerate(chunks):
            if ping and i == 0:
                await channel.send("@everyone\n" + chunk,
                                   allowed_mentions=discord.AllowedMentions(everyone=True))
            else:
                await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())

    def user_team_list() -> list:
        teams = set()
        for games in load_schedule().values():
            if isinstance(games, list):
                for pair in games:
                    teams.update(pair)
        return sorted(teams)

    async def gather_media(guild: discord.Guild, since):
        """Collect box-score/recruiting/heisman images + chat banter for the paper."""
        async def imgs(channel_name, cap, use_since):
            ch = discord.utils.get(guild.text_channels, name=channel_name)
            out = []
            if ch is None:
                return out
            # Scan deep so recent posts buried under chatter still get picked up; keep the newest `cap`.
            it = ch.history(limit=600, after=since) if use_since else ch.history(limit=600)
            async for m in it:  # history() yields newest-first when no `after`
                for att in m.attachments:
                    if _is_image(att):
                        try:
                            out.append((_img_media_type(att), await att.read()))
                        except Exception:
                            pass
                if len(out) >= cap:
                    return out[:cap]
            return out[:cap]
        images = await imgs(SCORES_CHANNEL, 16, since is not None)
        recruit = await imgs(RECRUITING_CHANNEL, 12, False)
        heis = await imgs(HEISMAN_CHANNEL, 8, False)
        lines = []
        for cname in TRASH_CHANNELS:
            ch = discord.utils.get(guild.text_channels, name=cname)
            if ch is None:
                continue
            async for m in ch.history(limit=400, after=since):
                if m.author.bot:
                    continue
                txt = " ".join(m.content.split())
                if 3 <= len(txt) <= 300:
                    lines.append(f"{m.author.display_name}: {txt}")
        return images, recruit, heis, "\n".join(lines[-120:])

    async def build_dispatch_image(guild: discord.Guild, week, this_games, next_games, since):
        """Render the full broadcast newspaper PNG. Returns (png|None, data|None, results)."""
        global _last_ai_error
        if dispatch_render is None:
            _last_ai_error = "dispatch_render module failed to import on the host"
            return None, None, []
        images, recruit, heis, banter = await gather_media(guild, since)
        tl = user_team_list()
        summary = "\n".join(f"{t}: {standings.record_str(t) or '0-0'}" for t in tl)
        wl = f"Week {week}" if str(week).strip() not in ("", "Preseason") else "Preseason"
        data, results = await ai_paper(wl, this_games, next_games, summary, banter, images, recruit, heis)
        if not data:
            return None, None, results
        if results and str(week).strip() not in ("", "Preseason"):
            standings.record_week(int(week), results)
        data["week_label"] = wl
        data["dek_html"] = _dek_html(data.get("dek", ""))
        for key in ("storylines", "cfp_watch", "recruiting"):
            for item in (data.get(key) or []):
                if isinstance(item, dict) and item.get("body"):
                    item["body_html"] = _dek_html(item["body"])
        data["standings"] = [{"team": t, "rating": "—", "rec": standings.record_str(t) or "0–0"} for t in tl]
        try:
            png = await dispatch_render.html_to_png(dispatch_render.build_html(data))
        except Exception as exc:
            print(f"Dispatch build_html/render failed: {exc}")
            png = None
        if png is None and not _last_ai_error:
            _last_ai_error = ("paper written OK but image render returned nothing — check HCTI_USER_ID/"
                              "HCTI_API_KEY and the 'Dispatch API render' log line")
        return png, data, results

    def data_to_text(data: dict, week_label: str) -> str:
        """Compact markdown fallback built from the paper data (no extra AI call)."""
        L = [f"# 📰 The Dynasty Dispatch — {week_label}"]
        if data.get("headline"):
            L.append(f"*{data['headline']}*")
        res = data.get("results") or []
        if res:
            L.append("\n## 🏈 On the Field")
            L += [f"• **{r.get('winner')}** def. {r.get('loser')}" for r in res if r.get("winner")]
        for title, key in (("📈 Recruiting Trail", "recruiting"), ("🍿 Storylines", "storylines")):
            items = data.get(key) or []
            if items:
                L.append(f"\n## {title}")
                L += [f"• {i.get('body','')}" for i in items[:4]]
        hz = data.get("heisman") or []
        if hz:
            L.append("\n## 🏆 Heisman Watch")
            L += [f"{n+1}. **{h.get('name')}** — {h.get('tag','')}" for n, h in enumerate(hz[:4])]
        return "\n".join(L)

    async def do_preseason(guild: discord.Guild) -> str:
        news_ch = discord.utils.get(guild.text_channels, name=NEWS_CHANNEL)
        if news_ch is None:
            return "no #league-news channel"
        schedule = load_schedule()
        weeks = sorted(int(w) for w in schedule if w.isdigit())
        opener_games = schedule[str(weeks[0])] if weeks else []
        png, data, _ = await build_dispatch_image(guild, "Preseason", [], opener_games, None)
        if png:
            await news_ch.send(
                content=("📰 **The Dynasty Dispatch — Preseason** is out!" if QUIET_MODE
                         else "@everyone 📰 **The Dynasty Dispatch — Preseason** is out!"),
                file=discord.File(io.BytesIO(png), filename="dispatch-preseason.png"),
                allowed_mentions=(discord.AllowedMentions.none() if QUIET_MODE
                                  else discord.AllowedMentions(everyone=True)),
            )
            return "preseason newspaper posted to #league-news"
        # fallbacks: text paper + matchup card
        paper = await build_preseason(guild)
        await send_paper(news_ch, paper, ping=not QUIET_MODE)
        card_png = await make_matchup_graphic("Preseason", opener_games, paper)
        if card_png:
            await news_ch.send(file=discord.File(io.BytesIO(card_png), filename="dispatch-preseason.png"))
        reason = _last_ai_error or "no material"
        return f"preseason posted (text fallback — newspaper render didn't run: {reason})"

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
            await clear_game_rooms(guild)
            await create_game_rooms(guild, weeks[0], schedule[str(weeks[0])])
            return f"kicked off — posted Week {weeks[0]}"
        if not force and last_time and (discord.utils.utcnow() - last_time).total_seconds() < ADVANCE_HOURS * 3600:
            return "too soon since the last advance"

        upcoming = [w for w in weeks if w > last_week]
        next_week = upcoming[0] if upcoming else None
        next_games = schedule[str(next_week)] if next_week is not None else []

        news_ch = discord.utils.get(guild.text_channels, name=NEWS_CHANNEL)
        if news_ch is not None:
            last_games = schedule.get(str(last_week), [])
            # Primary: the full broadcast newspaper image.
            png, data, _ = await build_dispatch_image(guild, last_week, last_games, next_games, last_time)
            if png:
                await news_ch.send(
                    content=("📰 **The Dynasty Dispatch — Week %d** is out!" % last_week if QUIET_MODE
                             else f"@everyone 📰 **The Dynasty Dispatch — Week {last_week}** is out!"),
                    file=discord.File(io.BytesIO(png), filename=f"dispatch-week-{last_week}.png"),
                    allowed_mentions=(discord.AllowedMentions.none() if QUIET_MODE
                                      else discord.AllowedMentions(everyone=True)),
                )
            else:
                # Fallback: text recap + Game-of-the-Week card.
                paper, results = await build_newspaper(guild, last_week, last_time, next_week, next_games)
                if results:
                    standings.record_week(last_week, results)
                await send_paper(news_ch, paper, ping=not QUIET_MODE)
                if next_games:
                    gcard = await make_matchup_graphic(next_week, next_games, paper)
                    if gcard:
                        await news_ch.send(file=discord.File(io.BytesIO(gcard), filename=f"gotw-week-{next_week}.png"))

        await clear_game_rooms(guild)  # last week's coordination rooms expire
        if not upcoming:
            return f"posted Week {last_week} recap — season complete, no more weeks"
        await post_week(guild, uch, upcoming[0], schedule[str(upcoming[0])])
        await create_game_rooms(guild, upcoming[0], schedule[str(upcoming[0])])
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
        cmd = message.content.strip().lower()
        if cmd not in ("!advance", "!preseason"):
            return
        roles = getattr(message.author, "roles", [])
        if not any(r.name in COMMISH_ROLES for r in roles):
            await message.reply(f"Only commissioners can run `{cmd}`.", mention_author=False)
            return
        async with roster_lock:  # serialize with board refreshes
            if cmd == "!preseason":
                status = await do_preseason(message.guild)
            else:
                status = await do_advance(message.guild, force=True)
        await message.reply(f"⏩ {status}", mention_author=False)

    client.run(token)


if __name__ == "__main__":
    main()
