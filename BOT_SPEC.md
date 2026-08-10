# CFB Online Dynasty — Discord League Bot

A self-hosted Discord bot + setup scripts that stand up and run a college-football
online-dynasty league server. It builds the channels/roles, posts the rules, greets
new members, and maintains **live "teams taken / available" boards** driven entirely
by coaches' nicknames. Runs 24/7 in the cloud.

---

## What it does

**Server setup (one-time, scripted)**
- Creates all categories, channels, and roles from a single JSON config
- Posts formatted **rules**, **how-to-join**, and a pinned **welcome** message

**Onboarding (automatic, always-on)**
- Greets every new member in a welcome channel
- Auto-assigns a starter role (e.g. `Coach`) on join

**Live team boards (the core feature)**
- **#team-assignments** — who has which school, as clickable @mentions
- **#teams-available** — every open school, grouped by conference, one per line
- A coach claims a team simply by **setting their server nickname to their school**
  (e.g. "Georgia"). The bot moves it from Available → Taken instantly. Leaving,
  renaming, or role changes all refresh both boards automatically — no commands.

**Smart nickname matching**
- 230+ **nickname/mascot aliases** (Bama → Alabama, Buckeyes → Ohio State,
  Geaux Tigers → LSU, Hotty Toddy → Ole Miss, etc.)
- Tolerant of **emojis, numbers/years, and fancy unicode fonts** in nicknames
  (e.g. "🐘 Bama 2027", "Texas🟠longhorn" both resolve correctly)
- Ambiguous bare mascots (Tigers, Bulldogs, Wildcats…) intentionally require the
  school name, since they map to multiple teams

**League management**
- **Duplicate protection** — if two coaches pick the same team, the board flags a
  conflict and the bot DMs the second person to choose another
- **Reserved teams** — manually pre-claim schools for coaches who signed up before
  joining Discord (tags them by username or role if they're in the server)
- **#later-losers** — auto-posts a rotating farewell roast when a coach leaves, and
  frees their team back to the board

---

## Tech stack

- **Language:** Python 3 (single dependency: `discord.py`)
- **Hosting:** any always-on host; reference deployment is **Railway** (~$5/mo)
- **Persistence:** none required — the boards are derived from live Discord state
  (member roles + nicknames) plus small JSON config files in the repo
- **Two kinds of scripts:**
  - one-shot (run locally when needed): build server, post rules/how-to/welcome
  - persistent (runs 24/7 on the host): greetings, auto-role, live boards, farewells

---

## What it needs

1. **A Discord account** with permission to create/own a server
2. **A bot application** (free, via the Discord Developer Portal) with:
   - the bot token (kept as a secret env var, never in code)
   - the **Server Members Intent** enabled (needed for join/leave/nickname events)
   - **Administrator** permission in the server (simplest; can be scoped down)
3. **A host** for the always-on process (Railway, a small VPS, a Raspberry Pi, or
   any machine left running)
4. **Bot role positioned above** the auto-assigned role so it can grant it

---

## Configuration (all plain JSON, edit + redeploy)

| File | Controls |
|------|----------|
| `server_config.json` | server name, categories, channels, roles |
| `teams_fbs.json` | full team pool grouped by conference (trim to your league) |
| `aliases.json` | nickname/mascot → school mappings |
| `reserved.json` | manually pre-claimed teams → owner label |

Rules / how-to / welcome text live in small standalone post scripts.

---

## Setup flow (about 20–30 minutes)

1. Create an empty Discord server (one click)
2. Create the bot in the Developer Portal, enable Server Members Intent, copy token
3. Invite the bot to the server with Administrator
4. Run `setup_server.py` → builds all channels + roles
5. Run the post scripts → rules, how-to-join, pinned welcome
6. Deploy the persistent bot to the host with the token + server ID as env vars
7. Drag the bot's role above the coach role → done

Everything after that is automatic. To change rules, teams, aliases, etc., edit the
relevant JSON/script and redeploy.

---

## Customizing for a different league

- **Different sport/teams:** replace `teams_fbs.json` with any team list grouped by
  division/conference — the whole boards system is generic
- **Different roles/channels:** edit `server_config.json`
- **Different vibe:** edit the rules, welcome, and farewell text
- **Different claim role:** change one constant (`ROLE_ON_JOIN`)

---

## Costs

- Discord bot: **free**
- Hosting: **free** on a home machine / Raspberry Pi, or **~$5/month** on Railway or
  a small VPS

---

## Honest limitations

- The bot **flags** duplicate claims and DMs the offender but doesn't forcibly rename
  people (avoids edit loops / permission issues). A hard-enforcement mode is possible.
- Team matching keys off nicknames, so **ambiguous bare mascots** need the school name.
- The live board reflects state **only while the bot is running** — it rebuilds on
  startup, so brief host downtime just means it refreshes when it comes back.
