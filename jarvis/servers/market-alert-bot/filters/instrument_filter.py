# ──────────────────────────────────────────────────────────────
# filters/instrument_filter.py — Map events to affected instruments
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import List

from config import INSTRUMENTS
from news.normalizer import NewsEvent

logger = logging.getLogger(__name__)


def tag_instruments(event: NewsEvent) -> NewsEvent:
    """Populate event.affected_instruments based on currency exposure."""
    affected = []
    for instrument, currencies in INSTRUMENTS.items():
        if event.currency.upper() in currencies:
            affected.append(instrument)
    event.affected_instruments = affected
    return event


def tag_all(events: List[NewsEvent]) -> List[NewsEvent]:
    return [tag_instruments(e) for e in events]
