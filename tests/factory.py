"""Builders for fake HenrikDev v4 match payloads."""

from __future__ import annotations

from typing import Any

AGENT_JETT = {"id": "add6443a-41bd-e414-f6ad-e58d267f4e95", "name": "Jett"}
MAP_ASCENT = {"id": "7eaecc1b-4337-bbf6-6ab9-04b8f06b3319", "name": "Ascent"}


def player(puuid: str, team: str, *, kills: int = 15, deaths: int = 15, assists: int = 5, score: int = 4000,
           dealt: int = 2900, headshots: int = 20, bodyshots: int = 60, legshots: int = 5,
           tier: tuple[int, str] = (18, "Diamond 1"), agent: dict | None = None) -> dict[str, Any]:
    return {
        "puuid": puuid,
        "name": puuid.capitalize(),
        "tag": "NA1",
        "team_id": team,
        "platform": "pc",
        "agent": agent or AGENT_JETT,
        "tier": {"id": tier[0], "name": tier[1]},
        "stats": {
            "score": score, "kills": kills, "deaths": deaths, "assists": assists,
            "headshots": headshots, "bodyshots": bodyshots, "legshots": legshots,
            "damage": {"dealt": dealt, "received": 2500},
        },
    }


def kill(round_no: int, t_ms: int, killer: str, killer_team: str, victim: str, victim_team: str) -> dict[str, Any]:
    return {
        "round": round_no,
        "time_in_round_in_ms": t_ms,
        "killer": {"puuid": killer, "team": killer_team},
        "victim": {"puuid": victim, "team": victim_team},
    }


def match(match_id: str = "match-1", *, queue: str = "competitive", started_at: str = "2026-09-20T18:00:00.000Z",
          red_rounds: int = 13, blue_rounds: int = 9, players: list[dict] | None = None,
          kills: list[dict] | None = None, length_ms: int = 2_400_000, teams: list[dict] | None = None,
          completed: bool = True) -> dict[str, Any]:
    if players is None:
        players = [player(f"red{i}", "Red") for i in range(5)] + [player(f"blue{i}", "Blue") for i in range(5)]
    total = red_rounds + blue_rounds
    if teams is None:
        teams = [
            {"team_id": "Red", "won": red_rounds > blue_rounds, "rounds": {"won": red_rounds, "lost": blue_rounds}},
            {"team_id": "Blue", "won": blue_rounds > red_rounds, "rounds": {"won": blue_rounds, "lost": red_rounds}},
        ]
    return {
        "metadata": {
            "match_id": match_id,
            "map": MAP_ASCENT,
            "game_length_in_ms": length_ms,
            "started_at": started_at,
            "is_completed": completed,
            "queue": {"id": queue, "name": queue.title(), "mode_type": "Standard"},
        },
        "players": players,
        "teams": teams,
        "rounds": [{"id": i, "winning_team": "Red"} for i in range(total)],
        "kills": kills or [],
    }
