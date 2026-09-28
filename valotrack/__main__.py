"""Entry point: `python -m valotrack`."""

import logging
import sys

import discord

from .bot import ValoTrackBot
from .config import ConfigError, load_settings


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(1)
    discord.utils.setup_logging(level=logging.INFO)
    bot = ValoTrackBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
