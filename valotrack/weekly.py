"""Weekly MVP scheduling and award selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .stats import Aggregate

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def last_scheduled(now: datetime, weekday: int, hour: int, tz: ZoneInfo) -> datetime:
    """The most recent `weekday` at `hour`:00 in `tz` that is <= now, returned in UTC."""
    local = now.astimezone(tz)
    days_back = (local.weekday() - weekday) % 7
    candidate = (local - timedelta(days=days_back)).replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate > local:
        candidate -= timedelta(days=7)
    return candidate.astimezone(timezone.utc)


@dataclass
class Award:
    emoji: str
    title: str
    puuid: str
    detail: str


def rank_by_rating(aggs: list[Aggregate], min_games: int) -> list[Aggregate]:
    qualified = [a for a in aggs if a.games >= min_games]
    return sorted(qualified, key=lambda a: a.rating, reverse=True)


def pick_awards(aggs: list[Aggregate], min_games: int) -> list[Award]:
    """Side awards for the weekly recap. Rate stats need `min_games`; totals don't."""
    qualified = [a for a in aggs if a.games >= min_games]
    awards: list[Award] = []

    def best(pool: list[Aggregate], key: Callable[[Aggregate], float], emoji: str, title: str,
             detail: Callable[[Aggregate], str]) -> None:
        if not pool:
            return
        winner = max(pool, key=key)
        if key(winner) > 0:
            awards.append(Award(emoji, title, winner.puuid, detail(winner)))

    best(aggs, lambda a: a.kills, "💥", "Frag Leader", lambda a: f"{a.kills} kills")
    best(qualified, lambda a: a.hs_pct, "🎯", "Headshot Machine", lambda a: f"{a.hs_pct:.1f}% headshots")
    best(qualified, lambda a: a.adr, "🩸", "Damage Dealer", lambda a: f"{a.adr:.0f} ADR")
    best(aggs, lambda a: a.first_bloods, "⚡", "Entry King", lambda a: f"{a.first_bloods} first bloods")
    best(aggs, lambda a: a.aces, "🃏", "Ace Collector", lambda a: f"{a.aces} ace{'s' if a.aces != 1 else ''}")
    best(aggs, lambda a: a.best_acs, "📈", "Game of the Week", lambda a: f"{a.best_acs:.0f} ACS ({a.best_game})")
    best(aggs, lambda a: a.rr_net, "📊", "RR Farmer", lambda a: f"{a.rr_net:+d} RR")
    best(aggs, lambda a: a.games, "🕹️", "Grinder", lambda a: f"{a.games} games")

    ranked = rank_by_rating(aggs, min_games)
    if len(ranked) >= 3:
        worst = ranked[-1]
        awards.append(Award("🫠", "Rough Week", worst.puuid, f"{worst.rating:.2f} rating, there's always next week"))
    return awards
