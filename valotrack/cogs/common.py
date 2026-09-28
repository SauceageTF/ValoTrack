"""Helpers shared by the command cogs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from ..db import GuildConfig, Link
from ..stats import QUEUE_NAMES, queue_name

if TYPE_CHECKING:
    from ..bot import ValoTrackBot

PERIODS = {
    "7d": ("Last 7 days", timedelta(days=7)),
    "30d": ("Last 30 days", timedelta(days=30)),
    "all": ("All time", None),
}
PERIOD_CHOICES = [app_commands.Choice(name=label, value=key) for key, (label, _) in PERIODS.items()]
MODE_CHOICES = [app_commands.Choice(name=name, value=key) for key, name in QUEUE_NAMES.items()]


def period_window(key: str | None) -> tuple[str, datetime | None]:
    label, span = PERIODS[key or "7d"]
    return label, (datetime.now(timezone.utc) - span) if span else None


def mode_filter(config: GuildConfig, mode: str | None) -> tuple[list[str], str]:
    if mode:
        return [mode], queue_name(mode)
    return list(config.queues), " + ".join(queue_name(q) for q in config.queues)


async def resolve_link(bot: ValoTrackBot, interaction: discord.Interaction,
                       member: discord.abc.User | None) -> Link | None:
    """Look up who a command is about, replying with a hint if they aren't linked."""
    assert interaction.guild_id is not None
    target = member or interaction.user
    link = await bot.db.get_link(interaction.guild_id, target.id)
    if link is None:
        who = "You haven't" if target.id == interaction.user.id else f"{target.mention} hasn't"
        message = f"{who} linked a Riot account yet. Use `/link` first."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    return link
