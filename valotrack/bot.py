"""The Discord client: wires up the database, API client, cogs, and command sync."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .config import Settings
from .db import Database
from .henrik import HenrikClient
from .tracker import Tracker

log = logging.getLogger(__name__)

EXTENSIONS = (
    "valotrack.cogs.accounts",
    "valotrack.cogs.feed",
    "valotrack.cogs.profile",
    "valotrack.cogs.mvp",
    "valotrack.cogs.settings",
)


class ValoTrackBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        # Slash commands only, so no privileged intents are needed.
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default(), help_command=None)
        self.settings = settings
        self.db = Database(settings.database_path)
        self.api = HenrikClient(settings.henrik_api_key, settings.requests_per_minute)
        self.tracker = Tracker(self.api, self.db)
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.api.start()
        for ext in EXTENSIONS:
            await self.load_extension(ext)

        if self.settings.dev_guild_id:
            guild = discord.Object(self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to dev guild %s", len(synced), self.settings.dev_guild_id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global commands (can take a few minutes to show up)", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s in %d servers", self.user, len(self.guilds))

    async def close(self) -> None:
        await super().close()
        await self.api.close()
        await self.db.close()

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = "You don't have permission to use that command."
        else:
            log.exception("Command %s failed", interaction.command and interaction.command.qualified_name, exc_info=error)
            message = "Something went wrong running that command. Check the bot logs for details."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
