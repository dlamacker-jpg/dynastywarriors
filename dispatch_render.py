#!/usr/bin/env python3
"""
Render The Dynasty Dispatch front page (broadcast-style newspaper) to a PNG.

Two stages:
  1. build_html(data)  -> fills the approved template with real+editorial data.
  2. html_to_png(html) -> renders it with headless Chromium (Playwright), async.

Grounding rule: this module only LAYS OUT what it's given. Scores/records/recruiting
must arrive already-verified from the caller; empty sections are omitted, never faked.
"""

from __future__ import annotations

import html as _html


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


# ---------------------------------------------------------------- section builders
def _power(items) -> str:
    rows = "".join(
        f'<div class="pr"><span class="n">{i+1}</span><span class="sh"></span>'
        f'<span class="body"><span class="tm">{esc(x.get("team"))} <b>{esc(x.get("conf",""))}</b></span>'
        f'<span class="blurb">{esc(x.get("blurb",""))}</span></span></div>'
        for i, x in enumerate(items)
    )
    return _panel("Dispatch Power Rankings", rows) if rows else ""


def _notes(title, items) -> str:
    rows = "".join(
        f'<div class="note"><div class="kick">{esc(x.get("title",""))}</div>'
        f'<p>{x.get("body_html") or esc(x.get("body",""))}</p></div>'
        for x in items
    )
    return _panel(title, rows) if rows else ""


def _poll(items) -> str:
    if not items:
        return ""
    rows = "".join(
        f'<div class="pl"><span class="rk">{esc(x.get("rk"))}.</span>'
        f'<span class="tm">{esc(x.get("team"))}</span>'
        f'<span class="rec">{esc(x.get("rec","0–0"))}</span></div>'
        for x in items
    )
    return _panel("CFB 27 Top 25 — Poll", f'<div class="poll">{rows}</div>', pad=True)


def _heisman(items) -> str:
    rows = "".join(
        f'<div class="hz"><span class="n">{i+1}</span>'
        f'<span class="nm">{esc(x.get("name"))} <b>{esc(x.get("tag",""))}</b></span>'
        f'<span class="note2">{esc(x.get("note",""))}</span></div>'
        for i, x in enumerate(items)
    )
    return _panel("Heisman Watch — Official Board", rows) if rows else ""


def _standings(items) -> str:
    if not items:
        return ""
    half = (len(items) + 1) // 2
    def col(rows):
        return "".join(
            f'<div class="st"><span class="t">{esc(x.get("team"))}</span>'
            f'<span class="rt">{esc(x.get("rating","—"))}</span>'
            f'<span class="wl">{esc(x.get("rec","0–0"))}</span></div>' for x in rows
        )
    inner = (f'<div class="stand"><div>{col(items[:half])}</div><div>{col(items[half:])}</div></div>'
             '<p class="foot-mini">User-vs-user records, built from reported box scores.</p>')
    return _panel("User Standings", inner, pad=True)


def _line(items) -> str:
    rows = "".join(
        f'<div class="ln"><div><div class="g">{esc(x.get("game"))}</div>'
        f'<div class="loc">{esc(x.get("loc",""))}</div></div>'
        f'<div class="od">{esc(x.get("odds",""))}</div></div>'
        for x in items
    )
    return _panel("The Dispatch Line", rows) if rows else ""


def _slate(items) -> str:
    rows = "".join(
        f'<div class="cg"><span class="t">{esc(x.get("team"))}</span>'
        f'<span class="op">{esc(x.get("opp",""))}</span>'
        f'<span class="day">{esc(x.get("day",""))}</span></div>'
        for x in items
    )
    return _panel("Around the League — CPU Slate", f'<div class="slate">{rows}</div>', pad=True) if rows else ""


def _media(quote) -> str:
    if not quote:
        return ""
    inner = (f'<p class="mq"><span class="qm">“</span>{esc(quote)}'
             '<cite>— The Dynasty Dispatch</cite></p>')
    return _panel("From the Media Desk", inner)


def _num(v):
    return int(v) if isinstance(v, (int, float)) else None


