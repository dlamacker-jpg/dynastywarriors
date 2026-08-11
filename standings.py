#!/usr/bin/env python3
"""
Season standings for The Dynasty Dispatch.

Records are built from the game results the recap reads out of #score-reporting.
Each week is recorded exactly once (idempotent — re-running !advance won't double-count).
Conference sub-records use the alignment in teams_fbs.json.

    import standings
    standings.record_week(3, [{"winner": "Ohio State", "loser": "Alabama"}])
    standings.record_str("Ohio State")   # -> "3-0 (1-0)"
"""

from __future__ import annotations

import json
from pathlib import Path

STANDINGS_FILE = Path(__file__).with_name("standings.json")
TEAMS_FILE = Path(__file__).with_name("teams_fbs.json")

_conf_cache: dict | None = None


def _conf_map() -> dict:
    """team name -> conference, from teams_fbs.json (shape: {conf: [teams]})."""
    global _conf_cache
    if _conf_cache is None:
        _conf_cache = {}
        try:
            data = json.loads(TEAMS_FILE.read_text())
            for conf, teams in data.items():
                if conf.startswith("_"):
                    continue
                for t in teams:
                    _conf_cache[t] = conf
        except Exception:
            _conf_cache = {}
    return _conf_cache


def load() -> dict:
    try:
        d = json.loads(STANDINGS_FILE.read_text())
    except Exception:
        d = {}
    d.setdefault("recorded_weeks", [])
    d.setdefault("teams", {})
    return d


def save(data: dict) -> None:
    STANDINGS_FILE.write_text(json.dumps(data, indent=2))


def _team(data: dict, name: str) -> dict:
    return data["teams"].setdefault(name, {"w": 0, "l": 0, "cw": 0, "cl": 0})


def record_week(week: int, results: list) -> dict:
    """Apply a week's results once. results: [{"winner": str, "loser": str}, ...]."""
    data = load()
    if week in data["recorded_weeks"]:
        return data
    cmap = _conf_map()
    for r in results:
        w, l = r.get("winner"), r.get("loser")
        if not w or not l or w == l:
            continue
        tw, tl = _team(data, w), _team(data, l)
        tw["w"] += 1
        tl["l"] += 1
        if cmap.get(w) and cmap.get(w) == cmap.get(l):
            tw["cw"] += 1
            tl["cl"] += 1
    data["recorded_weeks"].append(week)
    save(data)
    return data


def record_str(team: str) -> str:
    t = load()["teams"].get(team)
    if not t:
        return ""
    s = f'{t["w"]}-{t["l"]}'
    if t["cw"] or t["cl"]:
        s += f' ({t["cw"]}-{t["cl"]})'
    return s
