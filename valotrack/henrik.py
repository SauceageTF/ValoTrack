"""Async client for the unofficial HenrikDev Valorant API (https://docs.henrikdev.xyz).

Riot doesn't hand out Valorant match data to personal API keys and tracker.gg's
developer API doesn't cover Valorant, so HenrikDev is the practical source.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://api.henrikdev.xyz"
MAX_ATTEMPTS = 3


class HenrikError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HenrikDev API error {status}: {message}")
        self.status = status
        self.message = message


class NotFound(HenrikError):
    pass


class HenrikClient:
    """Thin wrapper around the endpoints ValoTrack uses, with client-side throttling.

    HenrikDev counts both our request and any Riot requests it makes on our behalf
    against the key's limit, so the throttle is only a first line of defence; 429s
    are handled by backing off for as long as the API asks.
    """

    def __init__(self, api_key: str, requests_per_minute: int = 25) -> None:
        self._api_key = api_key
        self._interval = 60.0 / max(requests_per_minute, 1)
        self._lock = asyncio.Lock()
        self._next_slot = 0.0
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                base_url=BASE_URL,
                headers={"Authorization": self._api_key, "User-Agent": "ValoTrack Discord bot"},
                timeout=aiohttp.ClientTimeout(total=30),
            )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # -- endpoints -----------------------------------------------------------------

    async def get_account(self, name: str, tag: str) -> dict[str, Any]:
        return await self._get(f"/valorant/v2/account/{quote(name, safe='')}/{quote(tag, safe='')}")

    async def get_matches(self, region: str, platform: str, puuid: str, size: int = 5) -> list[dict[str, Any]]:
        data = await self._get(f"/valorant/v4/by-puuid/matches/{region}/{platform}/{puuid}", {"size": size})
        return data or []

    async def get_mmr(self, region: str, platform: str, puuid: str) -> dict[str, Any]:
        return await self._get(f"/valorant/v3/by-puuid/mmr/{region}/{platform}/{puuid}")

    async def get_mmr_history(self, region: str, platform: str, puuid: str) -> list[dict[str, Any]]:
        data = await self._get(f"/valorant/v2/by-puuid/mmr-history/{region}/{platform}/{puuid}")
        return (data or {}).get("history") or []

    # -- plumbing ------------------------------------------------------------------

    async def _wait_for_slot(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            delay = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._interval
        if delay > 0:
            await asyncio.sleep(delay)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self.start()
        assert self._session is not None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._wait_for_slot()
            try:
                async with self._session.get(path, params=params) as resp:
                    if resp.status == 429:
                        wait = _retry_after(resp.headers)
                        log.warning("HenrikDev rate limit hit on %s; backing off %.0fs", path, wait)
                        self._next_slot = max(self._next_slot, asyncio.get_running_loop().time() + wait)
                        continue
                    status = resp.status
                    text = await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == MAX_ATTEMPTS:
                    raise HenrikError(0, f"network error: {exc!r}") from exc
                await asyncio.sleep(2**attempt)
                continue

            try:
                payload = json.loads(text)
            except ValueError:
                payload = {"errors": [{"message": text[:200] or "empty response"}]}

            if status == 404:
                raise NotFound(status, _error_message(payload))
            if status in (401, 403):
                raise HenrikError(status, "HenrikDev rejected the API key; check HENRIK_API_KEY")
            if status >= 500 and attempt < MAX_ATTEMPTS:
                log.info("HenrikDev returned %s on %s; retrying", status, path)
                await asyncio.sleep(2**attempt)
                continue
            if status >= 400:
                raise HenrikError(status, _error_message(payload))
            return payload.get("data") if isinstance(payload, dict) else payload
        raise HenrikError(429, "still rate limited after retries")


def _retry_after(headers: Any) -> float:
    for key in ("Retry-After", "X-RateLimit-Reset"):
        try:
            return max(float(headers[key]), 1.0)
        except (KeyError, TypeError, ValueError):
            continue
    return 30.0


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            return str(first.get("message") or first) if isinstance(first, dict) else str(first)
        if payload.get("message"):
            return str(payload["message"])
    return str(payload)[:200]
