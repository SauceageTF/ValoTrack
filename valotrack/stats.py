"""Pure stat logic: parse HenrikDev v4 matches and aggregate a player's games.

Nothing in here talks to Discord, the API, or the database, so it's all unit-testable.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

QUEUE_NAMES = {
    "competitive": "Competitive",
    "unrated": "Unrated",
    "swiftplay": "Swiftplay",
    "premier": "Premier",
    "spikerush": "Spike Rush",
    "deathmatch": "Deathmatch",
    "hurm": "Team Deathmatch",
    "ggteam": "Escalation",
    "onefa": "Replication",
    "snowball": "Snowball Fight",
    "newmap": "New Map",
    "custom": "Custom",
}
DEFAULT_QUEUES = ("competitive", "unrated", "swiftplay", "premier")
# Friendly spellings people might type, mapped to Riot's internal queue ids.
QUEUE_ALIASES = {
    "comp": "competitive",
    "ranked": "competitive",
    "tdm": "hurm",
    "teamdeathmatch": "hurm",
    "escalation": "ggteam",
    "replication": "onefa",
    "dm": "deathmatch",
}


def normalize_queue(queue_id: str | None) -> str:
    key = (queue_id or "").strip().lower().replace(" ", "")
    return QUEUE_ALIASES.get(key, key) or "custom"


def queue_name(queue: str) -> str:
    return QUEUE_NAMES.get(queue, queue.replace("_", " ").title())


def parse_riot_id(riot_id: str) -> tuple[str, str]:
    name, sep, tag = riot_id.strip().rpartition("#")
    name, tag = name.strip(), tag.strip()
    if not sep or not name or not tag:
        raise ValueError("Riot IDs look like `Name#TAG`.")
    return name, tag


def parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class PlayerLine:
    """One player's performance in one match. Field names double as DB columns."""

    puuid: str
    name: str
    tag: str
    team_id: str
    agent: str
    agent_id: str
    tier_id: int
    tier_name: str
    kills: int
    deaths: int
    assists: int
    score: int
    damage_dealt: int
    damage_received: int
    headshots: int
    bodyshots: int
    legshots: int
    rounds: int
    result: str | None  # "win" | "loss" | "draw" | None when the mode has no result
    team_rounds_won: int
    team_rounds_lost: int
    placement: int  # 1 = top score in the lobby
    team_placement: int
    first_bloods: int = 0
    first_deaths: int = 0
    aces: int = 0
    four_ks: int = 0
    rr_change: int | None = None

    @property
    def riot_id(self) -> str:
        return f"{self.name}#{self.tag}"

    @property
    def acs(self) -> float:
        return self.score / self.rounds if self.rounds else 0.0

    @property
    def adr(self) -> float:
        return self.damage_dealt / self.rounds if self.rounds else 0.0

    @property
    def kd(self) -> float:
        return self.kills / self.deaths if self.deaths else float(self.kills)

    @property
    def hs_pct(self) -> float:
        shots = self.headshots + self.bodyshots + self.legshots
        return 100 * self.headshots / shots if shots else 0.0


@dataclass
class MatchInfo:
    match_id: str
    map_name: str
    map_id: str
    queue: str
    started_at: datetime
    length_ms: int
    rounds: int
    team_mode: bool  # two teams with a winner/loser (not deathmatch)
    lobby_size: int
    players: list[PlayerLine] = field(default_factory=list)

    @property
    def mode_name(self) -> str:
        return queue_name(self.queue)

    @property
    def ended_at(self) -> datetime:
        return self.started_at + timedelta(milliseconds=self.length_ms)


