"""SQLite persistence: linked accounts, stored matches, and per-server settings."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any, Iterable

import aiosqlite

from .stats import DEFAULT_QUEUES, MatchInfo, PlayerLine

MATCH_COLUMNS = (
    "match_id", "map_name", "map_id", "queue", "started_at", "length_ms", "rounds", "team_mode", "lobby_size",
)
LINE_COLUMNS = tuple(f.name for f in fields(PlayerLine))

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id        INTEGER PRIMARY KEY,
    feed_channel_id INTEGER,
    mvp_channel_id  INTEGER,
    mvp_weekday     INTEGER NOT NULL DEFAULT 0,   -- 0 = Monday
    mvp_hour        INTEGER NOT NULL DEFAULT 12,
    timezone        TEXT    NOT NULL DEFAULT 'UTC',
    queues          TEXT    NOT NULL DEFAULT '{",".join(DEFAULT_QUEUES)}',
    min_games       INTEGER NOT NULL DEFAULT 3,
    last_mvp_at     TEXT
);

CREATE TABLE IF NOT EXISTS players (
    puuid    TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    tag      TEXT NOT NULL,
    region   TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'pc',
    card     TEXT
);

CREATE TABLE IF NOT EXISTS links (
    guild_id   INTEGER NOT NULL,
    discord_id INTEGER NOT NULL,
    puuid      TEXT    NOT NULL REFERENCES players(puuid),
    linked_at  TEXT    NOT NULL,
    PRIMARY KEY (guild_id, discord_id)
);
CREATE INDEX IF NOT EXISTS links_puuid ON links(puuid);

CREATE TABLE IF NOT EXISTS matches (
    match_id   TEXT PRIMARY KEY,
    map_name   TEXT NOT NULL,
    map_id     TEXT NOT NULL,
    queue      TEXT NOT NULL,
    started_at TEXT NOT NULL,
    length_ms  INTEGER NOT NULL,
    rounds     INTEGER NOT NULL,
    team_mode  INTEGER NOT NULL,
    lobby_size INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS player_matches (
    match_id         TEXT    NOT NULL REFERENCES matches(match_id),
    puuid            TEXT    NOT NULL,
    name             TEXT    NOT NULL,
    tag              TEXT    NOT NULL,
    team_id          TEXT    NOT NULL,
    agent            TEXT    NOT NULL,
    agent_id         TEXT    NOT NULL,
    tier_id          INTEGER NOT NULL,
    tier_name        TEXT    NOT NULL,
    kills            INTEGER NOT NULL,
    deaths           INTEGER NOT NULL,
    assists          INTEGER NOT NULL,
    score            INTEGER NOT NULL,
    damage_dealt     INTEGER NOT NULL,
    damage_received  INTEGER NOT NULL,
    headshots        INTEGER NOT NULL,
    bodyshots        INTEGER NOT NULL,
    legshots         INTEGER NOT NULL,
    rounds           INTEGER NOT NULL,
    result           TEXT,
    team_rounds_won  INTEGER NOT NULL,
    team_rounds_lost INTEGER NOT NULL,
    placement        INTEGER NOT NULL,
    team_placement   INTEGER NOT NULL,
    first_bloods     INTEGER NOT NULL,
    first_deaths     INTEGER NOT NULL,
    aces             INTEGER NOT NULL,
    four_ks          INTEGER NOT NULL,
    rr_change        INTEGER,
    PRIMARY KEY (match_id, puuid)
);
CREATE INDEX IF NOT EXISTS player_matches_puuid ON player_matches(puuid);

CREATE TABLE IF NOT EXISTS announced (
    guild_id   INTEGER NOT NULL,
    match_id   TEXT    NOT NULL,
    message_id INTEGER,
    PRIMARY KEY (guild_id, match_id)
);
"""