def _final_scores(items) -> str:
    if not items:
        return ""
    boxes = []
    for x in items[:6]:
        a, h = esc(x.get("away")), esc(x.get("home"))
        asc, hsc = _num(x.get("away_score")), _num(x.get("home_score"))
        have = asc is not None and hsc is not None
        aw = "win" if have and asc > hsc else ""
        hw = "win" if have and hsc > asc else ""
        sa = str(asc) if have else "—"
        sh = str(hsc) if have else "—"
        boxes.append(
            f'<div class="sbx"><div class="sbx-v">{esc(x.get("venue",""))}</div>'
            f'<div class="sbx-r {aw}"><span>{a}</span><b>{sa}</b></div>'
            f'<div class="sbx-r {hw}"><span>{h}</span><b>{sh}</b></div>'
            f'<div class="sbx-b">{esc(x.get("blurb",""))}</div></div>'
        )
    return _panel("Final Scores", f'<div class="sgrid">{"".join(boxes)}</div>', pad=True)


def _marquee(items) -> str:
    if not items:
        return ""
    cards = []
    for x in items[:3]:
        watch = (f'<div class="mq-watch"><b>Watch For:</b> {esc(x.get("watch_for"))}</div>'
                 if x.get("watch_for") else "")
        cards.append(
            f'<div class="mq-card"><div class="mq-top"><span class="mq-label">{esc(x.get("label","Matchup"))}</span>'
            f'<span class="mq-venue">{esc(x.get("venue",""))}</span></div>'
            f'<div class="mq-teams">{esc(x.get("matchup",""))}</div>'
            f'<div class="mq-write">{esc(x.get("writeup",""))}</div>{watch}</div>'
        )
    return _panel("This Week's Marquee Matchups", f'<div class="mgrid">{"".join(cards)}</div>', pad=True)


def _overheard(items) -> str:
    if not items:
        return ""
    rows = "".join(
        f'<div class="oh"><span class="q">“{esc(x.get("quote"))}”</span>'
        f'<cite>— {esc(x.get("who"))}</cite></div>' for x in items[:5]
    )
    return _panel("Overheard in the League", rows)


def _commish(text) -> str:
    if not text:
        return ""
    return _panel("Commissioner's Corner", f'<p class="cmq">“{esc(text)}”</p>')


def _scoreboard(x) -> str:
    """Quarter-by-quarter hero for the single biggest game. Rendered only if a real line exists."""
    if not isinstance(x, dict) or not (x.get("away") and x.get("home")):
        return ""
    al, hl = x.get("away_line") or [], x.get("home_line") or []
    n = max(len(al), len(hl))
    if n == 0:
        return ""
    at, ht = _num(x.get("away_total")), _num(x.get("home_total"))
    aw = at is not None and ht is not None and at > ht
    hw = at is not None and ht is not None and ht > at
    cols = "".join(f"<th>{i+1}</th>" for i in range(n)) + "<th>T</th>"

    def row(team, line, tot, win):
        padded = (list(line) + [None] * n)[:n]
        cells = "".join(f"<td>{_num(v) if _num(v) is not None else '—'}</td>" for v in padded)
        return (f'<tr class="{"win" if win else ""}"><td class="tm">{esc(team)}</td>{cells}'
                f'<td class="tot">{tot if tot is not None else "—"}</td></tr>')

    note = f'<div class="sb-note">{esc(x.get("note",""))}</div>' if x.get("note") else ""
    return (f'<div class="scoreboard"><div class="sb-head">★ {esc(x.get("venue",""))} · FINAL ★</div>'
            f'<table class="sbt"><tr class="sb-cols"><th></th>{cols}</tr>'
            f'{row(x.get("away"), al, at, aw)}{row(x.get("home"), hl, ht, hw)}</table>{note}</div>')


def _panel(title, inner, pad=False) -> str:
    body = inner if pad else inner
    return (f'<section class="panel"><div class="shead"><h2>{esc(title)}</h2></div>'
            f'<div class="pbody">{body}</div></section>')


