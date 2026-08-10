#!/usr/bin/env python3
"""
Build out a Discord server for an NCAA CFB online dynasty from server_config.json.

You create the (empty) server and invite the bot with Administrator permission,
then run this once. It creates the roles, categories, and channels described in
the config. Safe to re-run: anything that already exists (matched by name) is
skipped, so you can tweak the config and run again to add what's new.

Usage:
    pip install "discord.py>=2.3"
    export DISCORD_BOT_TOKEN="your-bot-token"
    export DISCORD_GUILD_ID="123456789012345678"
    python setup_server.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import discord

CONFIG_PATH = Path(__file__).with_name("server_config.json")


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def parse_color(value: str | None) -> discord.Colour:
    if not value:
        return discord.Colour.default()
    return discord.Colour(int(value.lstrip("#"), 16))


async def ensure_roles(guild: discord.Guild, role_defs: list[dict]) -> dict[str, discord.Role]:
    existing = {r.name: r for r in guild.roles}
    roles: dict[str, discord.Role] = {}
    # Create top-to-bottom so Discord's position (highest first) matches config order.
    for spec in role_defs:
        name = spec["name"]
        if name in existing:
            print(f"  role exists:   {name}")
            roles[name] = existing[name]
            continue
        perms = discord.Permissions.all() if spec.get("admin") else discord.Permissions.none()
        role = await guild.create_role(
            name=name,
            colour=parse_color(spec.get("color")),
            hoist=spec.get("hoist", False),
            mentionable=spec.get("mentionable", True),
            permissions=perms,
            reason="Dynasty server setup",
        )
        print(f"  created role:  {name}")
        roles[name] = role
    return roles


def staff_overwrites(guild: discord.Guild, roles: dict[str, discord.Role]) -> dict:
    """Hide a category from @everyone; allow commish + co-commish (+ the bot)."""
    ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    for staff in ("Commissioner", "Co-Commissioner"):
        if staff in roles:
            ow[roles[staff]] = discord.PermissionOverwrite(view_channel=True)
    if guild.me is not None:
        ow[guild.me] = discord.PermissionOverwrite(view_channel=True)
    return ow


async def ensure_structure(guild: discord.Guild, config: dict, roles: dict[str, discord.Role]) -> None:
    existing_cats = {c.name: c for c in guild.categories}
    existing_chans = {c.name for c in guild.channels}

    for cat_spec in config["categories"]:
        cat_name = cat_spec["name"]
        overwrites = staff_overwrites(guild, roles) if cat_spec.get("staff_only") else {}

        category = existing_cats.get(cat_name)
        if category is None:
            category = await guild.create_category(cat_name, overwrites=overwrites, reason="Dynasty setup")
            print(f"created category: {cat_name}")
        else:
            print(f"category exists:  {cat_name}")

        for ch in cat_spec["channels"]:
            if ch["name"] in existing_chans:
                print(f"  channel exists: {ch['name']}")
                continue
            if ch.get("type") == "voice":
                await guild.create_voice_channel(ch["name"], category=category, reason="Dynasty setup")
            else:
                await guild.create_text_channel(
                    ch["name"], category=category, topic=ch.get("topic"), reason="Dynasty setup"
                )
            print(f"  created channel: {ch['name']}")


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if not token or not guild_id:
        sys.exit("Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID environment variables first.")

    config = load_config()
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(int(guild_id)) or await client.fetch_guild(int(guild_id))
            if guild is None:
                print("Bot is not in that guild. Invite it first, then re-run.")
                return
            print(f"Building '{guild.name}'...\n")
            desired_name = config.get("server_name")
            if desired_name and guild.name != desired_name:
                await guild.edit(name=desired_name, reason="Dynasty server setup")
                print(f"renamed server:  {desired_name}\n")
            roles = await ensure_roles(guild, config["roles"])
            await ensure_structure(guild, config, roles)
            print("\nDone. Your dynasty server is set up.")
        finally:
            await client.close()

    client.run(token)


if __name__ == "__main__":
    main()
