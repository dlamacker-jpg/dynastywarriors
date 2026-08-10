#!/usr/bin/env python3
"""
Persistent bot that greets new members in the #newbies channel.

Unlike setup_server.py / post_rules.py (which run once and exit), this one stays
running and listens for join events. It only greets people while it's running.

Requirements:
  1. Enable the Server Members Intent:
     Developer Portal -> your app -> Bot -> Privileged Gateway Intents ->
     turn ON "Server Members Intent" -> Save.
  2. Same env vars as the other scripts:
       export DISCORD_BOT_TOKEN="..."   # terminal only
       export DISCORD_GUILD_ID="..."    # optional here, but keep it consistent
  3. Run it and leave it running:
       python3 welcome_bot.py

Edit WELCOME_CHANNEL or WELCOME_MESSAGE below to taste. {mention} is replaced
with a ping to the new member; {server} with the server name.
"""

import os
import sys

import discord

WELCOME_CHANNEL = "newbies"

WELCOME_MESSAGE = (
    "🏈 Welcome to **{server}**, {mention}! Glad to have you in the league.\n\n"
    "Get started:\n"
    "• 📜 Read the rules in **#rules**\n"
    "• 🏫 Claim your team in **#how-to-join**\n"
    "• ✏️ Change your server nickname to the **school you're using** (rule #9)\n"
    "• 👋 Say what's up and introduce yourself right here\n\n"
    "It's just a game — play hard, have fun. Let's run it. 🏆"
)


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit("Set DISCORD_BOT_TOKEN in your terminal first.")

    intents = discord.Intents.default()
    intents.members = True  # requires the Server Members Intent (see header)
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        print(f"Welcome bot online as {client.user}. Watching for new members...")

    @client.event
    async def on_member_join(member: discord.Member) -> None:
        channel = discord.utils.get(member.guild.text_channels, name=WELCOME_CHANNEL)
        if channel is None:
            print(f"No #{WELCOME_CHANNEL} channel in {member.guild.name}; skipping.")
            return
        await channel.send(
            WELCOME_MESSAGE.format(mention=member.mention, server=member.guild.name)
        )
        print(f"Greeted {member} in #{WELCOME_CHANNEL}.")

    client.run(token)


if __name__ == "__main__":
    main()
