# ──────────────────────────────────────────────────────────────
# news/breaking_news.py — Breaking / unscheduled news sources
# ──────────────────────────────────────────────────────────────
"""
Monitors multiple RSS/Atom feeds for breaking financial news that doesn't
appear on any calendar.  Covers:

  • Central bank press-release feeds (Fed, ECB, BOJ, BOE)
  • Financial news wire feeds (Reuters, MarketWatch, Bloomberg via RSS)

Every item that mentions a monitored currency or instrument keyword is
treated as *provisionally HIGH IMPACT* until proven otherwise (per §3).
Source reliability is flagged as CONFIRMED or UNCONFIRMED based on the
feed's trust level.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import aiohttp
import feedparser

from config import MONITORED_CURRENCIES
from news import BaseNewsSource
from news.normalizer import ImpactLevel, NewsCategory, NewsEvent

logger = logging.getLogger(__name__)

# ── Feed definitions ─────────────────────────────────────────
# Each tuple: (url, reliability, friendly_name)
# "CONFIRMED" = official central bank / major wire; "UNCONFIRMED" = aggregator
_FEEDS: list[tuple[str, str, str]] = [
    # Central banks
    (
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "CONFIRMED",
        "Federal Reserve",
    ),
    (
        "https://www.ecb.europa.eu/rss/press.html",
        "CONFIRMED",
        "ECB",
    ),
    (
        "https://www.bankofengland.co.uk/rss/news",
        "CONFIRMED",
        "Bank of England",
    ),
    (
        "https://www.boj.or.jp/en/rss/whatsnew.xml",
        "CONFIRMED",
        "Bank of Japan",
    ),
    # Financial news wires / aggregators
    (
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
        "UNCONFIRMED",
        "Yahoo Finance",
    ),
    (
        "https://www.investing.com/rss/news.rss",
        "UNCONFIRMED",
        "Investing.com",
    ),
    (
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "UNCONFIRMED",
        "MarketWatch",
    ),
]

# Keywords that signal a news item might affect our currencies
_CURRENCY_KEYWORDS: Dict[str, list[str]] = {
    "USD": ["dollar", "usd", "fed", "fomc", "treasury", "us economy",
            "united states", "wall street", "nasdaq", "s&p", "dow"],
    "EUR": ["euro", "eur", "ecb", "eurozone", "eu economy", "european central"],
    "GBP": ["pound", "gbp", "sterling", "boe", "bank of england", "uk economy"],
    "JPY": ["yen", "jpy", "boj", "bank of japan", "japan economy", "nikkei"],
}

# Build flat keyword → currency mapping
_KEYWORD_TO_CURRENCY: Dict[str, str] = {}
for cur, kws in _CURRENCY_KEYWORDS.items():
    for kw in kws:
        _KEYWORD_TO_CURRENCY[kw] = cur


class BreakingNewsSource(BaseNewsSource):
    """Aggregate multiple RSS feeds for unscheduled/breaking financial news."""

    name = "breaking_news"

    def __init__(self) -> None:
        self._seen_ids: Set[str] = set()
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch(self) -> List[NewsEvent]:
        events: List[NewsEvent] = []
        session = await self._get_session()

        for url, reliability, feed_name in _FEEDS:
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.debug("%s returned HTTP %d", feed_name, resp.status)
                        continue
                    raw_xml = await resp.text()
            except Exception:
                logger.debug("Failed to fetch %s", feed_name, exc_info=True)
                continue

            feed = feedparser.parse(raw_xml)

            for entry in feed.entries:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                link = entry.get("link", "")
                pub_date = self._parse_pub_date(entry)

                # Deterministic ID
                entry_id = hashlib.sha256(
                    f"{feed_name}:{title}:{link}".encode()
                ).hexdigest()[:24]

                if entry_id in self._seen_ids:
                    continue

                # Check relevance: does the headline/summary mention any
                # monitored currency keyword?
                combined_text = f"{title} {summary}".lower()
                matched_currency = self._match_currency(combined_text)
                if not matched_currency:
                    self._seen_ids.add(entry_id)
                    continue

                event = NewsEvent(
                    title=title,
                    currency=matched_currency,
                    source=f"breaking:{feed_name.lower().replace(' ', '_')}",
                    timestamp=pub_date,
                    headline=title,
                    summary=summary[:500] if summary else None,
                    source_impact=ImpactLevel.HIGH,   # §3: provisional HIGH
                    final_impact=ImpactLevel.BREAKING,
                    category=NewsCategory.BREAKING,
                    source_reliability=reliability,
                    event_id=entry_id,
                )

                events.append(event)
                self._seen_ids.add(entry_id)

        logger.debug(
            "Breaking-news poll: %d new items from %d feeds",
            len(events),
            len(_FEEDS),
        )
        return events

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _match_currency(text: str) -> Optional[str]:
        """Return the first monitored currency that matches keywords in text."""
        for keyword, currency in _KEYWORD_TO_CURRENCY.items():
            if currency in MONITORED_CURRENCIES and keyword in text:
                return currency
        return None

    @staticmethod
    def _parse_pub_date(entry: dict) -> datetime:
        """Parse RSS published date, falling back to now()."""
        raw = entry.get("published_parsed") or entry.get("updated_parsed")
        if raw:
            try:
                from time import mktime
                return datetime.fromtimestamp(mktime(raw), tz=timezone.utc)
            except Exception:
                pass
        return datetime.now(timezone.utc)