# ---------------------------------------------------------------- page shell
def build_html(data: dict) -> str:
    d = data or {}
    top = "".join(filter(None, [
        _scoreboard(d.get("scoreboard")),
        _final_scores(d.get("final_scores", [])),
        _marquee(d.get("marquee", [])),
    ]))
    # Balance the columns: recruiting rides the left so it doesn't leave a void bottom-right.
    left = "".join(filter(None, [
        _power(d.get("power_rankings", [])),
        _notes("Storyline Corner", d.get("storylines", [])),
        _notes("Recruiting Trail", d.get("recruiting", [])),
        _standings(d.get("standings", [])),
        _overheard(d.get("overheard", [])),
    ]))
    right = "".join(filter(None, [
        _notes("CFP Contender Watch", d.get("cfp_watch", [])),
        _poll(d.get("top25", [])),
        _heisman(d.get("heisman", [])),
        _line(d.get("line", [])),
        _commish(d.get("commissioner", "")),
        _media(d.get("media_quote", "")),
    ]))
    return _SHELL.format(
        top=top,
        title=esc(d.get("title", "The Dynasty Dispatch")),
        issue=esc(d.get("issue", "Volume 1 · Issue 1")),
        date=esc(d.get("date", "")),
        week=esc(d.get("week_label", "Week 1")).upper(),
        eyebrow_week=esc(d.get("week_label", "Week 1")),
        headline=esc(d.get("headline", "The Dynasty Dispatch")),
        kicker=esc(d.get("kicker", "This Week in the League")),
        dek=d.get("dek_html") or esc(d.get("dek", "")),
        left=left, right=right,
    )


async def html_to_png(html_str: str, width: int = 1060) -> bytes | None:
    """Render HTML to PNG. Tries a hosted HTML→image API first, then local Playwright."""
    png = await _render_via_api(html_str, width)
    if png is not None:
        return png
    return await _render_via_playwright(html_str, width)


async def _render_via_api(html_str: str, width: int) -> bytes | None:
    """htmlcsstoimage.com (hcti.io): reliable on small hosts. Needs HCTI_USER_ID + HCTI_API_KEY."""
    import os
    uid, key = os.environ.get("HCTI_USER_ID"), os.environ.get("HCTI_API_KEY")
    if not (uid and key):
        return None
    try:
        import aiohttp
        auth = aiohttp.BasicAuth(uid, key)
        payload = {"html": html_str, "device_scale": 2,
                   "viewport_width": width + 40, "viewport_height": 2200, "selector": ".wrap",
                   "google_fonts": "Anton|Oswald|Kaushan Script|Playfair Display", "ms_delay": 500}
        async with aiohttp.ClientSession() as s:
            async with s.post("https://hcti.io/v1/image", data=payload, auth=auth) as r:
                if r.status not in (200, 201):
                    print(f"Dispatch API render failed: HTTP {r.status} {await r.text()}")
                    return None
                url = (await r.json()).get("url")
            if not url:
                return None
            async with s.get(url) as img:
                return await img.read() if img.status == 200 else None
    except Exception as exc:
        print(f"Dispatch API render error: {exc}")
        return None


async def _render_via_playwright(html_str: str, width: int) -> bytes | None:
    try:
        from playwright.async_api import async_playwright
    except Exception:
        print("Dispatch render skipped: no HCTI creds and playwright not installed")
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page(viewport={"width": width + 40, "height": 1400}, device_scale_factor=2)
            await page.set_content(html_str, wait_until="networkidle")
            el = await page.query_selector(".wrap")
            png = await (el.screenshot(type="png") if el else page.screenshot(full_page=True, type="png"))
            await browser.close()
            return png
    except Exception as exc:
        print(f"Dispatch Playwright render failed: {exc}")
        return None


