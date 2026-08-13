#!/usr/bin/env python3
"""
Post the PSN / gamertag roster into the #psns-gamertags channel via the bot.

Same usage as the other post scripts:
    export DISCORD_BOT_TOKEN="..."   # terminal only
    export DISCORD_GUILD_ID="..."
    python3 post_psns.py

Safe by default: skips if the bot already posted here. Repost after edits with:
    python3 post_psns.py --force

Mentions are rendered but do NOT ping (allowed_mentions is disabled), so this
won't blow up 29 phones when it posts.

Gamertags verified from the in-game Members list (Week 1, 2026).
"""

import os
import sys

import discord

TARGET_CHANNEL = "psns-gamertags"

MESSAGES = [
    (
        "# 🎮 PSNs & Gamertags — Dynasty Warriors\n"
        "Who you're actually playing when the invite comes through. "
        "Add your opponent before your user game so scheduling is painless.\n\n"
        "## SEC\n"
        "• **Alabama** — <@418963999212830720> — `Mondo BurgerHD`\n"
        "• **Florida** — <@1241181520870379701> — `campbellc21539`\n"
        "• **Georgia** — <@891744763919880254> — `Xploress`\n"
        "• **Kentucky** — <@636435644679192586> — `Choppaa1595`\n"
        "• **LSU** — <@1144566120175636481> — `LTEmmons2`\n"
        "• **Notre Dame** — <@468403022243037205> — `heard bout you`\n"
        "• **Oklahoma** — <@1011664803464491078> — `JustinR6655`\n"
        "• **Ole Miss** — <@1127413877085311067> — `Bepps212815`\n"
        "• **South Carolina** — <@544608171113840661> — `NinjaFrm-_-VA`\n"
        "• **Tennessee** — <@1220101692901691453> — `srry cj`\n"
        "• **Texas** — <@1307496646778818652> — `I_95south_`\n"
        "• **Texas A&M** — <@1240534443110830131> — `SoS_dotthemboys`"
    ),
    (
        "## Big Ten\n"
        "• **Indiana** — <@766460551420444692> — `Napoleon 223962`\n"
        "• **Iowa** — <@855984228156178483> — `iowa2021`\n"
        "• **Michigan** — <@881546145078345799> — `tlrockett10`\n"
        "• **Nebraska** — <@1529216586878160995> — `WarDaddy1988___`\n"
        "• **Ohio State** — <@1508460527175733288> — `dlamacker`\n"
        "• **Oregon** — <@1147339146982072391> — `CGC MorG`\n"
        "• **Penn State** — <@1013519510348759133> — `Rude214_MBK`\n"
        "• **UCLA** — <@255506749136568321> — `Mental Flawss`\n"
        "• **USC** — <@1264373901635485767> — `SkippyQ18`"
    ),
    (
        "## Big 12\n"
        "• **Texas Tech** — <@350359533987561492> — `WurthIt7895`\n"
        "• **Utah** — <@1537451898309705788> — `Lapound_`\n\n"
        "## ACC\n"
        "• **Clemson** — <@798636422034620437> — `CARNEGIE2381`\n"
        "• **Duke** — <@267674431260721152> — `lyfboothman1`\n"
        "• **Florida State** — <@1475977017651495085> — `CouncilStrong21`\n"
        "• **Miami** — <@948313627004387419> — `ROLLTIDE_334CRIP`\n"
        "• **NC State** — <@902818023847632956> — `Fatt_Trell215-`\n"
        "• **Pittsburgh** — <@1419895353951387722> — `luckylucianno737`\n\n"
        "**In the dynasty, not claimed in Discord yet:**\n"
        "• **Auburn** — `LukaSwifty3979`\n"
        "• **SMU** — `F_L_O_A_T_Y-`\n\n"
        "*Wrong tag? Ping the commish and it gets fixed.*"
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
                print(f"No #{TARGET_CHANNEL} channel found. Create it first.")
                return

            already = False
            async for msg in channel.history(limit=50):
                if msg.author.id == client.user.id:
                    already = True
                    break
            if already and not force:
                print(f"#{TARGET_CHANNEL} already has a post from the bot. Re-run with --force to repost.")
                return

            silent = discord.AllowedMentions.none()
            for block in MESSAGES:
                await channel.send(block, allowed_mentions=silent)
            print(f"Posted {len(MESSAGES)} message(s) to #{TARGET_CHANNEL}.")
        finally:
            await client.close()

    client.run(token)


if __name__ == "__main__":
    main()
