"""Core tracking logic: linking accounts, finding new matches, deciding where to announce.

Kept free of Discord objects so it can be tested with a fake API and a temp database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .db import Database, GuildConfig, Player
from .henrik import HenrikClient, HenrikError
from .stats import MatchInfo, PlayerLine, current_streak, parse_match, parse_riot_id, parse_timestamp

log = logging.getLogger(__name__)

MATCHES_PER_POLL = 5
BACKFILL_SIZE = 10
# Don't post matches that finished longer ago than this (e.g. after the bot was offline).
MAX_ANNOUNCE_AGE = timedelta(hours=12)
REGIONS = ("na", "eu", "ap", "kr", "latam", "br")

NewMatch = tuple[MatchInfo, list[PlayerLine]]


@dataclass
class Announcement:
    guild_id: int
    channel_id: int
    match: MatchInfo
    lines: list[PlayerLine]
    owners: dict[str, int]  # puuid -> discord user id
    streaks: dict[str, tuple[str | None, int]]


def _platform(platforms: Iterable[str] | None) -> str:
    values = [p.lower() for p in platforms or []]
    return "pc" if not values or "pc" in values else "console"


class Tracker:
    def __init__(self, api: HenrikClient, db: Database) -> None:
        self.api = api
        self.db = db

    async def link_account(self, guild_id: int, discord_id: int, riot_id: str) -> Player:
        name, tag = parse_riot_id(riot_id)
        account = await self.api.get_account(name, tag)
        region = (account.get("region") or "").lower()
        if region not in REGIONS:
            raise ValueError(f"That account's region (`{region or 'unknown'}`) isn't supported by the match API.")
        player = Player(
            puuid=account["puuid"],
            name=account.get("name") or name,
            tag=account.get("tag") or tag,
            region=region,
            platform=_platform(account.get("platforms")),
            card=account.get("card"),
        )
        await self.db.upsert_player(player)
        await self.db.link(guild_id, discord_id, player.puuid)
        return player

    async def backfill(self, player: Player) -> int:
        """Import recent matches for a newly linked player so stats aren't empty.

        These finished before the link was made, so `announcements_for` skips them.
        """
        tracked = {p.puuid for p in await self.db.tracked_players()} | {player.puuid}
        return len(await self.poll_player(player, tracked, size=BACKFILL_SIZE))

    async def poll_player(self, player: Player, tracked: set[str], size: int = MATCHES_PER_POLL) -> list[NewMatch]:
        """Fetch a player's recent matches and store any we haven't seen. Returns the new ones, oldest first."""
        raw_matches = await self.api.get_matches(player.region, player.platform, player.puuid, size=size)
        raw_matches.sort(key=lambda m: parse_timestamp((m.get("metadata") or {}).get("started_at")))

        rr_cache: dict[str, dict[str, dict]] = {}
        new: list[NewMatch] = []
        for raw in raw_matches:
            meta = raw.get("metadata") or {}
            match_id = meta.get("match_id")
            if not match_id or meta.get("is_completed") is False:
                continue
            if await self.db.has_player_match(match_id, player.puuid):
                continue

            match = parse_match(raw)
            lines = [
                line for line in match.players
                if line.puuid in tracked and not await self.db.has_player_match(match_id, line.puuid)
            ]
            if not lines:
                continue
            if match.queue == "competitive":
                await self._attach_rr(match_id, lines, player, rr_cache)
            await self.db.save_match(match, lines)
            for line in lines:
                await self.db.rename_player(line.puuid, line.name, line.tag)
            new.append((match, lines))
        return new

    async def _attach_rr(self, match_id: str, lines: list[PlayerLine], player: Player,
                         cache: dict[str, dict[str, dict]]) -> None:
        # Everyone in a match shares a region/platform, so the polled player's work for all.
        for line in lines:
            if line.puuid not in cache:
                try:
                    history = await self.api.get_mmr_history(player.region, player.platform, line.puuid)
                except HenrikError as exc:
                    log.warning("Couldn't fetch MMR history for %s: %s", line.riot_id, exc)
                    history = []
                cache[line.puuid] = {h.get("match_id"): h for h in history}
            entry = cache[line.puuid].get(match_id)
            if entry is not None and entry.get("last_change") is not None:
                line.rr_change = int(entry["last_change"])

    async def announcements_for(self, match: MatchInfo, lines: list[PlayerLine],
                                now: datetime | None = None) -> list[Announcement]:
        """Work out which servers should hear about a newly stored match."""
        now = now or datetime.now(timezone.utc)
        if now - match.ended_at > MAX_ANNOUNCE_AGE:
            return []

        by_guild: dict[int, dict[str, int]] = {}
        for link in await self.db.links_for_puuids(line.puuid for line in lines):
            # Only announce games that finished after the account was linked in that server.
            if link.linked_at <= match.ended_at:
                by_guild.setdefault(link.guild_id, {})[link.player.puuid] = link.discord_id

        out = []
        for guild_id, owners in by_guild.items():
            config = await self.db.get_config(guild_id)
            if not config.feed_channel_id or match.queue not in config.queues:
                continue
            if await self.db.is_announced(guild_id, match.match_id):
                continue
            guild_lines = [line for line in lines if line.puuid in owners]
            streaks = {line.puuid: await self.streak(line.puuid, config) for line in guild_lines}
            out.append(Announcement(guild_id, config.feed_channel_id, match, guild_lines, owners, streaks))
        return out

    async def streak(self, puuid: str, config: GuildConfig) -> tuple[str | None, int]:
        queues = [q for q in config.queues if q not in ("deathmatch", "custom")]
        history = await self.db.history(puuid, queues=queues, limit=20)
        return current_streak(line.result for _, line in history)
