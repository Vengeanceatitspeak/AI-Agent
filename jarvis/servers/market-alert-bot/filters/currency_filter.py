# ──────────────────────────────────────────────────────────────
# filters/currency_filter.py — Drop events for unmonitored currencies
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import List

from config import MONITORED_CURRENCIES
from news.normalizer import NewsEvent

logger = logging.getLogger(__name__)


def is_relevant(event: NewsEvent) -> bool:
    """Return True if the event's currency is in our monitored set."""
    relevant = event.currency.upper() in MONITORED_CURRENCIES
    if not relevant:
        logger.debug("Filtered out %s (currency %s not monitored)", event.title, event.currency)
    return relevant


def filter_events(events: List[NewsEvent]) -> List[NewsEvent]:
    """Keep only events whose currency we care about."""
    return [e for e in events if is_relevant(e)]
