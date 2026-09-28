from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from tests import factory
from valotrack.stats import aggregate, current_streak, normalize_queue, parse_match, parse_riot_id
from valotrack.weekly import last_scheduled, pick_awards, rank_by_rating


def test_parse_basic_match():
    players = [factory.player("me", "Red", kills=24, deaths=12, score=6600, dealt=4400, headshots=30,
                              bodyshots=60, legshots=10)]
    players += [factory.player(f"red{i}", "Red") for i in range(4)]
    players += [factory.player(f"blue{i}", "Blue", score=3000 + i) for i in range(5)]
    m = parse_match(factory.match(players=players))

    assert m.match_id == "match-1"
    assert m.map_name == "Ascent"
    assert m.queue == "competitive"
    assert m.rounds == 22
    assert m.team_mode and m.lobby_size == 10
    assert m.started_at == datetime(2026, 9, 20, 18, tzinfo=timezone.utc)

    me = next(p for p in m.players if p.puuid == "me")
    assert me.result == "win"
    assert (me.team_rounds_won, me.team_rounds_lost) == (13, 9)
    assert me.acs == pytest.approx(300)
    assert me.adr == pytest.approx(200)
    assert me.kd == pytest.approx(2.0)
    assert me.hs_pct == pytest.approx(30)
    assert me.placement == 1 and me.team_placement == 1

    blue_top = next(p for p in m.players if p.puuid == "blue4")
    assert blue_top.result == "loss"
    assert blue_top.team_placement == 1 and blue_top.placement > 1


def test_draw_and_no_team_modes():
    draw = factory.match(red_rounds=12, blue_rounds=12, teams=[
        {"team_id": "Red", "won": False, "rounds": {"won": 12, "lost": 12}},
        {"team_id": "Blue", "won": False, "rounds": {"won": 12, "lost": 12}},
    ])
    assert {p.result for p in parse_match(draw).players} == {"draw"}

    dm_players = [factory.player(f"p{i}", f"p{i}", score=100 * i) for i in range(4)]
    dm = parse_match(factory.match(queue="deathmatch", players=dm_players, teams=[], red_rounds=1, blue_rounds=0))
    assert not dm.team_mode
    assert {p.result for p in dm.players} == {None}
    assert [p.placement for p in sorted(dm.players, key=lambda p: p.puuid)] == [4, 3, 2, 1]


def test_first_bloods_and_aces():
    kills = [
        # Round 0: red0 opens and aces.
        factory.kill(0, 5000, "red0", "Red", "blue0", "Blue"),
        factory.kill(0, 9000, "red0", "Red", "blue1", "Blue"),
        factory.kill(0, 12000, "blue2", "Blue", "red1", "Red"),
        factory.kill(0, 15000, "red0", "Red", "blue2", "Blue"),
        factory.kill(0, 20000, "red0", "Red", "blue3", "Blue"),
        factory.kill(0, 25000, "red0", "Red", "blue4", "Blue"),
        # Round 1: a team kill happens first and must not count as first blood.
        factory.kill(1, 1000, "blue1", "Blue", "blue0", "Blue"),
        factory.kill(1, 3000, "blue1", "Blue", "red2", "Red"),
        factory.kill(1, 4000, "red3", "Red", "blue1", "Blue"),
        factory.kill(1, 5000, "red3", "Red", "blue2", "Blue"),
        factory.kill(1, 6000, "red3", "Red", "blue3", "Blue"),
        factory.kill(1, 7000, "red3", "Red", "blue4", "Blue"),
    ]
    m = parse_match(factory.match(kills=kills))
    by = {p.puuid: p for p in m.players}
    assert by["red0"].first_bloods == 1 and by["red0"].aces == 1
    assert by["blue0"].first_deaths == 1
    assert by["blue1"].first_bloods == 1 and by["red2"].first_deaths == 1
    assert by["red3"].four_ks == 1 and by["red3"].aces == 0


def test_aggregate_and_rating():
    games = []
    for i, (won, kills) in enumerate([(True, 25), (True, 20), (False, 10)]):
        players = [factory.player("me", "Red", kills=kills, deaths=15, score=kills * 250)]
        players += [factory.player(f"x{j}", "Blue") for j in range(5)]
        m = parse_match(factory.match(f"m{i}", players=players,
                                      red_rounds=13 if won else 5, blue_rounds=5 if won else 13))
        games.append((m, next(p for p in m.players if p.puuid == "me")))
    agg = aggregate("me", games)
    assert agg.games == 3 and agg.wins == 2 and agg.losses == 1
    assert agg.kills == 55
    assert agg.kd == pytest.approx(55 / 45)
    assert agg.acs == pytest.approx(55 * 250 / 54)
    assert agg.agents["Jett"] == 3
    assert 0.5 < agg.rating < 2.5


def test_rating_prefers_better_player_and_shrinks_small_samples():
    def agg_for(puuid, n, kills):
        rows = []
        for i in range(n):
            players = [factory.player(puuid, "Red", kills=kills, deaths=14, score=kills * 260, dealt=kills * 180)]
            m = parse_match(factory.match(f"{puuid}{i}", players=players + [factory.player("o", "Blue")]))
            rows.append((m, m.players[0]))
        return aggregate(puuid, rows)

    star, average, one_hit_wonder = agg_for("star", 6, 25), agg_for("avg", 6, 14), agg_for("lucky", 1, 25)
    assert star.rating > average.rating
    assert star.rating > one_hit_wonder.rating  # same per-game stats, fewer games -> pulled toward 1.0
    assert rank_by_rating([average, star, one_hit_wonder], min_games=3) == [star, average]

    titles = {a.title: a.puuid for a in pick_awards([star, average, one_hit_wonder], min_games=3)}
    assert titles["Grinder"] in ("star", "avg")
    assert titles["Headshot Machine"] in ("star", "avg")
    assert "Rough Week" not in titles  # needs 3 qualified players


def test_streaks():
    assert current_streak(["win", "win", "win", "loss"]) == ("win", 3)
    assert current_streak(["loss", "win"]) == ("loss", 1)
    assert current_streak(["draw", "win"]) == (None, 0)
    assert current_streak([]) == (None, 0)


def test_last_scheduled_handles_timezones():
    tz = ZoneInfo("America/New_York")
    # Sunday 2026-09-27 15:00 UTC = 11:00 EDT. Schedule: Mondays 12:00 local.
    now = datetime(2026, 9, 27, 15, tzinfo=timezone.utc)
    due = last_scheduled(now, weekday=0, hour=12, tz=tz)
    assert due == datetime(2026, 9, 21, 16, tzinfo=timezone.utc)  # Mon 12:00 EDT
    # Exactly at the scheduled time counts as due.
    assert last_scheduled(due, weekday=0, hour=12, tz=tz) == due
    # Across the DST change (Nov 1 2026), noon local stays noon local.
    later = datetime(2026, 11, 3, 0, tzinfo=timezone.utc)
    assert last_scheduled(later, weekday=0, hour=12, tz=tz) == datetime(2026, 11, 2, 17, tzinfo=timezone.utc)


def test_riot_id_and_queue_parsing():
    assert parse_riot_id("  Some Name #NA1 ") == ("Some Name", "NA1")
    assert parse_riot_id("we#ird#TAG") == ("we#ird", "TAG")
    for bad in ("noTag", "#TAG", "Name#"):
        with pytest.raises(ValueError):
            parse_riot_id(bad)
    assert normalize_queue("Competitive") == "competitive"
    assert normalize_queue("teamdeathmatch") == "hurm"
    assert normalize_queue("") == "custom"
