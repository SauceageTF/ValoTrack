"""Settings loaded from environment variables (or a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    discord_token: str
    henrik_api_key: str
    database_path: str = "valotrack.db"
    poll_interval_minutes: float = 3.0
    requests_per_minute: int = 25
    dev_guild_id: int | None = None


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable {name} (see .env.example)")
    return value


def load_settings() -> Settings:
    load_dotenv()
    dev_guild = os.getenv("DEV_GUILD_ID", "").strip()
    return Settings(
        discord_token=_require("DISCORD_TOKEN"),
        henrik_api_key=_require("HENRIK_API_KEY"),
        database_path=os.getenv("DATABASE_PATH", "").strip() or "valotrack.db",
        poll_interval_minutes=float(os.getenv("POLL_INTERVAL_MINUTES", "").strip() or 3),
        requests_per_minute=int(os.getenv("HENRIK_REQUESTS_PER_MINUTE", "").strip() or 25),
        dev_guild_id=int(dev_guild) if dev_guild else None,
    )
