# ValoTrack

A Discord bot that follows your friends' Valorant accounts. It posts a card whenever someone finishes a game and crowns a weekly MVP.

## Features

- **Live match feed**: after every game, it posts the map, mode, score, K/D/A, ACS, ADR, headshot %, first bloods, aces/4Ks, rank and RR change, and whether you were Match or Team MVP. It also calls out win/loss streaks (3+). When several linked players are in the same game they share one card, and if they were on opposite teams it's labelled a **CIVIL WAR**.
- **Weekly MVP**: an automatic recap on a schedule you pick. It shows the MVP, the full standings, and side awards: Frag Leader, Headshot Machine, Damage Dealer, Entry King, Ace Collector, Game of the Week, RR Farmer, Grinder, and a gentle "Rough Week".
- **Stats on demand**: `/stats`, `/compare`, `/leaderboard`, `/rank`, `/lastmatch`.
- **Multi-server**: each server has its own linked players, channels, schedule and tracked modes.

## Where the data comes from

tracker.gg's developer API doesn't include Valorant, and Riot only gives Valorant match data to approved production apps, not personal keys. ValoTrack uses the free, unofficial **[HenrikDev API](https://docs.henrikdev.xyz)** instead, which most Valorant Discord bots use. Map, agent and player-card art comes from [valorant-api.com](https://valorant-api.com).

## Setup

### 1. Create the Discord bot

1. Go to <https://discord.com/developers/applications> → **New Application**.
2. **Bot** tab → **Reset Token** → copy the token.
3. Invite it to your server. Replace `YOUR_APP_ID` with the Application ID from **General Information**:
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=19456
   ```
   (19456 = View Channels + Send Messages + Embed Links. No privileged intents needed.)

### 2. Get a HenrikDev API key

Go to <https://api.henrikdev.xyz/dashboard/> → **API Keys** → generate a key. A basic key allows about 30 requests/minute, which comfortably covers a friend group.

### 3. Install and run

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

Fill in `DISCORD_TOKEN` and `HENRIK_API_KEY` in `.env`, then:

```bash
python -m valotrack
```

> Tip: while testing, set `DEV_GUILD_ID` to your server's ID so slash commands show up instantly. Global commands can take a few minutes to appear.

### 4. Configure in Discord

1. `/settings feed-channel #valorant`: where match cards go.
2. Everyone runs `/link Name#TAG`. Admins can do `/link Name#TAG @friend`.
3. Optional: `/settings mvp-schedule`, `/settings mvp-channel`, `/settings modes`, `/settings mvp-min-games`.

The bot checks for new games every 3 minutes by default. It only needs to be running to post; if it goes offline, it catches up on games from the last 12 hours when it comes back. For 24/7 uptime, run it on an always-on machine or a small VPS.

## Commands

| Command | What it does |
| --- | --- |
| `/link riot_id [member]` | Track a Riot account (imports the last 10 games for stats) |
| `/unlink [member]` | Stop tracking |
| `/tracked` | Who's linked in this server |
| `/lastmatch [member]` | Re-post someone's latest game |
| `/stats [member] [period] [mode]` | Record, K/D, ACS, ADR, HS%, top agents/maps, best game |
| `/compare first [second] [period] [mode]` | Head to head |
| `/leaderboard [stat] [period] [mode]` | Rank the server by rating, ACS, K/D, ADR, HS%, win rate, kills, games, RR, first bloods |
| `/rank [member]` | Live rank, RR, peak |
| `/mvp standings` | This week's MVP race so far |
| `/mvp post` | (Manage Server) Post a 7-day recap now |
| `/settings …` | (Manage Server) `feed-channel`, `mvp-channel`, `mvp-schedule`, `modes`, `mvp-min-games`, `show` |
| `/help` | Command overview |

Tracked modes default to Competitive, Unrated, Swiftplay and Premier. Change them with `/settings modes competitive, unrated`, or use `all`.

## How the MVP rating works

Each stat is scaled so an average ranked player scores about **1.00**, then blended:

| Component | Weight | Baseline |
| --- | --- | --- |
| ACS | 40% | 200 |
| K/D (capped at 3.0) | 25% | 1.0 |
| ADR | 15% | 135 |
| Win rate | 15% | 50% |
| Headshot % | 5% | 20% |

Small samples are pulled toward 1.00 (as if you'd also played two average games), so one lucky game can't steal MVP. You also need the minimum number of games (default 3) to qualify. The weights live in `valotrack/stats.py` if you want to tweak them.

## Configuration (`.env`)

| Variable | Default | Notes |
| --- | --- | --- |
| `DISCORD_TOKEN` | required | |
| `HENRIK_API_KEY` | required | |
| `DEV_GUILD_ID` | blank | Sync commands to one server instantly |
| `DATABASE_PATH` | `valotrack.db` | SQLite file |
| `POLL_INTERVAL_MINUTES` | `3` | How often to check for new games |
| `HENRIK_REQUESTS_PER_MINUTE` | `25` | Keep under your key's limit |

## Project layout

```
valotrack/
  bot.py        Discord client, cog loading, command sync
  henrik.py     HenrikDev API client (throttling, 429 backoff)
  tracker.py    Linking, polling for new games, deciding where to announce
  stats.py      Match parsing, aggregation, the rating
  weekly.py     MVP schedule + awards
  db.py         SQLite storage
  embeds.py     All the Discord embeds
  cogs/         Slash commands + background loops
tests/          pytest suite (fake API + temp DB)
```

Run the tests with:

```bash
pip install -r requirements-dev.txt
python -m pytest
```
