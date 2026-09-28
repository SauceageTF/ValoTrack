"""Tracker + Database behaviour against a real temp SQLite file and a fake API."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from tests import factory
from valotrack.db import Database
from valotrack.henrik import HenrikError, NotFound
from valotrack.tracker import Tracker

GUILD_A, GUILD_B = 111, 222
ALICE, BOB = 1, 2


class FakeApi:
    def __init__(self):
        self.accounts = {
            ("Alice", "NA1"): {"puuid": "red0", "name": "Alice", "tag": "NA1", "region": "na",
                               "platforms": ["PC"], "card": "card-1"},
            ("Bob", "NA1"): {"puuid": "blue0", "name": "Bob", "tag": "NA1", "region": "na", "platforms": ["PC"]},
        }
        self.matches: dict[str, list[dict]] = {}
        self.mmr_history: dict[str, list[dict]] = {}
        self.calls: list[str] = []

    async def get_account(self, name, tag):
        self.calls.append("account")
        try:
            return self.accounts[(name, tag)]
        except KeyError:
            raise NotFound(404, "not found") from None

    async def get_matches(self, region, platform, puuid, size=5):
        self.calls.append(f"matches:{puuid}")
        return [m for m in self.matches.get(puuid, []) if any(p["puuid"] == puuid for p in m["players"])][:size]

    async def get_mmr_history(self, region, platform, puuid):
        self.calls.append(f"mmr:{puuid}")
        if puuid == "broken":
            raise HenrikError(500, "boom")
        return self.mmr_history.get(puuid, [])


def run(coro):
    return asyncio.run(coro)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def env(tmp_path):
    async def make():
        db = Database(str(tmp_path / "test.db"))
        await db.connect()
        return db
    db = run(make())
    api = FakeApi()
    yield api, db, Tracker(api, db)
    run(db.close())


def test_link_backfill_does_not_announce_old_games(env):
    api, db, tracker = env
    old = factory.match("old", started_at=iso(datetime.now(timezone.utc) - timedelta(hours=3)))
    api.matches["red0"] = [old]

    async def scenario():
        await db.update_config(GUILD_A, feed_channel_id=999)
        player = await tracker.link_account(GUILD_A, ALICE, "Alice#NA1")
        assert player.platform == "pc" and player.region == "na"
        assert await tracker.backfill(player) == 1
        stored = await db.history("red0")
        assert [m.match_id for m, _ in stored] == ["old"]
        # The game ended before the link, so nobody hears about it.
        match, lines = await db.match_lines("old", ["red0"])
        assert await tracker.announcements_for(match, lines) == []
    run(scenario())


def test_new_match_announced_once_per_guild_with_rr(env):
    api, db, tracker = env
    now = datetime.now(timezone.utc)

    async def scenario():
        await db.update_config(GUILD_A, feed_channel_id=900)
        await db.update_config(GUILD_B, feed_channel_id=901, queues=("unrated",))
        alice = await tracker.link_account(GUILD_A, ALICE, "Alice#NA1")
        bob = await tracker.link_account(GUILD_A, BOB, "Bob#NA1")
        await tracker.link_account(GUILD_B, ALICE, "Alice#NA1")
        # Everyone linked yesterday, well before the game.
        for guild, user, puuid in ((GUILD_A, ALICE, "red0"), (GUILD_A, BOB, "blue0"), (GUILD_B, ALICE, "red0")):
            await db.link(guild, user, puuid, when=now - timedelta(days=1))

        # Alice and Bob were on opposite teams in a fresh competitive game.
        fresh = factory.match("fresh", started_at=iso(now - timedelta(minutes=45)), length_ms=35 * 60_000)
        api.matches["red0"] = [fresh]
        api.matches["blue0"] = [fresh]
        api.mmr_history["red0"] = [{"match_id": "fresh", "last_change": 21}]
        api.mmr_history["blue0"] = [{"match_id": "fresh", "last_change": -17}]

        tracked = {p.puuid for p in await db.tracked_players()}
        new = await tracker.poll_player(alice, tracked)
        assert len(new) == 1
        match, lines = new[0]
        assert {l.puuid: l.rr_change for l in lines} == {"red0": 21, "blue0": -17}

        announcements = await tracker.announcements_for(match, lines)
        # Guild B only posts unrated, so only guild A gets it, with both players.
        assert [a.guild_id for a in announcements] == [GUILD_A]
        assert {l.puuid for l in announcements[0].lines} == {"red0", "blue0"}
        assert announcements[0].owners == {"red0": ALICE, "blue0": BOB}

        await db.mark_announced(GUILD_A, "fresh", 12345)
        assert await tracker.announcements_for(match, lines) == []
        # Polling Bob afterwards finds nothing new: the game was stored for both.
        assert await tracker.poll_player(bob, tracked) == []
    run(scenario())


def test_stale_and_incomplete_matches(env):
    api, db, tracker = env
    now = datetime.now(timezone.utc)

    async def scenario():
        await db.update_config(GUILD_A, feed_channel_id=900)
        # Pretend Alice linked a long time ago.
        alice = await tracker.link_account(GUILD_A, ALICE, "Alice#NA1")
        await db.link(GUILD_A, ALICE, alice.puuid, when=now - timedelta(days=30))
        api.matches["red0"] = [
            factory.match("ancient", started_at=iso(now - timedelta(days=2))),
            factory.match("live", started_at=iso(now - timedelta(minutes=10)), completed=False),
        ]
        new = await tracker.poll_player(alice, {"red0"})
        assert [m.match_id for m, _ in new] == ["ancient"]
        # Too old to be worth posting (bot was probably offline), but still stored for stats.
        assert await tracker.announcements_for(*new[0]) == []
    run(scenario())


def test_link_errors(env):
    _, _, tracker = env
    with pytest.raises(NotFound):
        run(tracker.link_account(GUILD_A, ALICE, "Nobody#000"))
    with pytest.raises(ValueError):
        run(tracker.link_account(GUILD_A, ALICE, "missing-tag"))


def test_config_roundtrip_and_history_filters(env):
    api, db, tracker = env
    now = datetime.now(timezone.utc)

    async def scenario():
        config = await db.get_config(GUILD_A)
        assert config.feed_channel_id is None and "competitive" in config.queues
        updated = await db.update_config(GUILD_A, mvp_weekday=4, mvp_hour=20, timezone="Europe/London",
                                         last_mvp_at=now, queues=("competitive", "swiftplay"))
        assert (updated.mvp_weekday, updated.mvp_hour, updated.timezone) == (4, 20, "Europe/London")
        assert updated.queues == ("competitive", "swiftplay")
        assert abs((updated.last_mvp_at - now).total_seconds()) < 1

        alice = await tracker.link_account(GUILD_A, ALICE, "Alice#NA1")
        api.matches["red0"] = [
            factory.match("a", started_at=iso(now - timedelta(days=10))),
            factory.match("b", started_at=iso(now - timedelta(days=2)), queue="unrated"),
            factory.match("c", started_at=iso(now - timedelta(hours=5))),
        ]
        await tracker.poll_player(alice, {"red0"})
        everything = await db.history("red0")
        assert [m.match_id for m, _ in everything] == ["c", "b", "a"]
        week = await db.history("red0", since=now - timedelta(days=7), queues=["competitive"])
        assert [m.match_id for m, _ in week] == ["c"]
        window = await db.history("red0", since=now - timedelta(days=11), until=now - timedelta(days=1))
        assert [m.match_id for m, _ in window] == ["b", "a"]
        # Round-trips through SQLite intact.
        match, line = everything[0]
        assert match.team_mode and match.lobby_size == 10 and match.map_id
        assert line.result == "win" and line.rounds == 22
        assert await db.unlink(GUILD_A, ALICE) and not await db.unlink(GUILD_A, ALICE)
        assert await db.tracked_players() == []
    run(scenario())


def test_mmr_failure_does_not_block_match(env):
    api, db, tracker = env
    api.accounts[("Broken", "NA1")] = {"puuid": "broken", "name": "Broken", "tag": "NA1", "region": "na"}
    players = [factory.player("broken", "Red")] + [factory.player(f"b{i}", "Blue") for i in range(5)]
    api.matches["broken"] = [factory.match("m", players=players)]

    async def scenario():
        p = await tracker.link_account(GUILD_A, ALICE, "Broken#NA1")
        new = await tracker.poll_player(p, {"broken"})
        assert new[0][1][0].rr_change is None
    run(scenario())
