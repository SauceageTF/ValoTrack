"""Stat lookups: /stats, /compare, /leaderboard, /rank."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..embeds import compare_embed, leaderboard_embed, rank_embed, stats_embed
from ..henrik import HenrikError
from ..stats import LEADERBOARD_STATS, aggregate
from .common import MODE_CHOICES, PERIOD_CHOICES, mode_filter, period_window, resolve_link

if TYPE_CHECKING:
    from ..bot import ValoTrackBot

# Totals don't need a minimum sample; rate stats do.
COUNT_STATS = {"kills", "games", "rr", "fb"}
STAT_CHOICES = [app_commands.Choice(name=label, value=key) for key, (label, _, _) in LEADERBOARD_STATS.items()]
NO_PINGS = discord.AllowedMentions.none()


class Profile(commands.Cog):
    def __init__(self, bot: ValoTrackBot) -> None:
        self.bot = bot

    @app_commands.command(name="stats", description="Stats for a tracked player")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose stats (defaults to you)", period="Time window", mode="Only one game mode")
    @app_commands.choices(period=PERIOD_CHOICES, mode=MODE_CHOICES)
    async def stats(self, interaction: discord.Interaction, member: discord.Member | None = None,
                    period: str | None = None, mode: str | None = None) -> None:
        link = await resolve_link(self.bot, interaction, member)
        if link is None:
            return
        config = await self.bot.db.get_config(link.guild_id)
        label, since = period_window(period)
        queues, modes_label = mode_filter(config, mode)
        history = await self.bot.db.history(link.player.puuid, since=since, queues=queues)
        embed = stats_embed(link.player, link.discord_id, aggregate(link.player.puuid, history), label, modes_label)
        await interaction.response.send_message(embed=embed, allowed_mentions=NO_PINGS)

    @app_commands.command(name="compare", description="Put two players head to head")
    @app_commands.guild_only()
    @app_commands.describe(first="First player", second="Second player (defaults to you)", period="Time window",
                           mode="Only one game mode")
    @app_commands.choices(period=PERIOD_CHOICES, mode=MODE_CHOICES)
    async def compare(self, interaction: discord.Interaction, first: discord.Member,
                      second: discord.Member | None = None, period: str | None = None,
                      mode: str | None = None) -> None:
        entries = []
        for member in (first, second or interaction.user):
            link = await resolve_link(self.bot, interaction, member)
            if link is None:
                return
            entries.append(link)
        config = await self.bot.db.get_config(entries[0].guild_id)
        label, since = period_window(period)
        queues, _ = mode_filter(config, mode)
        rows = []
        for link in entries:
            history = await self.bot.db.history(link.player.puuid, since=since, queues=queues)
            rows.append((link.player, link.discord_id, aggregate(link.player.puuid, history)))
        await interaction.response.send_message(embed=compare_embed(rows, label), allowed_mentions=NO_PINGS)

    @app_commands.command(name="leaderboard", description="Rank everyone tracked in this server")
    @app_commands.guild_only()
    @app_commands.describe(stat="What to rank by", period="Time window", mode="Only one game mode")
    @app_commands.choices(stat=STAT_CHOICES, period=PERIOD_CHOICES, mode=MODE_CHOICES)
    async def leaderboard(self, interaction: discord.Interaction, stat: str = "rating",
                          period: str | None = None, mode: str | None = None) -> None:
        assert interaction.guild_id is not None
        config = await self.bot.db.get_config(interaction.guild_id)
        label, since = period_window(period)
        queues, modes_label = mode_filter(config, mode)
        _, key, _ = LEADERBOARD_STATS[stat]
        min_games = 1 if stat in COUNT_STATS else config.min_games

        rows = []
        for link in await self.bot.db.guild_links(interaction.guild_id):
            history = await self.bot.db.history(link.player.puuid, since=since, queues=queues)
            agg = aggregate(link.player.puuid, history)
            if agg.games >= min_games:
                rows.append((link.discord_id, link.player, agg))
        rows.sort(key=lambda row: key(row[2]), reverse=True)
        embed = leaderboard_embed(rows[:15], stat, f"{label} · {modes_label}", min_games)
        await interaction.response.send_message(embed=embed, allowed_mentions=NO_PINGS)

    @app_commands.command(name="rank", description="Current competitive rank and RR")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose rank (defaults to you)")
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        link = await resolve_link(self.bot, interaction, member)
        if link is None:
            return
        await interaction.response.defer()
        p = link.player
        try:
            mmr = await self.bot.api.get_mmr(p.region, p.platform, p.puuid)
        except HenrikError as exc:
            await interaction.followup.send(f"Couldn't fetch rank for **{p.riot_id}** ({exc.message}).")
            return
        await interaction.followup.send(embed=rank_embed(p, link.discord_id, mmr or {}), allowed_mentions=NO_PINGS)


async def setup(bot: ValoTrackBot) -> None:
    await bot.add_cog(Profile(bot))