def parse_match(raw: dict[str, Any]) -> MatchInfo:
    """Turn a v4 match payload into a MatchInfo holding every player in the lobby."""
    meta = raw.get("metadata") or {}
    queue = normalize_queue((meta.get("queue") or {}).get("id"))
    teams = {t.get("team_id"): t for t in raw.get("teams") or []}

    rounds = len(raw.get("rounds") or [])
    if not rounds and teams:
        tally = next(iter(teams.values())).get("rounds") or {}
        rounds = int(tally.get("won") or 0) + int(tally.get("lost") or 0)

    # Round-by-round highlights only make sense for standard two-team, multi-round modes.
    standard = len(teams) == 2 and rounds > 1
    highlights = _round_highlights(raw.get("kills") or []) if standard else _Highlights()

    players = []
    for p in raw.get("players") or []:
        stats = p.get("stats") or {}
        damage = stats.get("damage") or {}
        tier = p.get("tier") or {}
        agent = p.get("agent") or {}
        result, won, lost = _team_result(teams.get(p.get("team_id")), teams)
        puuid = p.get("puuid") or ""
        players.append(
            PlayerLine(
                puuid=puuid,
                name=p.get("name") or "?",
                tag=p.get("tag") or "?",
                team_id=p.get("team_id") or "",
                agent=agent.get("name") or "Unknown",
                agent_id=agent.get("id") or "",
                tier_id=int(tier.get("id") or 0),
                tier_name=tier.get("name") or "Unrated",
                kills=int(stats.get("kills") or 0),
                deaths=int(stats.get("deaths") or 0),
                assists=int(stats.get("assists") or 0),
                score=int(stats.get("score") or 0),
                damage_dealt=int(damage.get("dealt") or 0),
                damage_received=int(damage.get("received") or 0),
                headshots=int(stats.get("headshots") or 0),
                bodyshots=int(stats.get("bodyshots") or 0),
                legshots=int(stats.get("legshots") or 0),
                rounds=rounds,
                result=result,
                team_rounds_won=won,
                team_rounds_lost=lost,
                placement=0,
                team_placement=0,
                first_bloods=highlights.first_bloods[puuid],
                first_deaths=highlights.first_deaths[puuid],
                aces=highlights.aces[puuid],
                four_ks=highlights.four_ks[puuid],
            )
        )
    _assign_placements(players)

    return MatchInfo(
        match_id=meta.get("match_id") or "",
        map_name=(meta.get("map") or {}).get("name") or "Unknown map",
        map_id=(meta.get("map") or {}).get("id") or "",
        queue=queue,
        started_at=parse_timestamp(meta.get("started_at")),
        length_ms=int(meta.get("game_length_in_ms") or 0),
        rounds=rounds,
        team_mode=len(teams) == 2,
        lobby_size=len(players),
        players=players,
    )


def _team_result(team: dict | None, teams: dict) -> tuple[str | None, int, int]:
    if team is None or len(teams) < 2:
        return None, 0, 0
    tally = team.get("rounds") or {}
    won, lost = int(tally.get("won") or 0), int(tally.get("lost") or 0)
    if team.get("won"):
        return "win", won, lost
    if any(t.get("won") for t in teams.values()):
        return "loss", won, lost
    return "draw", won, lost


@dataclass
class _Highlights:
    first_bloods: Counter = field(default_factory=Counter)
    first_deaths: Counter = field(default_factory=Counter)
    aces: Counter = field(default_factory=Counter)
    four_ks: Counter = field(default_factory=Counter)


def _round_highlights(kills: Iterable[dict[str, Any]]) -> _Highlights:
    out = _Highlights()
    by_round: dict[int, list[dict]] = defaultdict(list)
    for kill in kills:
        killer, victim = kill.get("killer") or {}, kill.get("victim") or {}
        # Ignore team kills and suicides for highlight purposes.
        if killer.get("puuid") and killer.get("team") != victim.get("team"):
            by_round[kill.get("round", 0)].append(kill)

    for round_kills in by_round.values():
        opener = min(round_kills, key=lambda k: k.get("time_in_round_in_ms") or 0)
        out.first_bloods[opener["killer"]["puuid"]] += 1
        if (opener.get("victim") or {}).get("puuid"):
            out.first_deaths[opener["victim"]["puuid"]] += 1
        for puuid, count in Counter(k["killer"]["puuid"] for k in round_kills).items():
            if count >= 5:
                out.aces[puuid] += 1
            elif count == 4:
                out.four_ks[puuid] += 1
    return out


def _assign_placements(players: list[PlayerLine]) -> None:
    for i, p in enumerate(sorted(players, key=lambda p: p.score, reverse=True), start=1):
        p.placement = i
    by_team: dict[str, list[PlayerLine]] = defaultdict(list)
    for p in players:
        by_team[p.team_id].append(p)
    for team in by_team.values():
        for i, p in enumerate(sorted(team, key=lambda p: p.score, reverse=True), start=1):
            p.team_placement = i


# -- Aggregation -------------------------------------------------------------------

