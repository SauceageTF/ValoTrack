"""Background match polling and the live match feed."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..embeds import match_embed
from ..henrik import HenrikError
from ..tracker import Announcement
from .common import resolve_link

if TYPE_CHECKING:
    from ..bot import ValoTrackBot

log = logging.getLogger(__name__)


class Feed(commands.Cog):
    def __init__(self, bot: ValoTrackBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.poll.change_interval(minutes=self.bot.settings.poll_interval_minutes)
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()

    @tasks.loop(minutes=3)
    async def poll(self) -> None:
        players = await self.bot.db.tracked_players()
        tracked = {p.puuid for p in players}
        for player in players:
            # Any exception escaping a tasks.loop body stops the loop for good, so contain it per player.
            try:
                for match, lines in await self.bot.tracker.poll_player(player, tracked):
                    log.info("New match %s for %s", match.match_id, ", ".join(l.riot_id for l in lines))
                    for announcement in await self.bot.tracker.announcements_for(match, lines):
                        await self._send(announcement)
            except HenrikError as exc:
                log.warning("Polling %s failed: %s", player.riot_id, exc)
            except Exception:
                log.exception("Unexpected error polling %s", player.riot_id)

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def _send(self, a: Announcement) -> None:
        channel = self.bot.get_channel(a.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("Feed channel %s in guild %s is missing or not a text channel", a.channel_id, a.guild_id)
            return
        embed = match_embed(a.match, a.lines, a.owners, a.streaks)
        try:
            message = await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.warning("Couldn't post match %s to channel %s: %s", a.match.match_id, a.channel_id, exc)
            return
        await self.bot.db.mark_announced(a.guild_id, a.match.match_id, message.id)

    @app_commands.command(name="lastmatch", description="Show someone's most recent tracked match")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose match to show (defaults to you)")
    async def lastmatch(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        link = await resolve_link(self.bot, interaction, member)
        if link is None:
            return
        history = await self.bot.db.history(link.player.puuid, limit=1)
        if not history:
            await interaction.response.send_message(
                f"No matches stored for **{link.player.riot_id}** yet.", ephemeral=True
            )
            return
        match_id = history[0][0].match_id

        # Include any other linked players from this server who were in the same game.
        guild_links = await self.bot.db.guild_links(link.guild_id)
        owners = {l.player.puuid: l.discord_id for l in guild_links}
        found = await self.bot.db.match_lines(match_id, owners)
        assert found is not None
        match, lines = found
        await interaction.response.send_message(
            embed=match_embed(match, lines, owners), allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: ValoTrackBot) -> None:
    await bot.add_cog(Feed(bot))
