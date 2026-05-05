"""SAHIIX AGI Chat Bot Integrations — Slack + Discord."""
import asyncio
import logging
from typing import Optional

from integrations.slack_bot import SlackBot
from integrations.discord_bot import DiscordBot

logger = logging.getLogger(__name__)


async def start_bots(
    slack: bool = True,
    discord: bool = True,
    slack_bot_token: Optional[str] = None,
    slack_app_token: Optional[str] = None,
    discord_bot_token: Optional[str] = None,
    agi_api_base: str = "http://localhost:7777",
):
    """Start requested bot integrations concurrently.

    Parameters
    ----------
    slack : bool
        Start Slack Socket Mode bot.
    discord : bool
        Start Discord bot.
    slack_bot_token : str, optional
        Overrides env SLACK_BOT_TOKEN.
    slack_app_token : str, optional
        Overrides env SLACK_APP_TOKEN.
    discord_bot_token : str, optional
        Overrides env DISCORD_BOT_TOKEN.
    agi_api_base : str
        Base URL for SAHIIX AGI HTTP API.
    """
    tasks = []
    slack_bot: Optional[SlackBot] = None
    discord_bot: Optional[DiscordBot] = None

    if slack:
        slack_bot = SlackBot(
            bot_token=slack_bot_token,
            app_token=slack_app_token,
            agi_api_base=agi_api_base,
        )
        if slack_bot.bot_token and slack_bot.app_token:
            tasks.append(asyncio.create_task(slack_bot.start()))
        else:
            logger.warning("Slack tokens missing; skipping Slack bot.")

    if discord:
        discord_bot = DiscordBot(
            token=discord_bot_token,
            agi_api_base=agi_api_base,
        )
        if discord_bot.token:
            tasks.append(asyncio.create_task(discord_bot.start_bot()))
        else:
            logger.warning("Discord token missing; skipping Discord bot.")

    if not tasks:
        logger.error("No bots configured to start.")
        return

    logger.info("Starting %d bot(s)…", len(tasks))
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Bot runner cancelled.")
    finally:
        if slack_bot:
            await slack_bot.stop()
        if discord_bot:
            await discord_bot.stop_bot()


def main():
    """CLI entry point for starting all bots."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(start_bots())


if __name__ == "__main__":
    main()
