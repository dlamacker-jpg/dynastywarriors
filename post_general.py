#!/usr/bin/env python3
"""
Post a welcome / server intro message into the #general channel via the bot.

Same usage as the other post scripts:
    export DISCORD_BOT_TOKEN="..."   # terminal only
    export DISCORD_GUILD_ID="..."
    python3 post_general.py

Safe by default: skips if the bot already posted here. Repost after edits with:
    python3 post_general.py --force
"""

import os
import sys

import discord

TARGET_CHANNEL = "general"

MESSAGES = [
    (
        "# 🏈 Welcome to Dynasty Warriors — NCAA CFB 2027\n"
        "**User vs user dynasty league where legends are made.**\n"
        "Glad you're here. This is home base — trash talk, hype, and everyday chatter all live here.\n\n"
        "**New? Get rolling in 3 steps:**\n"
        "• 🏫 Join the dynasty → **#how-to-join**\n"
        "• 📜 Know the rules → **#rules**\n"
        "• ✏️ Rename yourself to your school (rule #9), then say what's up in **#newbies**\n\n"
        "**Where things happen:**\n"
        "• 🎮 Set up your games → **#user-game-coordination**\n"
        "• 📺 Going live? Drop your link → **#streaming**\n"
        "• 🎬 Big plays → **#highlight-tapes**\n"
        "• ✅ Done with your week → **#rta**\n"
        "• 🏆 Rankings, awards & polls → **#polls** / season channels\n\n"
        "It's just a game — play hard, have fun, and step on some throats. 😤\n"
        "Let's build a dynasty. 🐾🏆"
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
