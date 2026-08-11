#!/usr/bin/env python3
"""
Delete messages in a channel posted AFTER a given timestamp.

SAFETY: without --confirm this only COUNTS what would be deleted (dry run) and
deletes nothing. Deletion is PERMANENT — there is no undo. Discord can bulk-delete
only messages younger than 14 days; older ones are removed one-by-one (slower).

Usage:
    export DISCORD_BOT_TOKEN=...
    export DISCORD_GUILD_ID=...

    # 1) dry run — just counts:
    python3 clear_chat.py general "2026-08-11 14:00"

    # 2) actually delete (only after you're happy with the count):
    python3 clear_chat.py general "2026-08-11 14:00" --confirm

Timestamp is read as US Eastern (the league's timezone). Messages posted AFTER it
are deleted.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import discord

EASTERN = timezone(timedelta(hours=-4))  # EDT


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if not token or not guild_id:
        sys.exit("Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID first.")

    confirm = "--confirm" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--confirm"]
    if len(args) < 2:
        sys.exit('Usage: python3 clear_chat.py <channel> "YYYY-MM-DD HH:MM" [--confirm]')
    channel_name = args[0]
    try:
        cutoff = datetime.strptime(args[1], "%Y-%m-%d %H:%M").replace(tzinfo=EASTERN)
    except ValueError:
        sys.exit('Timestamp must look like "2026-08-11 14:00" (24-hour, Eastern).')

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(int(guild_id)) or await client.fetch_guild(int(guild_id))
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if channel is None:
                print(f"No #{channel_name} channel found.")
                return

            count = 0
            async for _ in channel.history(limit=None, after=cutoff):
                count += 1
            print(f"{count} messages in #{channel_name} posted after {args[1]} Eastern.")

            if count == 0:
                print("Nothing to delete.")
                return
            if not confirm:
                print("DRY RUN — nothing deleted. Re-run with --confirm to permanently delete them.")
                return

            deleted = await channel.purge(after=cutoff, limit=None, bulk=True)
            print(f"Deleted {len(deleted)} messages permanently.")
            if len(deleted) < count:
                print("Some were older than 14 days and couldn't be bulk-deleted.")
        finally:
            await client.close()

    client.run(token)


if __name__ == "__main__":
    main()