def iso(dt: datetime) -> str:
    """Fixed-width UTC timestamps so string comparison in SQL matches time order."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Player:
    puuid: str
    name: str
    tag: str
    region: str
    platform: str = "pc"
    card: str | None = None

    @property
    def riot_id(self) -> str:
        return f"{self.name}#{self.tag}"


@dataclass
class Link:
    guild_id: int
    discord_id: int
    linked_at: datetime
    player: Player


@dataclass
class GuildConfig:
    guild_id: int
    feed_channel_id: int | None = None
    mvp_channel_id: int | None = None
    mvp_weekday: int = 0
    mvp_hour: int = 12
    timezone: str = "UTC"
    queues: tuple[str, ...] = DEFAULT_QUEUES
    min_games: int = 3
    last_mvp_at: datetime | None = None

    @property
    def recap_channel_id(self) -> int | None:
        return self.mvp_channel_id or self.feed_channel_id


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._conn

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- players & links -----------------------------------------------------------

    async def upsert_player(self, player: Player) -> None:
        await self.conn.execute(
            """INSERT INTO players (puuid, name, tag, region, platform, card) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(puuid) DO UPDATE SET name=excluded.name, tag=excluded.tag,
                   region=excluded.region, platform=excluded.platform, card=excluded.card""",
            (player.puuid, player.name, player.tag, player.region, player.platform, player.card),
        )
        await self.conn.commit()

    async def rename_player(self, puuid: str, name: str, tag: str) -> None:
        await self.conn.execute("UPDATE players SET name = ?, tag = ? WHERE puuid = ?", (name, tag, puuid))
        await self.conn.commit()

    async def link(self, guild_id: int, discord_id: int, puuid: str, when: datetime | None = None) -> None:
        await self.conn.execute(
            """INSERT INTO links (guild_id, discord_id, puuid, linked_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(guild_id, discord_id) DO UPDATE SET puuid=excluded.puuid, linked_at=excluded.linked_at""",
            (guild_id, discord_id, puuid, iso(when or datetime.now(timezone.utc))),
        )
        await self.conn.commit()

    async def unlink(self, guild_id: int, discord_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM links WHERE guild_id = ? AND discord_id = ?", (guild_id, discord_id))
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_link(self, guild_id: int, discord_id: int) -> Link | None:
        links = await self._links("WHERE l.guild_id = ? AND l.discord_id = ?", (guild_id, discord_id))
        return links[0] if links else None

    async def guild_links(self, guild_id: int) -> list[Link]:
        return await self._links("WHERE l.guild_id = ? ORDER BY p.name COLLATE NOCASE", (guild_id,))

    async def links_for_puuids(self, puuids: Iterable[str]) -> list[Link]:
        puuids = list(puuids)
        if not puuids:
            return []
        marks = ",".join("?" * len(puuids))
        return await self._links(f"WHERE l.puuid IN ({marks})", puuids)

    async def tracked_players(self) -> list[Player]:
        async with self.conn.execute(
            "SELECT DISTINCT p.* FROM players p JOIN links l ON l.puuid = p.puuid ORDER BY p.name"
        ) as cur:
            return [_player(row) for row in await cur.fetchall()]

    async def _links(self, where: str, params: Iterable[Any]) -> list[Link]:
        async with self.conn.execute(
            f"""SELECT l.guild_id, l.discord_id, l.linked_at, p.*
                FROM links l JOIN players p ON p.puuid = l.puuid {where}""",
            tuple(params),
        ) as cur:
            return [
                Link(row["guild_id"], row["discord_id"], from_iso(row["linked_at"]), _player(row))
                for row in await cur.fetchall()
            ]

    # -- matches -------------------------------------------------------------------

    async def has_player_match(self, match_id: str, puuid: str) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM player_matches WHERE match_id = ? AND puuid = ?", (match_id, puuid)
        ) as cur:
            return await cur.fetchone() is not None

    async def save_match(self, match: MatchInfo, lines: Iterable[PlayerLine]) -> None:
        await self.conn.execute(
            f"INSERT OR IGNORE INTO matches ({', '.join(MATCH_COLUMNS)}) VALUES ({', '.join('?' * len(MATCH_COLUMNS))})",
            (match.match_id, match.map_name, match.map_id, match.queue, iso(match.started_at), match.length_ms,
             match.rounds, int(match.team_mode), match.lobby_size),
        )
        await self.conn.executemany(
            f"INSERT OR IGNORE INTO player_matches (match_id, {', '.join(LINE_COLUMNS)}) "
            f"VALUES (?, {', '.join('?' * len(LINE_COLUMNS))})",
            [(match.match_id, *(getattr(line, c) for c in LINE_COLUMNS)) for line in lines],
        )
        await self.conn.commit()

    async def history(
        self,
        puuid: str,
        since: datetime | None = None,
        until: datetime | None = None,
        queues: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[tuple[MatchInfo, PlayerLine]]:
        """A player's stored games, newest first."""
        clauses, params = ["pm.puuid = ?"], [puuid]
        if since is not None:
            clauses.append("m.started_at >= ?")
            params.append(iso(since))
        if until is not None:
            clauses.append("m.started_at < ?")
            params.append(iso(until))
        if queues is not None:
            queues = list(queues)
            clauses.append(f"m.queue IN ({','.join('?' * len(queues))})")
            params.extend(queues)
        sql = f"{_JOINED_SELECT} WHERE {' AND '.join(clauses)} ORDER BY m.started_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        async with self.conn.execute(sql, params) as cur:
            return [_joined(row) for row in await cur.fetchall()]

    async def match_lines(self, match_id: str, puuids: Iterable[str]) -> tuple[MatchInfo, list[PlayerLine]] | None:
        puuids = list(puuids)
        if not puuids:
            return None
        sql = f"{_JOINED_SELECT} WHERE m.match_id = ? AND pm.puuid IN ({','.join('?' * len(puuids))})"
        async with self.conn.execute(sql, (match_id, *puuids)) as cur:
            rows = [_joined(row) for row in await cur.fetchall()]
        if not rows:
            return None
        return rows[0][0], [line for _, line in rows]

    # -- announcements ----------------------------------------------------------------

    async def is_announced(self, guild_id: int, match_id: str) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM announced WHERE guild_id = ? AND match_id = ?", (guild_id, match_id)
        ) as cur:
            return await cur.fetchone() is not None

    async def mark_announced(self, guild_id: int, match_id: str, message_id: int | None) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO announced (guild_id, match_id, message_id) VALUES (?, ?, ?)",
            (guild_id, match_id, message_id),
        )
        await self.conn.commit()

    # -- guild config ------------------------------------------------------------------

    async def get_config(self, guild_id: int) -> GuildConfig:
        async with self.conn.execute("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)) as cur:
            row = await cur.fetchone()
        return _config(row) if row else GuildConfig(guild_id)

    async def all_configs(self) -> list[GuildConfig]:
        async with self.conn.execute("SELECT * FROM guild_config") as cur:
            return [_config(row) for row in await cur.fetchall()]

    async def update_config(self, guild_id: int, **values: Any) -> GuildConfig:
        allowed = {f.name for f in fields(GuildConfig)} - {"guild_id"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown config fields: {unknown}")
        if "queues" in values:
            values["queues"] = ",".join(values["queues"])
        if isinstance(values.get("last_mvp_at"), datetime):
            values["last_mvp_at"] = iso(values["last_mvp_at"])
        await self.conn.execute("INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,))
        if values:
            assignments = ", ".join(f"{key} = ?" for key in values)
            await self.conn.execute(
                f"UPDATE guild_config SET {assignments} WHERE guild_id = ?", (*values.values(), guild_id)
            )
        await self.conn.commit()
        return await self.get_config(guild_id)


_JOINED_SELECT = (
    "SELECT "
    + ", ".join(f"m.{c} AS m_{c}" for c in MATCH_COLUMNS)
    + ", "
    + ", ".join(f"pm.{c} AS p_{c}" for c in LINE_COLUMNS)
    + " FROM player_matches pm JOIN matches m ON m.match_id = pm.match_id"
)


def _joined(row: aiosqlite.Row) -> tuple[MatchInfo, PlayerLine]:
    match = MatchInfo(
        match_id=row["m_match_id"],
        map_name=row["m_map_name"],
        map_id=row["m_map_id"],
        queue=row["m_queue"],
        started_at=from_iso(row["m_started_at"]),
        length_ms=row["m_length_ms"],
        rounds=row["m_rounds"],
        team_mode=bool(row["m_team_mode"]),
        lobby_size=row["m_lobby_size"],
    )
    line = PlayerLine(**{c: row[f"p_{c}"] for c in LINE_COLUMNS})
    return match, line


def _player(row: aiosqlite.Row) -> Player:
    return Player(row["puuid"], row["name"], row["tag"], row["region"], row["platform"], row["card"])


def _config(row: aiosqlite.Row) -> GuildConfig:
    return GuildConfig(
        guild_id=row["guild_id"],
        feed_channel_id=row["feed_channel_id"],
        mvp_channel_id=row["mvp_channel_id"],
        mvp_weekday=row["mvp_weekday"],
        mvp_hour=row["mvp_hour"],
        timezone=row["timezone"],
        queues=tuple(q for q in row["queues"].split(",") if q),
        min_games=row["min_games"],
        last_mvp_at=from_iso(row["last_mvp_at"]) if row["last_mvp_at"] else None,
    )
