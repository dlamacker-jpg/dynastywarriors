#!/usr/bin/env python3
"""
Post the "how to join" guide into the #how-to-join channel via the bot.

Same usage as post_rules.py:
    export DISCORD_BOT_TOKEN="..."   # terminal only
    export DISCORD_GUILD_ID="..."
    python3 post_howto.py

Safe by default: skips if the bot already posted here. Repost after edits with:
    python3 post_howto.py --force

Edit the MESSAGES list below to change wording (keep each under ~2000 chars).
"""

import os
import sys

import discord

TARGET_CHANNEL = "how-to-join"

MESSAGES = [
    (
        "# 🏫 How to Join — Dynasty Warriors\n"
        "Welcome! Follow these steps to get into the league on **NCAA 27**.\n\n"
        "## 1️⃣ Join the dynasty in-game\n"
        "In NCAA 27: **Dynasty → Online Dynasty → Join Online Dynasty**, then search/enter:\n"
        "• **Dynasty Name:** `DynastyWarriors27`\n"
        "• **Password:** `GOAT`\n\n"
        "## 2️⃣ Pick an available team\n"
        "Check **#team-assignments** first to see which schools are already taken, then claim an open one.\n\n"
        "## 3️⃣ Rename yourself to your school (required — rule #9)\n"
        "Change your **server nickname** to the team you're using so everyone knows who's who.\n"
        "• **Desktop:** click the server name (top-left) → **Edit Server Profile** → set **Nickname** → Save\n"
        "• **Mobile:** tap the server name → your profile → **Edit Server Profile** → **Nickname**\n"
        "Example: if you're running Georgia, set your nickname to **Georgia**.\n\n"
        "## 4️⃣ Say what's up\n"
        "Drop an intro in **#newbies** and read the **#rules**. Then get to scheduling in **#user-game-coordination**.\n\n"
        "Need help? Tag a **@Commissioner**. Let's run it. 🏈🏆"
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

            already = False
            async for msg in channel.history(limit=50):
                if msg.author.id == client.user.id:
                    already = True
                    break
            if already and not force:
                print(f"#{TARGET_CHANNEL} already has a post from the bot. Re-run with --force to repost.")
                return

            for block in MESSAGES:
                await channel.send(block)
            print(f"Posted {len(MESSAGES)} message(s) to #{TARGET_CHANNEL}.")
        finally:
            await client.close()

    client.run(token)


if __name__ == "__main__":
    main()