# The CSS is the approved Hedge-Herald-style sheet; {left}/{right} receive rendered panels.
_SHELL = r"""<title>{title}</title>
<style>
  :root{{
    --panel:#170f0e; --panel2:#1e1412; --oxblood:#3a1416; --oxblood2:#280d0e;
    --edge:#5a2321; --edge-soft:rgba(230,194,92,.20); --crimson:#b3121a; --crimson-br:#e12a1e;
    --gold:#e6c25c; --gold-dim:#b6923f; --cream:#efe6d4; --muted:#a89584; --muted-dim:#79685b;
    --display:'Anton','Impact','Haettenschweiler','Arial Narrow Bold',sans-serif;
    --cond:'Oswald','Arial Narrow','Roboto Condensed',system-ui,sans-serif;
    --brush:'Kaushan Script','Brush Script MT','Segoe Script',cursive;
    --serif:'Playfair Display','Iowan Old Style','Palatino Linotype','Georgia',serif;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0}}
  .wrap{{max-width:1060px; margin:0 auto; padding:18px; color:var(--cream); font-family:var(--cond);
    background:radial-gradient(120% 60% at 50% -8%, rgba(179,18,26,.22), transparent 62%),
      radial-gradient(150% 130% at 50% 118%, rgba(0,0,0,.75), transparent 55%),
      linear-gradient(180deg,#110c0b,#0a0706);
    position:relative; box-shadow:0 30px 90px rgba(0,0,0,.6); border:1px solid #2a1a17}}
  .wrap::before{{content:""; position:absolute; inset:0; pointer-events:none; z-index:2;
    background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='150' height='150'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/></svg>");
    mix-blend-mode:overlay; opacity:.09}}
  .wrap>*{{position:relative; z-index:3}}
  .eyebrow{{position:absolute; top:-2px; right:0; font-size:11px; letter-spacing:.22em; text-transform:uppercase; color:var(--muted)}}
  .eyebrow b{{color:var(--gold)}}
  .mast{{display:grid; grid-template-columns:auto 1fr auto; align-items:center; gap:16px}}
  .crest{{width:96px; height:104px}} .crest svg{{width:100%; height:100%; filter:drop-shadow(0 4px 6px rgba(0,0,0,.6))}}
  .title{{padding-top:14px}}
  .title .wm{{font-family:var(--display); font-size:66px; line-height:.82; transform-origin:left;
    text-transform:uppercase; color:var(--cream); text-shadow:2px 3px 0 rgba(0,0,0,.5)}}
  .title .wm em{{font-family:var(--brush); font-style:normal; color:var(--crimson-br); text-transform:none; font-size:78px; margin-left:6px}}
  .title .sub{{font-size:12px; letter-spacing:.34em; text-transform:uppercase; color:var(--gold-dim); margin-top:12px}}
  .issue{{border:1px solid var(--edge); background:linear-gradient(180deg,var(--oxblood),var(--oxblood2)); padding:8px 12px; text-align:center; min-width:180px}}
  .issue .v{{font-family:var(--display); font-size:16px; color:var(--cream); transform:scaleY(1.08); display:block}}
  .issue .stars{{color:var(--gold); letter-spacing:.15em; font-size:12px}}
  .issue .d{{font-size:12px; letter-spacing:.16em; color:var(--muted); margin-top:5px; border-top:1px solid var(--edge-soft); padding-top:5px; text-transform:uppercase}}
  .issue .wk{{font-family:var(--display); font-size:30px; color:var(--gold); transform:scaleY(1.1); margin-top:2px}}
  .rule-heavy{{height:5px; margin-top:10px; background:linear-gradient(90deg,#6d0d10,var(--crimson-br) 35%,var(--crimson) 65%,#6d0d10); box-shadow:0 1px 0 rgba(230,194,92,.3)}}
  .rule-thin{{height:1px; background:var(--edge-soft); margin-top:3px}}
  .banner{{margin:14px 0 2px}}
  .banner .kicker{{font-family:var(--cond); letter-spacing:.32em; text-transform:uppercase; font-size:12px; color:var(--gold); text-align:center; margin-bottom:4px}}
  .banner h1{{font-family:var(--display); text-transform:uppercase; font-size:clamp(34px,6vw,64px); line-height:.9; text-align:center; margin:.1em 0;
    background:linear-gradient(180deg,#f6efdd,#d9b45f 65%,#a9832f); -webkit-background-clip:text; background-clip:text; color:transparent; text-wrap:balance}}
  .banner .dek{{font-family:var(--serif); font-style:italic; text-align:center; color:var(--muted); font-size:15px; max-width:74ch; margin:2px auto 0}}
  .banner .dek b{{color:var(--cream); font-style:normal; font-weight:600}}
  .fullwide{{display:flex; flex-direction:column; gap:16px; margin-top:14px}}
  .scoreboard{{border:1px solid var(--edge); background:linear-gradient(180deg,#241211,#130909); padding:12px 18px; box-shadow:inset 0 0 0 1px rgba(0,0,0,.4)}}
  .sb-head{{text-align:center; font-family:var(--display); font-style:italic; color:var(--gold); letter-spacing:.06em; font-size:18px; margin-bottom:8px}}
  .sbt{{width:100%; border-collapse:collapse}}
  .sbt th{{font-family:var(--cond); color:var(--gold-dim); font-size:12px; font-weight:normal; text-align:center; padding:3px 10px; border-bottom:1px solid var(--edge-soft)}}
  .sbt td{{text-align:center; font-family:var(--cond); color:var(--muted); font-variant-numeric:tabular-nums; font-size:20px; padding:6px 10px}}
  .sbt td.tm{{text-align:left; font-family:var(--display); font-style:italic; color:var(--muted); font-size:22px}}
  .sbt tr.win td{{color:var(--cream)}} .sbt tr.win td.tm{{color:var(--cream)}}
  .sbt td.tot{{font-family:var(--display); color:var(--gold); font-size:30px}}
  .sb-note{{text-align:center; font-family:var(--serif); font-style:italic; color:var(--muted); font-size:12px; margin-top:8px; border-top:1px solid var(--edge-soft); padding-top:8px}}
  .sgrid{{display:grid; grid-template-columns:repeat(3,1fr); gap:12px}}
  .sbx{{border:1px solid var(--edge); background:linear-gradient(180deg,#241211,#180d0c); padding:8px 11px}}
  .sbx-v{{font-family:var(--cond); text-transform:uppercase; letter-spacing:.14em; font-size:10px; color:var(--gold-dim); margin-bottom:4px}}
  .sbx-r{{display:flex; justify-content:space-between; align-items:baseline; font-family:var(--display); font-style:italic; color:var(--muted); font-size:17px; padding:2px 0}}
  .sbx-r.win{{color:var(--cream)}}
  .sbx-r b{{color:var(--gold); font-variant-numeric:tabular-nums; font-family:var(--cond); font-size:18px}}
  .sbx-b{{font-family:var(--serif); font-style:italic; color:var(--muted); font-size:11.5px; line-height:1.35; margin-top:5px; border-top:1px solid var(--edge-soft); padding-top:5px}}
  .mgrid{{display:grid; grid-template-columns:repeat(3,1fr); gap:12px}}
  .mq-card{{border:1px solid var(--edge); background:linear-gradient(180deg,#241211,#160c0b); padding:10px 12px; display:flex; flex-direction:column; gap:6px}}
  .mq-top{{display:flex; justify-content:space-between; align-items:center; font-family:var(--cond); text-transform:uppercase; font-size:10px; letter-spacing:.14em}}
  .mq-label{{color:var(--crimson-br)}} .mq-venue{{color:var(--gold-dim)}}
  .mq-teams{{font-family:var(--display); font-style:italic; color:var(--cream); font-size:20px; text-align:center; line-height:1.05}}
  .mq-write{{font-family:var(--serif); font-style:italic; color:var(--muted); font-size:12px; line-height:1.4}}
  .mq-watch{{font-family:var(--cond); color:var(--cream); font-size:12px; border-top:1px solid var(--edge-soft); padding-top:6px}}
  .mq-watch b{{color:var(--gold); text-transform:uppercase; letter-spacing:.1em; font-size:10px}}
  .oh{{padding:7px 0; border-bottom:1px solid rgba(230,194,92,.12)}} .oh:last-child{{border-bottom:0}}
  .oh .q{{font-family:var(--serif); font-style:italic; color:var(--cream); font-size:13.5px; line-height:1.4}}
  .oh cite{{display:block; font-style:normal; font-family:var(--cond); text-transform:uppercase; letter-spacing:.12em; font-size:10px; color:var(--crimson-br); margin-top:3px}}
  .cmq{{font-family:var(--serif); font-style:italic; color:var(--cream); font-size:15px; line-height:1.5; margin:0}}
  .cols{{display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:14px; align-items:start}}
  .stack{{display:flex; flex-direction:column; gap:16px}}
  .panel{{border:1px solid var(--edge); background:linear-gradient(180deg,var(--panel2),var(--panel)); box-shadow:inset 0 0 0 1px rgba(0,0,0,.4), 0 8px 22px rgba(0,0,0,.35)}}
  .shead{{text-align:center; padding:8px 10px; background:linear-gradient(90deg,var(--oxblood2),var(--oxblood) 50%,var(--oxblood2)); border-bottom:1px solid var(--edge)}}
  .shead h2{{font-family:var(--display); font-style:italic; transform:scaleY(1.1); font-size:20px; letter-spacing:.05em; color:var(--gold); display:inline-flex; align-items:center; gap:12px; margin:0}}
  .shead h2::before,.shead h2::after{{content:"★"; color:var(--crimson-br); font-size:12px}}
  .pbody{{padding:11px 13px}}
  .pr{{display:grid; grid-template-columns:30px 30px 1fr; align-items:center; gap:10px; padding:7px 0; border-bottom:1px solid rgba(230,194,92,.12)}}
  .pr:last-child{{border-bottom:0}}
  .pr .n{{font-family:var(--display); font-size:28px; color:var(--crimson-br); text-align:center}}
  .pr .body .tm{{font-family:var(--display); font-style:italic; transform:scaleY(1.05); transform-origin:left; font-size:17px; color:var(--cream)}}
  .pr .body .tm b{{color:var(--gold); font-style:normal; font-family:var(--cond); font-size:11px; letter-spacing:.08em; margin-left:6px}}
  .pr .body .blurb{{font-family:var(--serif); font-style:italic; color:var(--muted); font-size:12.5px; line-height:1.35}}
  .poll{{display:grid; grid-template-columns:1fr 1fr; gap:2px 22px}}
  .poll .pl{{display:flex; align-items:baseline; gap:7px; padding:4px 0; border-bottom:1px dotted rgba(230,194,92,.14)}}
  .poll .pl .rk{{color:var(--gold-dim); width:22px; text-align:right; font-variant-numeric:tabular-nums}}
  .poll .pl .tm{{font-family:var(--display); font-style:italic; color:var(--cream); font-size:14px}}
  .poll .pl .rec{{margin-left:auto; color:var(--muted); font-variant-numeric:tabular-nums; font-size:12px}}
  .note{{padding:8px 0; border-bottom:1px solid rgba(230,194,92,.12)}} .note:last-child{{border-bottom:0}}
  .note h3,.kick{{font-family:var(--cond); text-transform:uppercase; letter-spacing:.18em; font-size:11px; color:var(--crimson-br); margin:2px 0 4px}}
  .note p{{font-family:var(--serif); font-size:13px; line-height:1.5; color:var(--muted); margin:0}}
  .note p b{{color:var(--cream); font-weight:600}}
  .hz{{display:grid; grid-template-columns:26px 1fr auto; align-items:center; gap:10px; padding:7px 0; border-bottom:1px solid rgba(230,194,92,.12)}}
  .hz:last-child{{border-bottom:0}}
  .hz .n{{font-family:var(--display); font-size:22px; color:var(--crimson-br); text-align:center}}
  .hz .nm{{font-family:var(--display); font-style:italic; font-size:15px; color:var(--cream)}}
  .hz .nm b{{font-family:var(--cond); font-style:normal; color:var(--gold-dim); font-size:11px; letter-spacing:.06em; margin-left:6px}}
  .hz .note2{{font-family:var(--serif); font-style:italic; color:var(--muted); font-size:12px; text-align:right; max-width:52%}}
  .stand{{display:grid; grid-template-columns:1fr 1fr; gap:0 20px}}
  .st{{display:grid; grid-template-columns:1fr auto auto; align-items:baseline; gap:8px; padding:4px 0; border-bottom:1px dotted rgba(230,194,92,.14)}}
  .st .t{{font-family:var(--display); font-style:italic; transform:scaleY(1.03); transform-origin:left; color:var(--cream); font-size:14px}}
  .st .rt{{color:var(--gold); font-variant-numeric:tabular-nums; font-weight:700; font-size:13px}}
  .st .wl{{color:var(--muted); font-variant-numeric:tabular-nums; font-size:12px; width:34px; text-align:right}}
  .foot-mini{{border:0; padding-top:8px; font-family:var(--serif); font-style:italic; color:var(--muted-dim); font-size:12px; margin:0}}
  .ln{{display:grid; grid-template-columns:1fr auto; gap:4px 10px; padding:6px 0; border-bottom:1px solid rgba(230,194,92,.12)}} .ln:last-child{{border-bottom:0}}
  .ln .g{{font-family:var(--display); font-style:italic; color:var(--cream); font-size:14px}}
  .ln .loc{{font-family:var(--serif); font-style:italic; color:var(--muted); font-size:11.5px}}
  .ln .od{{font-family:var(--cond); font-variant-numeric:tabular-nums; font-weight:700; color:var(--gold); font-size:14px; align-self:center}}
  .slate{{display:grid; grid-template-columns:1fr 1fr; gap:1px 20px}}
  .cg{{display:flex; align-items:baseline; gap:8px; padding:4px 0; border-bottom:1px dotted rgba(230,194,92,.13); font-size:13px}}
  .cg .t{{font-family:var(--display); font-style:italic; color:var(--cream)}} .cg .op{{color:var(--muted)}}
  .cg .day{{margin-left:auto; color:var(--gold-dim); letter-spacing:.1em; font-size:11px}}
  .mq{{font-family:var(--serif); font-style:italic; color:var(--cream); font-size:15px; line-height:1.5; margin:0}}
  .mq .qm{{font-family:Georgia,serif; color:var(--crimson-br); font-size:40px; line-height:0; vertical-align:-14px; margin-right:4px}}
  .mq cite{{display:block; text-align:right; font-style:normal; font-family:var(--cond); text-transform:uppercase; letter-spacing:.14em; font-size:11px; color:var(--gold); margin-top:8px}}
  .ribbon{{margin-top:14px; padding:14px; text-align:center; display:flex; justify-content:center; gap:30px; flex-wrap:wrap;
    font-family:var(--display); background:linear-gradient(90deg,var(--oxblood2),var(--oxblood),var(--oxblood2)); border:1px solid var(--edge)}}
  .ribbon span{{color:var(--cream); font-size:19px; transform:scaleY(1.08); display:inline-block}}
  .ribbon span b{{color:var(--gold)}} .ribbon span i{{color:var(--crimson-br); font-style:normal}}
  @media(max-width:760px){{.cols,.poll,.stand,.slate,.sgrid,.mgrid{{grid-template-columns:1fr}} .crest{{display:none}} .title .wm{{font-size:52px}}}}
</style>
<div class="wrap">
  <span class="eyebrow">Page Two &nbsp;·&nbsp; <b>The Sports Desk</b> &nbsp;·&nbsp; <b>{eyebrow_week}</b></span>
  <header class="mast">
    <div class="crest" aria-hidden="true"><svg viewBox="0 0 96 104">
      <path d="M48 3 L92 15 V56 C92 82 74 96 48 102 C22 96 4 82 4 56 V15 Z" fill="#231012" stroke="#e6c25c" stroke-width="2.5"/>
      <path d="M48 10 L85 20 V56 C85 78 70 90 48 95 C26 90 11 78 11 56 V20 Z" fill="#3a141a" stroke="#b3121a" stroke-width="1.4"/>
      <text x="48" y="50" text-anchor="middle" font-family="Impact,sans-serif" font-size="30" fill="#e6c25c">DW</text>
      <rect x="30" y="58" width="36" height="3" fill="#b3121a"/>
      <text x="48" y="74" text-anchor="middle" font-family="Arial Narrow,sans-serif" font-size="8" letter-spacing="3" fill="#efe6d4">LEAGUE</text>
    </svg></div>
    <div class="title"><div class="wm">The Dynasty <em>Dispatch</em></div>
      <div class="sub">Dynasty Warriors · NCAA CFB 2027 · Est. 2026</div></div>
    <div class="issue"><span class="v">{issue}</span><span class="stars">★★★★★</span>
      <div class="d">{date}</div><div class="wk">{week}</div></div>
  </header>
  <div class="rule-heavy"></div><div class="rule-thin"></div>
  <section class="banner"><div class="kicker">{kicker}</div><h1>{headline}</h1><p class="dek">{dek}</p></section>
  <div class="fullwide">{top}</div>
  <div class="cols"><div class="stack">{left}</div><div class="stack">{right}</div></div>
  <div class="ribbon"><span><i>★</i> <b>Build</b> Your Dynasty</span><span><i>★</i> <b>Earn</b> Your Legacy</span><span><i>★</i> <b>Respect</b> the Dispatch</span></div>
</div>
"""


