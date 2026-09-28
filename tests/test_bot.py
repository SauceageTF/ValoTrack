"""Smoke tests: the bot loads every cog, registers its commands, and renders embeds."""

import asyncio
from datetime import datetime, timedelta, timezone

import discord

from tests import factory
from valotrack.bot import EXTENSIONS, ValoTrackBot
from valotrack.config import Settings
from valotrack.embeds import leaderboard_embed, match_embed, rank_embed, stats_embed, weekly_embed
from valotrack.db import Player
from valotrack.stats import aggregate, parse_match
from valotrack.weekly import pick_awards, rank_by_rating


def test_all_cogs_load_and_register_commands(tmp_path):
    async def scenario():
        bot = ValoTrackBot(Settings("token", "key", database_path=str(tmp_path / "bot.db")))
        await bot.db.connect()
        for ext in EXTENSIONS:
            await bot.load_extension(ext)
        # Stop background loops the cogs started; there's no gateway connection here.
        for cog in list(bot.cogs.values()):
            await cog.cog_unload()
        names = {cmd.qualified_name for cmd in bot.tree.walk_commands()}
        payloads = [cmd.to_dict(bot.tree) for cmd in bot.tree.get_commands()]
        await bot.db.close()
        await bot.api.close()
        return names, payloads

    names, payloads = asyncio.run(scenario())
    expected = {
        "link", "unlink", "tracked", "lastmatch", "stats", "compare", "leaderboard", "rank", "help",
        "mvp standings", "mvp post",
        "settings feed-channel", "settings mvp-channel", "settings mvp-schedule", "settings modes",
        "settings mvp-min-games", "settings show",
    }
    assert expected <= names
    settings = next(p for p in payloads if p["name"] == "settings")
    assert int(settings["default_member_permissions"]) == discord.Permissions(manage_guild=True).value
    link = next(p for p in payloads if p["name"] == "link")
    assert link.get("contexts") == [0] or link.get("dm_permission") is False


def test_embeds_render_within_discord_limits():
    now = datetime.now(timezone.utc)
    players = [factory.player("me", "Red", kills=31, deaths=9, score=9000), factory.player("pal", "Blue")]
    players += [factory.player(f"x{i}", "Red") for i in range(4)] + [factory.player(f"y{i}", "Blue") for i in range(4)]
    kills = [factory.kill(0, 1000 * i, "me", "Red", f"y{i % 4}", "Blue") for i in range(5)]
    match = parse_match(factory.match(players=players, kills=kills, started_at=now.isoformat()))
    lines = [p for p in match.players if p.puuid in ("me", "pal")]
    lines[0].rr_change = 24

    embeds = [
        match_embed(match, lines, {"me": 1, "pal": 2}, {"me": ("win", 4)}),  # civil war
        match_embed(match, lines[:1], {"me": 1}),
    ]
    agg = aggregate("me", [(match, lines[0])])
    me = Player("me", "Me", "NA1", "na", card="card")
    embeds.append(stats_embed(me, 1, agg, "Last 7 days", "Competitive"))
    embeds.append(stats_embed(me, 1, aggregate("me", []), "Last 7 days", "Competitive"))
    embeds.append(leaderboard_embed([(1, me, agg)], "rating", "Last 7 days", 3))
    embeds.append(rank_embed(me, 1, {"current": {"tier": {"id": 21, "name": "Ascendant 1"}, "rr": 40,
                                                  "last_change": 18, "games_needed_for_rating": 0}}))
    embeds.append(weekly_embed(now - timedelta(days=7), now, rank_by_rating([agg], 1), pick_awards([agg], 1),
                               {"me": 1}, 1))

    assert embeds[0].title.startswith("CIVIL WAR")
    assert embeds[1].title == "VICTORY · Ascent"
    assert "🃏 1 ACE" in embeds[1].fields[0].value
    assert "+24 RR" in embeds[1].fields[0].value
    for embed in embeds:
        assert len(embed) <= 6000
        assert all(len(f.value) <= 1024 and len(f.name) <= 256 for f in embed.fields)
