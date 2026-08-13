#!/usr/bin/env python3
"""
Post the TOP DAWGS league rules into the #rules channel via the bot.

Runs the same way as setup_server.py (same token + guild env vars):
    export DISCORD_BOT_TOKEN="..."      # in your terminal only
    export DISCORD_GUILD_ID="..."
    python3 post_rules.py

Safe by default: if the bot has already posted in #rules, it skips so you don't
get duplicates. To repost anyway (e.g. after editing the rules), run:
    python3 post_rules.py --force

Edit the RULES list below to change the wording. Each string is one Discord
message (keep each under ~2000 characters).
"""

import os
import sys

import discord

TARGET_CHANNEL = "rules"

RULES = [
    (
        "# 🏈 Dynasty Warriors — NCAA CFB 2027\n"
        "**It's just a game. Play hard. Have fun.**\n"
        "*User vs user dynasty league where legends are made.*\n\n"
        "**League Settings**\n"
        "❄️ 2 Play Cool Down  •  ⏱️ 6 Min Qtrs  •  🔁 24–48 Hour Sims  •  🎮 Play CPU Games  •  📋 Custom Playbooks Allowed\n\n"
        "## 📜 League Rules\n"
        "1. It's just a game\n"
        "2. Heisman difficulty\n"
        "3. 2 play cool down\n"
        "4. 6 min quarters\n"
        "5. 24–48 hour sims — sooner if the user games get played\n"
        "6. Will get kicked if not active — *unless on AUTO*\n"
        "7. Play CPU games!\n"
        "8. Conferences change every 2 years\n"
        "9. Change your name to the school you're using in the server for easy communication\n"
        "10. Custom head coaches\n"
        "11. Onside kicks only allowed if down 21 in the 1st half or 2nd half (if you're down or tied)\n"
        "12. Stay classy… or step on throats *(just a game)*\n"
        "13. Custom playbooks allowed\n\n"
        "⭐ Rules can be added depending on the situation throughout the game cycle."
    ),
    (
        "## 🏈 4th Down Rules\n"
        "• Must punt if 4th & 4 or more before the 50\n"
        "• Can go for it anytime in the redzone\n"
        "• Can go for it anytime if down 14\n"
        "• Can go for it anytime if down or tied in the 4th quarter\n"
        "• Can go if 4th & 3 or less anytime\n\n"
        "## ⏱️ Chew Clock Rules\n"
        "You can chew clock if you are up **21+ points in the 4th quarter**.\n\n"
        "## 🚪 Quitting Rules\n"
        "No tolerance for dashing.\n"
        "• **1st time:** player (highest overall on the team) suspended for a week or FL next game\n"
        "• **2nd time:** automatic boot\n"
        "You may concede defeat in the second half if down by 21+ points — **no dash boarding.**"
    ),
    (
        "## 🤖 CPU Games\n"
        "Play if you want — force wins given out. This is a user-vs-user focused league.\n\n"
        "## 😴 Inactivity Rules\n"
        "• If you miss an active check and show no activity, you'll be booted\n"
        "• If you know you'll be inactive for a bit, tell a commish\n"
        "• Get on at least one time a day\n"
        "• 3 no-shows / can't-plays in a row = kicked from the league — unless you let us know ahead of time!!\n\n"
        "## 📺 College Gameday\n"
        "Must stream playoffs & conference championship games!\n\n"
        "## 🗓️ Scheduling Rules\n"
        "• Both of y'all need to make an effort to schedule — FW determined by the commissioners\n"
        "• You must @ your opponent or you don't get credit for reaching out\n"
        "• If both attempted to schedule but it never happened, the game will be fair-simmed"
    ),
    (
        "## 🔄 Position Change Rules\n\n"
        "**Offense**\n"
        "✅ QB ↔ WR/RB\n"
        "✅ HB ↔ WR\n"
        "✅ WR → TE (if size fits, e.g. 6'3\" / 215 lbs)\n"
        "✅ TE ↔ FB\n"
        "✅ LT, LG, C, RG, RT — anywhere on the O-line\n"
        "❌ No moving OL to skill positions\n"
        "❌ No moving WR to QB\n\n"
        "**Defense**\n"
        "✅ LE ↔ RE ↔ DT\n"
        "✅ LOLB ↔ MLB ↔ ROLB\n"
        "✅ CB ↔ FS ↔ SS\n"
        "✅ LB ↔ DE/Edge\n"
        "❌ No DB to LB\n"
        "❌ No LB to DB\n\n"
        "**Athlete Recruits**\n"
        "• Can be moved to any position you'd like\n\n"
        "—\n"
        "**Play fair. Compete hard.** 🏆"
    ),
]


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if not token or not guild_id:
        sys.exit("Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID environment variables first.")
    force = "--force" in sys.argv

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(int(guild_id)) or await client.fetch_guild(int(guild_id))
            channel = discord.utils.get(guild.text_channels, name=TARGET_CHANNEL)
            if channel is None:
                print(f"No #{TARGET_CHANNEL} channel found. Run setup_server.py first.")
                return

            existing = [m async for m in channel.history(limit=50) if m.author.id == client.user.id]
            if existing and not force:
                print(f"#{TARGET_CHANNEL} already has rules posted. Re-run with --force to update them in place.")
                return

            # Sync in place: edit the bot's existing messages to match, add/delete as needed —
            # reposting after an edit updates #rules without duplicating the whole wall.
            existing.reverse()  # oldest first, to match block order
            for i, content in enumerate(RULES):
                if i < len(existing):
                    if existing[i].content != content:
                        await existing[i].edit(content=content)
                else:
                    await channel.send(content)
            for extra in existing[len(RULES):]:
                await extra.delete()
            print(f"Synced {len(RULES)} rules messages to #{TARGET_CHANNEL}.")
        finally:
            await client.close()

    client.run(token)


if __name__ == "__main__":
    main()
