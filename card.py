#!/usr/bin/env python3
"""
Render broadcast-style matchup graphics (PNG) for The Dynasty Dispatch — HYBRID.

An AI-generated *textless* background (stadium/energy art) is passed in as bytes; this
module composites the exact matchup data on top (team names, records, VS, keys, rail)
with pixel-accurate text and dark scrims for legibility. If no background is supplied it
falls back to flat team-color panels, so it always produces a usable card.

    from card import matchup_card
    png = matchup_card(bg_bytes, week=13, away="Michigan", home="Ohio State",
                       away_sub="10-2 (7-1)", home_sub="11-1 (8-0)",
                       subtitle="Ohio Stadium · Columbus",
                       keys_away=["Run the ball", "Win the trenches"],
                       keys_home=["Protect the QB", "Force turnovers"],
                       around=[("Georgia", "Florida")])
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).with_name("fonts")
COLORS_FILE = Path(__file__).with_name("team_colors.json")
W, H = 1280, 720
_colors_cache: dict | None = None


def _colors() -> dict:
    global _colors_cache
    if _colors_cache is None:
        try:
            data = json.loads(COLORS_FILE.read_text())
            _colors_cache = {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception:
            _colors_cache = {}
    return _colors_cache


def _hex(s: str) -> tuple:
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def _team_color(team: str) -> tuple:
    return _hex(_colors().get(team, {}).get("primary", "#1E2A44"))


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(BASE / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


def _darken(c: tuple, f: float = 0.5) -> tuple:
    return tuple(max(0, int(v * f)) for v in c)


def _fit_font(draw, text, max_w, start, bold=True, floor=22):
    size = start
    while size > floor:
        f = _font(size, bold)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(floor, bold)


def _shadow_text(d, xy, text, font, fill, anchor=None, off=3):
    x, y = xy
    d.text((x + off, y + off), text, font=font, fill=(0, 0, 0), anchor=anchor)
    d.text((x, y), text, font=font, fill=fill, anchor=anchor)


def _cover(bg: Image.Image) -> Image.Image:
    """Resize/crop a background to exactly WxH (cover)."""
    bw, bh = bg.size
    scale = max(W / bw, H / bh)
    bg = bg.resize((int(bw * scale), int(bh * scale)), Image.LANCZOS)
    x = (bg.width - W) // 2
    y = (bg.height - H) // 2
    return bg.crop((x, y, x + W, y + H))


def _vgrad(top_a: int, bot_a: int, y0: int, y1: int) -> Image.Image:
    """A vertical black gradient (alpha top_a->bot_a) spanning rows y0..y1."""
    layer = Image.new("L", (1, H), 0)
    for y in range(H):
        if y < y0:
            a = top_a
        elif y > y1:
            a = bot_a
        else:
            t = (y - y0) / max(1, (y1 - y0))
            a = int(top_a + (bot_a - top_a) * t)
        layer.putpixel((0, y), a)
    return layer.resize((W, H))


def matchup_card(bg_bytes: bytes | None, week, away: str, home: str,
                 away_sub: str = "", home_sub: str = "", label: str = "GAME OF THE WEEK",
                 subtitle: str = "", keys_away=None, keys_home=None, around=None,
                 title: str = "THE DYNASTY DISPATCH") -> bytes:
    keys_away, keys_home, around = keys_away or [], keys_home or [], around or []
    ac, hc = _team_color(away), _team_color(home)

    # ---- base: AI background (cover) or a team-color gradient fallback ----
    base = None
    if bg_bytes:
        try:
            base = _cover(Image.open(io.BytesIO(bg_bytes)).convert("RGB"))
        except Exception:
            base = None
    if base is None:
        base = Image.new("RGB", (W, H))
        px = base.load()
        for x in range(W):
            t = x / W
            c = tuple(int(_darken(ac, 0.9)[i] * (1 - t) + _darken(hc, 0.9)[i] * t) for i in range(3))
            for y in range(H):
                px[x, y] = c

    base = base.convert("RGBA")

    # ---- side team-color tints so identity reads over any photo ----
    tint = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tint)
    td.polygon([(0, 0), (int(W * 0.42), 0), (int(W * 0.34), H), (0, H)], fill=(*ac, 150))
    td.polygon([(W, 0), (int(W * 0.58), 0), (int(W * 0.66), H), (W, H)], fill=(*hc, 150))
    base = Image.alpha_composite(base, tint)

    # ---- dark scrims (top banner + bottom keys area) for legibility ----
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    scrim.putalpha(_vgrad(150, 150, 0, H))          # overall 150/255 darken
    base = Image.alpha_composite(base, scrim)
    band = Image.new("RGBA", (W, H), (5, 6, 10, 255))
    band.putalpha(_vgrad(235, 235, 0, 0))           # near-solid where used via crops below
    # solid top + bottom bars
    barlayer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(barlayer)
    bd.rectangle([0, 0, W, 92], fill=(8, 9, 13, 235))
    bd.rectangle([0, 470, W, H], fill=(8, 9, 13, 205))
    base = Image.alpha_composite(base, barlayer)

    img = base.convert("RGB")
    d = ImageDraw.Draw(img)

    # ---- top banner ----
    _shadow_text(d, (W / 2, 20), title, _font(40), (255, 255, 255), anchor="ma")
    wk = f"WEEK {week}" if str(week).strip() not in ("", "Preseason") else "PRESEASON"
    _shadow_text(d, (W / 2, 64), f"{wk}   ·   {label}", _font(20, False), (180, 186, 200), anchor="ma", off=2)

    # ---- team names + records ----
    fa = _fit_font(d, away.upper(), int(W * 0.42) - 40, 80)
    fh = _fit_font(d, home.upper(), int(W * 0.42) - 40, 80)
    _shadow_text(d, (W * 0.26, 210), away.upper(), fa, (255, 255, 255), anchor="ma")
    _shadow_text(d, (W * 0.74, 210), home.upper(), fh, (255, 255, 255), anchor="ma")
    if away_sub:
        _shadow_text(d, (W * 0.26, 320), away_sub, _font(32), (235, 238, 245), anchor="ma")
    if home_sub:
        _shadow_text(d, (W * 0.74, 320), home_sub, _font(32), (235, 238, 245), anchor="ma")

    # ---- center VS diamond ----
    cx, cy, r = W // 2, 300, 54
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=(8, 9, 13))
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], outline=(235, 200, 90), width=3)
    _shadow_text(d, (cx, cy - 26), "VS", _font(40), (255, 255, 255), anchor="ma")

    if subtitle:
        _shadow_text(d, (W / 2, 428), subtitle.upper(), _font(22, False), (210, 214, 224), anchor="ma", off=2)

    # ---- keys to win ----
    _shadow_text(d, (W / 2, 500), "KEYS TO WIN", _font(24), (235, 200, 90), anchor="ma")
    kf = _font(20, False)
    for i, k in enumerate(keys_away[:3]):
        _shadow_text(d, (60, 548 + i * 32), f"• {k}", kf, (226, 230, 238), off=2)
    for i, k in enumerate(keys_home[:3]):
        _shadow_text(d, (W - 60, 548 + i * 32), f"{k} •", kf, (226, 230, 238), anchor="ra", off=2)

    # ---- around the league footer ----
    if around:
        parts = "    ".join(f"{a} @ {h}" for h, a in around[:4])
        _shadow_text(d, (W / 2, H - 40), "AROUND THE LEAGUE:  " + parts, _font(18, False),
                     (170, 176, 190), anchor="ma", off=2)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


if __name__ == "__main__":  # local smoke test with a synthetic "AI background"
    bg = Image.new("RGB", (1536, 1024), (20, 24, 30))
    bp = bg.load()
    for y in range(1024):
        for x in range(0, 1536, 2):
            v = 20 + int(60 * ((x / 1536) * (y / 1024)))
            bp[x, y] = (v, v + 6, v + 14)
    buf = io.BytesIO(); bg.save(buf, format="PNG")
    png = matchup_card(
        buf.getvalue(), week=13, away="Michigan", home="Ohio State",
        away_sub="10-2 (7-1)", home_sub="11-1 (8-0)", subtitle="Ohio Stadium · Columbus, OH",
        keys_away=["Run the ball effectively", "Win the trenches", "Get pressure"],
        keys_home=["Protect the QB", "Explosive plays", "Limit turnovers"],
        around=[("Georgia", "Florida"), ("Texas A&M", "Texas"), ("Clemson", "South Carolina")],
    )
    Path("sample_card.png").write_bytes(png)
    print("wrote sample_card.png")
