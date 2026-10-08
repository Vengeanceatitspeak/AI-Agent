# ──────────────────────────────────────────────────────────────
# filters/impact_filter.py — Rule-based impact classification
# ──────────────────────────────────────────────────────────────
"""
First-pass impact classification (before Groq):

  1. Start with the source's own impact rating.
  2. For scheduled data with actual/forecast values, check the deviation.
     A large surprise bumps MEDIUM → HIGH.
  3. Breaking news from §3 is always at least HIGH.

After this, groq_client.py performs the second pass and may adjust the
final_impact further.
"""
from __future__ import annotations

import logging
import re
from typing import List

from news.normalizer import ImpactLevel, NewsCategory, NewsEvent

logger = logging.getLogger(__name__)

# Deviation thresholds (as ratio) that upgrade impact
_SMALL_DEVIATION = 0.02   # 2% – not enough to upgrade
_LARGE_DEVIATION = 0.10   # 10% – upgrades MEDIUM to HIGH


def classify(event: NewsEvent) -> NewsEvent:
    """Apply rule-based impact classification and return mutated event."""

    # Breaking news is always at least HIGH (§3)
    if event.category == NewsCategory.BREAKING:
        if event.final_impact.value not in ("HIGH", "BREAKING"):
            event.final_impact = ImpactLevel.HIGH
        return event

    # For scheduled data, check actual vs forecast deviation
    if event.actual and event.forecast:
        deviation = _compute_deviation(event.actual, event.forecast)
        if deviation is not None and deviation >= _LARGE_DEVIATION:
            if event.source_impact in (ImpactLevel.LOW, ImpactLevel.MEDIUM):
                logger.info(
                    "Impact upgrade %s: %s → HIGH (deviation %.1f%%)",
                    event.event_id,
                    event.source_impact.value,
                    deviation * 100,
                )
                event.final_impact = ImpactLevel.HIGH
            elif event.source_impact == ImpactLevel.HIGH:
                event.final_impact = ImpactLevel.HIGH
        else:
            event.final_impact = event.source_impact
    else:
        event.final_impact = event.source_impact

    return event


def classify_all(events: List[NewsEvent]) -> List[NewsEvent]:
    return [classify(e) for e in events]


# ── Helpers ──────────────────────────────────────────────────

_NUM_RE = re.compile(r"[^0-9.\-]")


def _parse_numeric(value: str) -> float | None:
    """Extract a float from strings like '3.4%', '-0.2', '340K'."""
    cleaned = _NUM_RE.sub("", value.strip())
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _compute_deviation(actual_str: str, forecast_str: str) -> float | None:
    actual = _parse_numeric(actual_str)
    forecast = _parse_numeric(forecast_str)
    if actual is None or forecast is None:
        return None
    if forecast == 0:
        return abs(actual) if actual != 0 else 0.0
    return abs(actual - forecast) / abs(forecast)
