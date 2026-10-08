# ──────────────────────────────────────────────────────────────
# news/forex_factory.py — PRIMARY source: Forex Factory calendar
# ──────────────────────────────────────────────────────────────
"""
Forex Factory does not expose a public REST/JSON API for its calendar.
We scrape the lightweight mobile-friendly calendar page, which is more
stable than the desktop layout and returns less HTML to parse.

Change-detection logic:
  • On each poll we fetch today's calendar rows.
  • We compare each row against a local cache (dict keyed by event_id).
  • We only emit a NewsEvent when:
      a) the row is brand-new, OR
      b) the "actual" value has CHANGED since the last poll (i.e. data just
         dropped).
  • This ensures we never flood alerts for events we've already seen.

Polling interval is configurable via config.FOREX_FACTORY_POLL_INTERVAL.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp
from bs4 import BeautifulSoup, Tag

from config import FOREX_FACTORY_POLL_INTERVAL
from news import BaseNewsSource
from news.normalizer import ImpactLevel, NewsCategory, NewsEvent, normalize_impact

logger = logging.getLogger(__name__)

FF_CALENDAR_URL = "https://www.forexfactory.com/calendar"

# Common request headers so we look like a real browser
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.forexfactory.com/",
}

# ── Currency code regex ─────────────────────────────────────
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class ForexFactorySource(BaseNewsSource):
    """Scrape Forex Factory calendar and emit *new/updated* events."""

    name = "forex_factory"

    def __init__(self) -> None:
        # Cache: event_id → last-seen actual value (or None)
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

    # ── Core interface ───────────────────────────────────────

    async def fetch(self) -> List[NewsEvent]:
        """Fetch today's FF calendar, return only new/updated events."""
        try:
            session = await self._get_session()
            async with session.get(FF_CALENDAR_URL) as resp:
                if resp.status != 200:
                    logger.warning("Forex Factory returned HTTP %d", resp.status)
                    return []
                html = await resp.text()
        except Exception:
            logger.exception("Failed to fetch Forex Factory calendar")
            return []

        return self._parse_and_diff(html)

    # ── Parsing ──────────────────────────────────────────────

    def _parse_and_diff(self, html: str) -> List[NewsEvent]:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("tr.calendar__row")
        if not rows:
            # Try alternate selector for table rows
            rows = soup.select("tr[class*='calendar_row']")
        if not rows:
            logger.warning("No calendar rows found — layout may have changed")
            return []

        events: List[NewsEvent] = []
        current_date_str: Optional[str] = None
        current_time_str: Optional[str] = None

        for row in rows:
            # --- Date cell (spans multiple rows) ---
            date_cell = row.select_one("td.calendar__date, td.date")
            if date_cell and date_cell.get_text(strip=True):
                current_date_str = date_cell.get_text(strip=True)

            # --- Time cell ---
            time_cell = row.select_one("td.calendar__time, td.time")
            if time_cell and time_cell.get_text(strip=True):
                t = time_cell.get_text(strip=True)
                if t.lower() not in ("", "all day", "tentative"):
                    current_time_str = t

            # --- Currency ---
            cur_cell = row.select_one("td.calendar__currency, td.currency")
            currency = cur_cell.get_text(strip=True).upper() if cur_cell else ""
            if not _CURRENCY_RE.match(currency):
                continue  # skip non-event rows (headers, blank, etc.)

            # --- Event name ---
            event_cell = row.select_one("td.calendar__event, td.event")
            title = event_cell.get_text(strip=True) if event_cell else "Unknown"

            # --- Impact ---
            impact_cell = row.select_one("td.calendar__impact, td.impact")
            impact_raw = "medium"
            if impact_cell:
                span = impact_cell.select_one("span")
                if span:
                    cls = " ".join(span.get("class", []))
                    if "high" in cls or "red" in cls:
                        impact_raw = "high"
                    elif "medium" in cls or "orange" in cls or "ora" in cls:
                        impact_raw = "medium"
                    elif "low" in cls or "yellow" in cls or "yel" in cls:
                        impact_raw = "low"
                    elif "gray" in cls or "grey" in cls:
                        impact_raw = "low"

            # --- Actual / Forecast / Previous ---
            actual = self._cell_text(row, "td.calendar__actual, td.actual")
            forecast = self._cell_text(row, "td.calendar__forecast, td.forecast")
            previous = self._cell_text(row, "td.calendar__previous, td.previous")

            # --- Timestamp ---
            ts = self._build_timestamp(current_date_str, current_time_str)

            # --- Build event ---
            event = NewsEvent(
                title=title,
                currency=currency,
                source=self.name,
                timestamp=ts,
                actual=actual,
                forecast=forecast,
                previous=previous,
                source_impact=normalize_impact(impact_raw),
                final_impact=normalize_impact(impact_raw),
                category=NewsCategory.SCHEDULED,
            )

            # --- Change detection ---
            cached_actual = self._cache.get(event.event_id)
            is_new = event.event_id not in self._cache
            actual_changed = (
                not is_new and actual and actual != cached_actual
            )

            if is_new or actual_changed:
                if actual_changed:
                    logger.info(
                        "FF actual updated: %s → %s (was %s)",
                        event.event_id,
                        actual,
                        cached_actual,
                    )
                events.append(event)

            # Update cache regardless
            self._cache[event.event_id] = actual

        logger.debug("FF poll: %d rows parsed, %d new/updated", len(rows), len(events))
        return events

    # ── Utilities ────────────────────────────────────────────

    @staticmethod
    def _cell_text(row: Tag, selector: str) -> Optional[str]:
        cell = row.select_one(selector)
        if cell:
            text = cell.get_text(strip=True)
            return text if text else None
        return None

    @staticmethod
    def _build_timestamp(
        date_str: Optional[str], time_str: Optional[str]
    ) -> datetime:
        """Best-effort parse of FF date + time strings into UTC datetime."""
        now = datetime.now(timezone.utc)
        if not date_str and not time_str:
            return now

        # FF often shows dates like "Sep 25" or "Thu Sep 25"
        # and times like "8:30am", "10:00pm", "Tentative"
        combined = f"{date_str or ''} {time_str or ''}".strip()
        for fmt in (
            "%b %d %I:%M%p",
            "%a %b %d %I:%M%p",
            "%b %d",
            "%a %b %d",
        ):
            try:
                parsed = datetime.strptime(combined, fmt)
                return parsed.replace(year=now.year, tzinfo=timezone.utc)
            except ValueError:
                continue

        return now
