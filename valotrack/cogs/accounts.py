"""Linking Discord users to Riot accounts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..embeds import VALORANT_RED, linked_embed
from ..henrik import HenrikError, NotFound

if TYPE_CHECKING:
    from ..bot import ValoTrackBot


def _can_manage(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)


class Accounts(commands.Cog):
    def __init__(self, bot: ValoTrackBot) -> None:
        self.bot = bot

    @app_commands.command(name="link", description="Link a Riot account so ValoTrack posts its matches")
    @app_commands.guild_only()
    @app_commands.describe(riot_id="Riot ID, e.g. Name#TAG", member="Link someone else (needs Manage Server)")
    async def link(self, interaction: discord.Interaction, riot_id: str, member: discord.Member | None = None) -> None:
        assert interaction.guild_id is not None
        target = member or interaction.user
        if target.id != interaction.user.id and not _can_manage(interaction):
            await interaction.response.send_message(
                "You need **Manage Server** to link accounts for other people.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        try:
            player = await self.bot.tracker.link_account(interaction.guild_id, target.id, riot_id)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except NotFound:
            await interaction.followup.send(f"Couldn't find a Riot account called `{riot_id}`.", ephemeral=True)
            return
        except HenrikError as exc:
            await interaction.followup.send(f"The Valorant API didn't cooperate ({exc.message}). Try again soon.",
                                            ephemeral=True)
            return

        try:
            imported = await self.bot.tracker.backfill(player)
        except HenrikError:
            imported = 0

        embed = linked_embed(player, target.id, imported)
        config = await self.bot.db.get_config(interaction.guild_id)
        if not config.feed_channel_id:
            embed.add_field(
                name="Almost there",
                value="An admin still needs to pick a channel with `/settings feed-channel`.",
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="unlink", description="Stop tracking a Riot account in this server")
    @app_commands.guild_only()
    @app_commands.describe(member="Unlink someone else (needs Manage Server)")
    async def unlink(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        assert interaction.guild_id is not None
        target = member or interaction.user
        if target.id != interaction.user.id and not _can_manage(interaction):
            await interaction.response.send_message(
                "You need **Manage Server** to unlink other people.", ephemeral=True
            )
            return
        removed = await self.bot.db.unlink(interaction.guild_id, target.id)
        message = f"Unlinked {target.mention}." if removed else f"{target.mention} wasn't linked."
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="tracked", description="List everyone ValoTrack follows in this server")
    @app_commands.guild_only()
    async def tracked(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        links = await self.bot.db.guild_links(interaction.guild_id)
        if not links:
            await interaction.response.send_message("Nobody is linked yet. Use `/link Name#TAG`.", ephemeral=True)
            return
        lines = [
            f"<@{link.discord_id}> → **{link.player.riot_id}** ({link.player.region.upper()})" for link in links
        ]
        embed = discord.Embed(title=f"Tracked players ({len(links)})", description="\n".join(lines),
                              color=VALORANT_RED)
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: ValoTrackBot) -> None:
    await bot.add_cog(Accounts(bot))