# The ValoTrack rating: each component is scaled so a roughly average ranked player
# lands near 1.00, then blended. Tweak to taste.
RATING_WEIGHTS = {"acs": 0.40, "kd": 0.25, "adr": 0.15, "win": 0.15, "hs": 0.05}
RATING_BASELINES = {"acs": 200.0, "adr": 135.0, "hs": 20.0}
KD_CAP = 3.0
# Pull small samples toward 1.00 so one lucky game doesn't win MVP.
RATING_PRIOR_GAMES = 2


@dataclass
class Aggregate:
    puuid: str
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    score: int = 0
    rounds: int = 0
    damage_dealt: int = 0
    headshots: int = 0
    bodyshots: int = 0
    legshots: int = 0
    first_bloods: int = 0
    aces: int = 0
    four_ks: int = 0
    rr_net: int = 0
    best_acs: float = 0.0
    best_game: str = ""
    agents: Counter = field(default_factory=Counter)
    maps: Counter = field(default_factory=Counter)

    def add(self, match: MatchInfo, line: PlayerLine) -> None:
        self.games += 1
        if line.result == "win":
            self.wins += 1
        elif line.result == "loss":
            self.losses += 1
        elif line.result == "draw":
            self.draws += 1
        for attr in (
            "kills", "deaths", "assists", "score", "rounds", "damage_dealt",
            "headshots", "bodyshots", "legshots", "first_bloods", "aces", "four_ks",
        ):
            setattr(self, attr, getattr(self, attr) + getattr(line, attr))
        self.rr_net += line.rr_change or 0
        if line.acs > self.best_acs:
            self.best_acs = line.acs
            self.best_game = f"{line.agent} on {match.map_name}"
        self.agents[line.agent] += 1
        self.maps[match.map_name] += 1

    @property
    def decided(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def win_rate(self) -> float:
        return self.wins / self.decided if self.decided else 0.0

    @property
    def acs(self) -> float:
        return self.score / self.rounds if self.rounds else 0.0

    @property
    def adr(self) -> float:
        return self.damage_dealt / self.rounds if self.rounds else 0.0

    @property
    def kd(self) -> float:
        return self.kills / self.deaths if self.deaths else float(self.kills)

    @property
    def hs_pct(self) -> float:
        shots = self.headshots + self.bodyshots + self.legshots
        return 100 * self.headshots / shots if shots else 0.0

    @property
    def rating(self) -> float:
        if not self.games:
            return 0.0
        w = RATING_WEIGHTS
        raw = (
            w["acs"] * self.acs / RATING_BASELINES["acs"]
            + w["kd"] * min(self.kd, KD_CAP)
            + w["adr"] * self.adr / RATING_BASELINES["adr"]
            + w["win"] * ((0.5 + self.win_rate) if self.decided else 1.0)
            + w["hs"] * self.hs_pct / RATING_BASELINES["hs"]
        )
        return (raw * self.games + RATING_PRIOR_GAMES) / (self.games + RATING_PRIOR_GAMES)


def aggregate(puuid: str, history: Iterable[tuple[MatchInfo, PlayerLine]]) -> Aggregate:
    agg = Aggregate(puuid)
    for match, line in history:
        agg.add(match, line)
    return agg


def current_streak(results: Iterable[str | None]) -> tuple[str | None, int]:
    """Given results newest-first, return the ongoing ("win"|"loss", length) streak."""
    kind, count = None, 0
    for result in results:
        if result not in ("win", "loss"):
            break
        if kind is None:
            kind = result
        if result != kind:
            break
        count += 1
    return kind, count


# Leaderboard stats: key -> (label, accessor, formatter)
LEADERBOARD_STATS = {
    "rating": ("Rating", lambda a: a.rating, lambda v: f"{v:.2f}"),
    "acs": ("ACS", lambda a: a.acs, lambda v: f"{v:.0f}"),
    "kd": ("K/D", lambda a: a.kd, lambda v: f"{v:.2f}"),
    "adr": ("ADR", lambda a: a.adr, lambda v: f"{v:.0f}"),
    "hs": ("Headshot %", lambda a: a.hs_pct, lambda v: f"{v:.1f}%"),
    "winrate": ("Win rate", lambda a: a.win_rate * 100, lambda v: f"{v:.0f}%"),
    "kills": ("Kills", lambda a: a.kills, lambda v: f"{v:.0f}"),
    "games": ("Games played", lambda a: a.games, lambda v: f"{v:.0f}"),
    "rr": ("Net RR", lambda a: a.rr_net, lambda v: f"{v:+.0f}"),
    "fb": ("First bloods", lambda a: a.first_bloods, lambda v: f"{v:.0f}"),
}
