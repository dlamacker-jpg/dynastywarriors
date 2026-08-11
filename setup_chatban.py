#!/usr/bin/env python3
"""
Create a "Chat Banned" role and deny it from sending messages in every channel.

Assign this role to a member (anyone with Manage Roles / the bot) to silence
them server-wide without kicking or banning — they can still read, but can't
post, react, or talk in voice. Remove the role to unmute.

    export DISCORD_BOT_TOKEN=...
    export DISCORD_GUILD_ID=...
    python3 setup_chatban.py

Idempotent: re-run any time (e.g. after adding new channels) to re-apply.
"""

import os
import sys

import discord

ROLE_NAME = "Chat Banned"


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if not token or not guild_id:
        sys.exit("Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID first.")

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(int(guild_id)) or await client.fetch_guild(int(guild_id))
            role = discord.utils.get(guild.roles, name=ROLE_NAME)
            if role is None:
                role = await guild.create_role(
                    name=ROLE_NAME, colour=discord.Colour(0x607D8B), reason="Chat ban role"
                )
                print(f"created role: {ROLE_NAME}")
            else:
                print(f"role exists: {ROLE_NAME}")

            deny = discord.PermissionOverwrite(
                send_messages=False,
                send_messages_in_threads=False,
                create_public_threads=False,
                create_private_threads=False,
                speak=False,
                send_voice_messages=False,
            )  # reactions intentionally left allowed
            locked = 0
            for channel in guild.channels:
                try:
                    await channel.set_permissions(role, overwrite=deny, reason="Chat ban lockout")
                    locked += 1
                except discord.Forbidden:
                    print(f"  no permission on #{channel.name}; skipped")
            print(f"locked {locked} channels for '{ROLE_NAME}'.")
            print("Assign the role to mute someone; remove it to unmute.")
        finally:
            await client.close()

    client.run(token)


if __name__ == "__main__":
    main()
