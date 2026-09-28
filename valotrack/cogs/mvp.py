"""Weekly MVP recap: scheduled post plus on-demand standings."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..db import GuildConfig
from ..embeds import weekly_embed
from ..stats import aggregate
from ..weekly import last_scheduled, pick_awards, rank_by_rating

if TYPE_CHECKING:
    from ..bot import ValoTrackBot

log = logging.getLogger(__name__)
WEEK = timedelta(days=7)


class Mvp(commands.Cog):
    mvp = app_commands.Group(name="mvp", description="Weekly MVP", guild_only=True)

    def __init__(self, bot: ValoTrackBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.weekly.start()

    async def cog_unload(self) -> None:
        self.weekly.cancel()

    async def build_recap(self, config: GuildConfig, start: datetime, end: datetime,
                          preview: bool = False) -> tuple[discord.Embed, int]:
        """Return the recap embed and how many games it covered."""
        aggs, owners = [], {}
        for link in await self.bot.db.guild_links(config.guild_id):
            history = await self.bot.db.history(link.player.puuid, since=start, until=end, queues=config.queues)
            aggs.append(aggregate(link.player.puuid, history))
            owners[link.player.puuid] = link.discord_id
        played = [a for a in aggs if a.games]
        embed = weekly_embed(
            start, end, rank_by_rating(played, config.min_games), pick_awards(played, config.min_games),
            owners, config.min_games, preview=preview,
        )
        return embed, sum(a.games for a in played)

    @tasks.loop(minutes=10)
    async def weekly(self) -> None:
        now = datetime.now(timezone.utc)
        for config in await self.bot.db.all_configs():
            try:
                await self._maybe_post(config, now)
            except Exception:
                log.exception("Weekly recap failed for guild %s", config.guild_id)

    @weekly.before_loop
    async def _before_weekly(self) -> None:
        await self.bot.wait_until_ready()

    async def _maybe_post(self, config: GuildConfig, now: datetime) -> None:
        if not config.recap_channel_id:
            return
        due = last_scheduled(now, config.mvp_weekday, config.mvp_hour, ZoneInfo(config.timezone))
        if config.last_mvp_at is None:
            # First run for this server: wait for the next scheduled slot instead of posting right away.
            await self.bot.db.update_config(config.guild_id, last_mvp_at=due)
            return
        if due <= config.last_mvp_at:
            return

        await self.bot.db.update_config(config.guild_id, last_mvp_at=due)
        embed, games = await self.build_recap(config, due - WEEK, due)
        if not games:
            log.info("Skipping weekly recap for guild %s: no games", config.guild_id)
            return
        channel = self.bot.get_channel(config.recap_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("Recap channel %s for guild %s not found", config.recap_channel_id, config.guild_id)
            return
        await channel.send(embed=embed)

    @mvp.command(name="standings", description="See how this week's MVP race looks so far")
    async def standings(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        config = await self.bot.db.get_config(interaction.guild_id)
        now = datetime.now(timezone.utc)
        start = last_scheduled(now, config.mvp_weekday, config.mvp_hour, ZoneInfo(config.timezone))
        embed, _ = await self.build_recap(config, start, now, preview=True)
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @mvp.command(name="post", description="Post an MVP recap for the last 7 days right now")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def post(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        config = await self.bot.db.get_config(interaction.guild_id)
        now = datetime.now(timezone.utc)
        embed, _ = await self.build_recap(config, now - WEEK, now)
        await interaction.response.send_message(embed=embed)


async def setup(bot: ValoTrackBot) -> None:
    await bot.add_cog(Mvp(bot))
