"""Discord embed builders."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import discord

from .db import Player
from .stats import LEADERBOARD_STATS, Aggregate, MatchInfo, PlayerLine
from .weekly import Award

VALORANT_RED = 0xFF4655
RESULT_COLORS = {"win": 0x2ECC71, "loss": 0xE74C3C, "draw": 0x95A5A6, None: VALORANT_RED}
RESULT_HEADLINES = {"win": "VICTORY", "loss": "DEFEAT", "draw": "DRAW", None: "MATCH COMPLETE"}

# Artwork from the community-run valorant-api.com asset CDN.
AGENT_ICON = "https://media.valorant-api.com/agents/{}/displayicon.png"
MAP_BANNER = "https://media.valorant-api.com/maps/{}/listviewicon.png"
CARD_SMALL = "https://media.valorant-api.com/playercards/{}/smallart.png"
CARD_WIDE = "https://media.valorant-api.com/playercards/{}/wideart.png"

MEDALS = ("🥇", "🥈", "🥉")


def _duration(ms: int) -> str:
    minutes = round(ms / 60000)
    return f"{minutes} min" if minutes else "<1 min"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _mention(discord_id: int | None) -> str:
    return f"<@{discord_id}>" if discord_id else ""


# -- match feed --------------------------------------------------------------------


def match_embed(
    match: MatchInfo,
    lines: list[PlayerLine],
    owners: dict[str, int],
    streaks: dict[str, tuple[str | None, int]] | None = None,
) -> discord.Embed:
    streaks = streaks or {}
    civil_war = match.team_mode and len({line.team_id for line in lines}) > 1
    results = {line.result for line in lines}
    result = results.pop() if len(results) == 1 else None

    if civil_war:
        headline, color = "CIVIL WAR", 0xF1C40F
    else:
        headline, color = RESULT_HEADLINES[result], RESULT_COLORS[result]

    details = [match.mode_name]
    if match.team_mode and not civil_war:
        details.append(f"**{lines[0].team_rounds_won}–{lines[0].team_rounds_lost}**")
    details.append(_duration(match.length_ms))

    embed = discord.Embed(
        title=f"{headline} · {match.map_name}",
        description=" · ".join(details),
        color=color,
        timestamp=match.started_at,
    )
    for line in sorted(lines, key=lambda l: l.placement):
        embed.add_field(
            name=f"{line.agent} — {line.riot_id}",
            value=_line_summary(match, line, owners.get(line.puuid), streaks.get(line.puuid), civil_war),
            inline=False,
        )
    if len(lines) == 1 and lines[0].agent_id:
        embed.set_thumbnail(url=AGENT_ICON.format(lines[0].agent_id))
    if match.map_id:
        embed.set_image(url=MAP_BANNER.format(match.map_id))
    embed.set_footer(text=f"ValoTrack · match {match.match_id[:8]}")
    return embed


def _line_summary(match: MatchInfo, line: PlayerLine, discord_id: int | None,
                  streak: tuple[str | None, int] | None, civil_war: bool) -> str:
    head = [_mention(discord_id)]
    if civil_war:
        verb = {"win": "✅ Won", "loss": "❌ Lost", "draw": "➖ Drew"}.get(line.result or "", "")
        head.append(f"{verb} {line.team_rounds_won}–{line.team_rounds_lost}")
    if match.team_mode and line.placement == 1:
        head.append("🏆 **Match MVP**")
    elif match.team_mode and line.team_placement == 1:
        head.append("⭐ **Team MVP**")
    else:
        head.append(f"#{line.placement} of {match.lobby_size} in lobby")

    rows = [
        " · ".join(part for part in head if part),
        f"**{line.kills} / {line.deaths} / {line.assists}** · K/D {line.kd:.2f}",
        f"ACS **{line.acs:.0f}** · ADR **{line.adr:.0f}** · HS **{line.hs_pct:.0f}%**",
    ]

    extras = []
    if line.first_bloods:
        extras.append(f"⚡ {_plural(line.first_bloods, 'first blood')}")
    if line.aces:
        extras.append(f"🃏 {_plural(line.aces, 'ACE')}")
    if line.four_ks:
        extras.append(f"4K ×{line.four_ks}")
    if extras:
        rows.append(" · ".join(extras))

    if match.queue == "competitive":
        rank = f"🏅 {line.tier_name}"
        if line.rr_change is not None:
            rank += f" (**{line.rr_change:+d} RR**)"
        rows.append(rank)

    if streak and streak[1] >= 3:
        rows.append(f"🔥 {streak[1]}-game win streak" if streak[0] == "win" else f"🧊 {streak[1]}-game loss streak")
    return "\n".join(rows)


# -- profile / stats ---------------------------------------------------------------


def _top(counter: Any, n: int = 3) -> str:
    return ", ".join(f"{name} ({count})" for name, count in counter.most_common(n)) or "—"


def stats_embed(player: Player, discord_id: int, agg: Aggregate, period_label: str, modes_label: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"{player.riot_id} — {period_label}",
        description=f"{_mention(discord_id)} · {modes_label}",
        color=VALORANT_RED,
    )
    if player.card:
        embed.set_thumbnail(url=CARD_SMALL.format(player.card))
    if not agg.games:
        embed.add_field(name="No games yet", value="Nothing stored for this period. Go queue up!")
        return embed

    record = f"{agg.wins}W – {agg.losses}L" + (f" – {agg.draws}D" if agg.draws else "")
    games = agg.games
    embed.add_field(name="Record", value=f"{record}\n{agg.win_rate:.0%} win rate · {_plural(games, 'game')}")
    embed.add_field(name="Rating", value=f"**{agg.rating:.2f}**")
    embed.add_field(
        name="K / D / A (avg)",
        value=f"{agg.kills / games:.1f} / {agg.deaths / games:.1f} / {agg.assists / games:.1f}\nK/D **{agg.kd:.2f}**",
    )
    embed.add_field(name="ACS", value=f"{agg.acs:.0f}")
    embed.add_field(name="ADR", value=f"{agg.adr:.0f}")
    embed.add_field(name="Headshot %", value=f"{agg.hs_pct:.1f}%")
    embed.add_field(name="Highlights",
                    value=f"⚡ {agg.first_bloods} FB · 🃏 {agg.aces} aces · {agg.four_ks} 4Ks")
    embed.add_field(name="Net RR", value=f"{agg.rr_net:+d}")
    embed.add_field(name="Best game", value=f"{agg.best_acs:.0f} ACS\n{agg.best_game}")
    embed.add_field(name="Top agents", value=_top(agg.agents), inline=False)
    embed.add_field(name="Top maps", value=_top(agg.maps), inline=False)
    return embed


def compare_embed(entries: list[tuple[Player, int, Aggregate]], period_label: str) -> discord.Embed:
    embed = discord.Embed(title=f"Head to head — {period_label}", color=VALORANT_RED)
    rows = [
        ("Games", lambda a: f"{a.games}", lambda a: a.games),
        ("Win rate", lambda a: f"{a.win_rate:.0%}", lambda a: a.win_rate),
        ("Rating", lambda a: f"{a.rating:.2f}", lambda a: a.rating),
        ("K/D", lambda a: f"{a.kd:.2f}", lambda a: a.kd),
        ("ACS", lambda a: f"{a.acs:.0f}", lambda a: a.acs),
        ("ADR", lambda a: f"{a.adr:.0f}", lambda a: a.adr),
        ("HS %", lambda a: f"{a.hs_pct:.1f}%", lambda a: a.hs_pct),
        ("First bloods", lambda a: f"{a.first_bloods}", lambda a: a.first_bloods),
    ]
    for player, discord_id, agg in entries:
        lines = []
        for label, fmt, key in rows:
            best = max(key(other) for _, _, other in entries)
            mark = " ◀" if key(agg) == best and agg.games and len(entries) > 1 else ""
            lines.append(f"{label}: **{fmt(agg)}**{mark}")
        embed.add_field(name=player.riot_id, value=f"{_mention(discord_id)}\n" + "\n".join(lines))
    return embed


def leaderboard_embed(rows: list[tuple[int, Player, Aggregate]], stat: str, period_label: str,
                      min_games: int) -> discord.Embed:
    label, key, fmt = LEADERBOARD_STATS[stat]
    embed = discord.Embed(title=f"Leaderboard · {label}", description=period_label, color=VALORANT_RED)
    if not rows:
        embed.description += "\n\nNobody has enough games yet."
        return embed
    lines = []
    for i, (discord_id, player, agg) in enumerate(rows):
        place = MEDALS[i] if i < len(MEDALS) else f"`#{i + 1}`"
        lines.append(f"{place} {_mention(discord_id)} **{fmt(key(agg))}** · {_plural(agg.games, 'game')}")
    embed.description += "\n\n" + "\n".join(lines)
    embed.set_footer(text=f"Rate stats need at least {min_games} games")
    return embed


def rank_embed(player: Player, discord_id: int | None, mmr: dict[str, Any]) -> discord.Embed:
    current = mmr.get("current") or {}
    tier = (current.get("tier") or {}).get("name") or "Unrated"
    embed = discord.Embed(title=player.riot_id, description=_mention(discord_id), color=VALORANT_RED)
    if player.card:
        embed.set_thumbnail(url=CARD_SMALL.format(player.card))

    needed = current.get("games_needed_for_rating") or 0
    if needed:
        embed.add_field(name="Current rank", value=f"Unrated, {_plural(needed, 'placement game')} to go")
    else:
        value = f"**{tier}** · {current.get('rr', 0)} RR"
        if current.get("last_change") is not None:
            value += f"\nLast game: {current['last_change']:+d} RR"
        placement = current.get("leaderboard_placement")
        if placement:
            value += f"\nLeaderboard #{placement.get('rank')}"
        embed.add_field(name="Current rank", value=value, inline=False)

    peak = mmr.get("peak")
    if peak:
        embed.add_field(
            name="Peak",
            value=f"{(peak.get('tier') or {}).get('name', '?')} ({(peak.get('season') or {}).get('short', '?')})",
        )
    seasons = [s for s in mmr.get("seasonal") or [] if s.get("games")]
    if seasons:
        latest = seasons[-1]
        embed.add_field(
            name=f"Season {(latest.get('season') or {}).get('short', '')}".strip(),
            value=f"{latest.get('wins', 0)}W in {_plural(latest.get('games', 0), 'game')}",
        )
    return embed


def linked_embed(player: Player, discord_id: int, imported: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"Linked {player.riot_id}",
        description=(
            f"{_mention(discord_id)} is now tracked. Region **{player.region.upper()}** · "
            f"{player.platform.upper()}\nImported {_plural(imported, 'recent match')} for stats."
        ),
        color=VALORANT_RED,
    )
    if player.card:
        embed.set_image(url=CARD_WIDE.format(player.card))
    return embed


# -- weekly MVP --------------------------------------------------------------------


def weekly_embed(
    start: datetime,
    end: datetime,
    ranked: list[Aggregate],
    awards: list[Award],
    owners: dict[str, int],
    min_games: int,
    preview: bool = False,
) -> discord.Embed:
    span = f"{discord.utils.format_dt(start, 'D')} → {discord.utils.format_dt(end, 'D')}"
    title = "📋 MVP race so far" if preview else "🏆 Weekly MVP"
    embed = discord.Embed(title=title, description=span, color=0xF1C40F)

    if ranked:
        mvp = ranked[0]
        embed.add_field(
            name="MVP",
            value=(
                f"👑 {_mention(owners.get(mvp.puuid))} with a **{mvp.rating:.2f}** rating\n"
                f"{mvp.wins}W–{mvp.losses}L · K/D {mvp.kd:.2f} · ACS {mvp.acs:.0f} · "
                f"ADR {mvp.adr:.0f} · HS {mvp.hs_pct:.0f}%"
            ),
            inline=False,
        )
        standings = []
        for i, agg in enumerate(ranked[:10]):
            place = MEDALS[i] if i < len(MEDALS) else f"`#{i + 1}`"
            standings.append(
                f"{place} {_mention(owners.get(agg.puuid))} **{agg.rating:.2f}** · "
                f"{_plural(agg.games, 'game')} · {agg.win_rate:.0%} WR"
            )
        embed.add_field(name="Standings", value="\n".join(standings), inline=False)
    else:
        embed.add_field(name="MVP", value=f"Nobody hit {_plural(min_games, 'game')} this week.", inline=False)

    if awards:
        embed.add_field(
            name="Awards",
            value="\n".join(f"{a.emoji} **{a.title}**: {_mention(owners.get(a.puuid))} ({a.detail})" for a in awards),
            inline=False,
        )
    embed.set_footer(text=f"Rating blends ACS, K/D, ADR, win rate & HS% · min {_plural(min_games, 'game')} to qualify")
    return embed