if __name__ == "__main__":  # local self-test: fill with sample data, write HTML to view
    sample = {
        "week_label": "Week 1", "issue": "Volume 1 · Issue 1", "date": "July 22, 2026",
        "kicker": "The Season Opens", "headline": "First Blood in the Trenches",
        "dek": "The whistle blew and the pretenders showed. The Dispatch keeps the receipts.",
        "power_rankings": [
            {"team": "Ohio State", "conf": "Big Ten", "blurb": "Jeremiah Smith headlines the Heisman board."},
            {"team": "Georgia", "conf": "SEC", "blurb": "No. 1 recruiting class. The Dawgs reload."},
            {"team": "Notre Dame", "conf": "SEC", "blurb": "A brutal slate awaits the paper favorite."},
        ],
        "storylines": [{"title": "The Bag Race", "body": "Alabama and Ohio State open at $12.5M apiece."}],
        "cfp_watch": [{"title": "The Frontrunners", "body": "The sheet's top tier sets the tone."}],
        "top25": [{"rk": i+1, "team": t, "rec": "0–0"} for i, t in enumerate(
            ["Ohio State", "Georgia", "Notre Dame", "Oregon", "Alabama", "Indiana"])],
        "heisman": [{"name": "Jeremiah Smith", "tag": "WR · OHIO STATE", "note": "99 overall. The leader."}],
        "recruiting": [{"title": "First Blood: Crimson", "body": "Georgia lands 5-star Edge K. Burfict."}],
        "standings": [{"team": t, "rating": "—", "rec": "0–0"} for t in
                      ["Alabama", "Georgia", "Ohio State", "Oregon", "Michigan", "Texas"]],
        "line": [{"game": "LSU at Ohio State", "loc": "Ohio Stadium", "odds": "OSU −7.5"}],
        "scoreboard": {
            "away": "Penn State", "away_line": [7, 7, 14, 0], "away_total": 28,
            "home": "Notre Dame", "home_line": [26, 14, 7, 17], "home_total": 64,
            "venue": "Notre Dame Stadium", "note": "The Irish average 12.5 a snap in a home coronation."},
        "final_scores": [
            {"away": "Penn State", "away_score": 28, "home": "Notre Dame", "home_score": 64,
             "venue": "Notre Dame Stadium", "blurb": "The Irish average 12.5 a snap in a home coronation."},
            {"away": "Auburn", "away_score": 42, "home": "Pitt", "home_score": 17,
             "venue": "Acrisure Stadium", "blurb": "Byrum Brown goes for 515 total in his Auburn debut."},
        ],
        "marquee": [
            {"matchup": "Texas at USC", "label": "GAME OF THE WEEK", "venue": "LA Coliseum · Monday",
             "writeup": "A Monday-night QB duel. Texas has the trench edge.", "watch_for": "Manning vs USC's edge rush"},
            {"matchup": "Oregon at Alabama", "label": "TOP-FIVE TEST", "venue": "Bryant-Denny · Saturday",
             "writeup": "The best game on paper. Playoff-seed leverage.", "watch_for": "Moore vs the Tide secondary"},
        ],
        "overheard": [
            {"quote": "Wasn't tryna run up the score, just getting him Heisman stats.", "who": "Austin (Auburn)"},
            {"quote": "Team ain't ready for them SEC boys.", "who": "Drew (Pitt)"},
        ],
        "commissioner": "It's early, but the war has officially begun. Keep building.",
        "media_quote": "Play your games, stream your games, and give us something worth printing.",
    }
    import pathlib
    pathlib.Path("dispatch_out.html").write_text(build_html(sample))
    print("wrote dispatch_out.html")
