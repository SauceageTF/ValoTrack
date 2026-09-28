"""Server configuration (/settings ...) and /help."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import discord
from discord import app_commands
from discord.ext import commands

from ..db import GuildConfig
from ..embeds import VALORANT_RED
from ..stats import DEFAULT_QUEUES, QUEUE_NAMES, normalize_queue, queue_name
from ..weekly import WEEKDAYS, last_scheduled

if TYPE_CHECKING:
    from ..bot import ValoTrackBot

WEEKDAY_CHOICES = [app_commands.Choice(name=day, value=i) for i, day in enumerate(WEEKDAYS)]


def _channel_problem(channel: discord.TextChannel) -> str | None:
    perms = channel.permissions_for(channel.guild.me)
    missing = [name for name, ok in (("View Channel", perms.view_channel), ("Send Messages", perms.send_messages),
                                     ("Embed Links", perms.embed_links)) if not ok]
    return f"I'm missing {', '.join(missing)} in {channel.mention}." if missing else None


def _describe(config: GuildConfig) -> discord.Embed:
    def channel(cid: int | None) -> str:
        return f"<#{cid}>" if cid else "not set"

    embed = discord.Embed(title="ValoTrack settings", color=VALORANT_RED)
    embed.add_field(name="Match feed", value=channel(config.feed_channel_id))
    embed.add_field(name="MVP recap", value=channel(config.mvp_channel_id) +
                    (" (uses feed channel)" if not config.mvp_channel_id and config.feed_channel_id else ""))
    embed.add_field(name="Recap time",
                    value=f"{WEEKDAYS[config.mvp_weekday]}s at {config.mvp_hour:02d}:00 {config.timezone}")
    embed.add_field(name="Tracked modes", value=", ".join(queue_name(q) for q in config.queues))
    embed.add_field(name="MVP minimum", value=f"{config.min_games} games")
    return embed


class Settings(commands.Cog):
    settings = app_commands.Group(
        name="settings",
        description="Configure ValoTrack for this server",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: ValoTrackBot) -> None:
        self.bot = bot

    @settings.command(name="feed-channel", description="Where match results get posted")
    async def feed_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if problem := _channel_problem(channel):
            await interaction.response.send_message(problem, ephemeral=True)
            return
        await self.bot.db.update_config(channel.guild.id, feed_channel_id=channel.id)
        await interaction.response.send_message(f"Match results will be posted in {channel.mention}.", ephemeral=True)

    @settings.command(name="mvp-channel", description="Where the weekly MVP recap goes (defaults to the feed channel)")
    async def mvp_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if problem := _channel_problem(channel):
            await interaction.response.send_message(problem, ephemeral=True)
            return
        await self.bot.db.update_config(channel.guild.id, mvp_channel_id=channel.id)
        await interaction.response.send_message(f"Weekly MVP recaps will go to {channel.mention}.", ephemeral=True)

    @settings.command(name="mvp-schedule", description="When the weekly MVP recap is posted")
    @app_commands.describe(day="Day of the week", hour="Hour of the day (0-23)",
                           timezone_name="Timezone, e.g. America/New_York (default UTC)")
    @app_commands.rename(timezone_name="timezone")
    @app_commands.choices(day=WEEKDAY_CHOICES)
    async def mvp_schedule(self, interaction: discord.Interaction, day: int,
                           hour: app_commands.Range[int, 0, 23], timezone_name: str = "UTC") -> None:
        assert interaction.guild_id is not None
        try:
            tz = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            await interaction.response.send_message(f"`{timezone_name}` isn't a timezone I know.", ephemeral=True)
            return
        # Reset the "last posted" marker so changing the schedule doesn't trigger an immediate post.
        last = last_scheduled(datetime.now(timezone.utc), day, hour, tz)
        await self.bot.db.update_config(interaction.guild_id, mvp_weekday=day, mvp_hour=hour,
                                        timezone=timezone_name, last_mvp_at=last)
        await interaction.response.send_message(
            f"Weekly MVP will post on **{WEEKDAYS[day]}s at {hour:02d}:00 {timezone_name}**.", ephemeral=True
        )

    @mvp_schedule.autocomplete("timezone_name")
    async def _timezone_autocomplete(self, interaction: discord.Interaction,
                                     current: str) -> list[app_commands.Choice[str]]:
        needle = current.lower().replace(" ", "_")
        matches = sorted(tz for tz in available_timezones() if needle in tz.lower())
        return [app_commands.Choice(name=tz, value=tz) for tz in matches[:25]]

    @settings.command(name="modes", description="Which game modes get posted and count toward stats")
    @app_commands.describe(modes="Comma-separated, e.g. competitive, unrated, swiftplay. Use 'all' for everything.")
    async def modes(self, interaction: discord.Interaction, modes: str) -> None:
        assert interaction.guild_id is not None
        if modes.strip().lower() == "all":
            queues = tuple(QUEUE_NAMES)
        elif modes.strip().lower() == "default":
            queues = DEFAULT_QUEUES
        else:
            queues = tuple(dict.fromkeys(normalize_queue(m) for m in modes.split(",") if m.strip()))
            unknown = [q for q in queues if q not in QUEUE_NAMES]
            if unknown or not queues:
                known = ", ".join(f"`{k}`" for k in QUEUE_NAMES)
                await interaction.response.send_message(
                    f"Unknown mode(s): {', '.join(unknown) or '(none given)'}. Pick from {known}.", ephemeral=True
                )
                return
        await self.bot.db.update_config(interaction.guild_id, queues=queues)
        await interaction.response.send_message(
            f"Tracking: {', '.join(queue_name(q) for q in queues)}.", ephemeral=True
        )

    @settings.command(name="mvp-min-games", description="Games needed in a week to qualify for MVP")
    async def mvp_min_games(self, interaction: discord.Interaction, games: app_commands.Range[int, 1, 30]) -> None:
        assert interaction.guild_id is not None
        await self.bot.db.update_config(interaction.guild_id, min_games=games)
        await interaction.response.send_message(f"MVP now needs at least **{games}** games.", ephemeral=True)

    @settings.command(name="show", description="Show the current settings")
    async def show(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        config = await self.bot.db.get_config(interaction.guild_id)
        await interaction.response.send_message(embed=_describe(config), ephemeral=True)

    @app_commands.command(name="help", description="What ValoTrack can do")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="ValoTrack",
            description="Posts Valorant match results for linked players and crowns a weekly MVP.",
            color=VALORANT_RED,
        )
        embed.add_field(name="Getting started", inline=False, value=(
            "`/link Name#TAG`: start tracking your account\n"
            "`/settings feed-channel`: (admins) pick where results are posted"
        ))
        embed.add_field(name="Stats", inline=False, value=(
            "`/stats` · `/rank` · `/lastmatch` · `/compare` · `/leaderboard`"
        ))
        embed.add_field(name="MVP", inline=False, value=(
            "`/mvp standings`: this week's race so far\n"
            "`/mvp post`: (admins) post a recap right now"
        ))
        embed.add_field(name="Admin", inline=False, value=(
            "`/settings show` · `feed-channel` · `mvp-channel` · `mvp-schedule` · `modes` · `mvp-min-games`\n"
            "`/link` and `/unlink` also accept a member for managing other people"
        ))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: ValoTrackBot) -> None:
    await bot.add_cog(Settings(bot))
