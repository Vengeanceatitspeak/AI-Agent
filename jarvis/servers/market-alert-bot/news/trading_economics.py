# ──────────────────────────────────────────────────────────────
# news/trading_economics.py — Secondary source: Trading Economics
# ──────────────────────────────────────────────────────────────
"""
Trading Economics provides a public (no-auth) calendar API endpoint that
returns JSON.  We poll it periodically as a secondary cross-check for
scheduled economic data.

Endpoint: https://api.tradingeconomics.com/calendar
(Public tier — rate-limited; we keep polling wide enough to stay safe.)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp

from news import BaseNewsSource
from news.normalizer import ImpactLevel, NewsCategory, NewsEvent, normalize_impact

logger = logging.getLogger(__name__)

# ── Public calendar endpoints (no auth key needed for basic data) ──
TE_CALENDAR_URL = "https://api.tradingeconomics.com/calendar"
TE_COUNTRY_URL = "https://api.tradingeconomics.com/calendar/country/united%20states,euro%20area,united%20kingdom,japan"

_HEADERS = {
    "User-Agent": "MarketAlertBot/1.0",
    "Accept": "application/json",
}

# Map Trading Economics country names → standard currency codes
_COUNTRY_TO_CURRENCY: Dict[str, str] = {
    "united states": "USD",
    "euro area": "EUR",
    "germany": "EUR",
    "france": "EUR",
    "italy": "EUR",
    "spain": "EUR",
    "united kingdom": "GBP",
    "japan": "JPY",
    "canada": "CAD",
    "australia": "AUD",
    "new zealand": "NZD",
    "switzerland": "CHF",
    "china": "CNY",
}


class TradingEconomicsSource(BaseNewsSource):
    """Fetch economic calendar from Trading Economics public API."""

    name = "trading_economics"

    def __init__(self) -> None:
        self._cache: Dict[str, Optional[str]] = {}
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                headers=_HEADERS,
                timeout=timeout,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch(self) -> List[NewsEvent]:
        try:
            session = await self._get_session()
            async with session.get(TE_COUNTRY_URL) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Trading Economics returned HTTP %d", resp.status
                    )
                    return []
                data = await resp.json(content_type=None)
        except Exception:
            logger.exception("Failed to fetch Trading Economics calendar")
            return []

        if not isinstance(data, list):
            logger.warning("TE response is not a list — API may have changed")
            return []

        return self._parse_and_diff(data)

    def _parse_and_diff(self, items: List[dict]) -> List[NewsEvent]:
        events: List[NewsEvent] = []

        for item in items:
            country = (item.get("Country") or "").strip().lower()
            currency = _COUNTRY_TO_CURRENCY.get(country, "")
            if not currency:
                continue

            title = item.get("Event") or item.get("Category") or "Unknown"
            actual = str(item.get("Actual", "")) if item.get("Actual") is not None else None
            forecast = str(item.get("Forecast", "")) if item.get("Forecast") is not None else None
            previous = str(item.get("Previous", "")) if item.get("Previous") is not None else None

            # Parse impact (TE uses 1/2/3 or "Low"/"Medium"/"High")
            raw_importance = str(item.get("Importance", "2"))
            source_impact = normalize_impact(raw_importance)

            # Parse timestamp
            ts = self._parse_ts(item.get("Date"))

            event = NewsEvent(
                title=title,
                currency=currency,
                source=self.name,
                timestamp=ts,
                actual=actual,
                forecast=forecast,
                previous=previous,
                source_impact=source_impact,
                final_impact=source_impact,
                category=NewsCategory.SCHEDULED,
            )

            # Change detection
            cached_actual = self._cache.get(event.event_id)
            is_new = event.event_id not in self._cache
            actual_changed = not is_new and actual and actual != cached_actual

            if is_new or actual_changed:
                events.append(event)

            self._cache[event.event_id] = actual

        logger.debug("TE poll: %d items, %d new/updated", len(items), len(events))
        return events

    @staticmethod
    def _parse_ts(raw: Optional[str]) -> datetime:
        if not raw:
            return datetime.now(timezone.utc)
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        ):
            try:
                return datetime.strptime(raw[:19], fmt[:19]).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
        return datetime.now(timezone.utc)
