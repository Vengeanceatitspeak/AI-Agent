# ──────────────────────────────────────────────────────────────
# news/normalizer.py — Canonical NewsEvent schema & normalisation
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ImpactLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BREAKING = "BREAKING"


class NewsCategory(Enum):
    SCHEDULED = "SCHEDULED"
    BREAKING = "BREAKING"


@dataclass
class NewsEvent:
    """Normalised representation of a single news event."""

    # Required
    title: str
    currency: str                       # e.g. "USD"
    source: str                         # e.g. "forex_factory"
    timestamp: datetime                 # when the data was released (UTC)

    # Scheduled data fields (optional)
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None

    # Impact / classification
    source_impact: ImpactLevel = ImpactLevel.MEDIUM   # from the source
    final_impact: ImpactLevel = ImpactLevel.MEDIUM     # after all classification
    category: NewsCategory = NewsCategory.SCHEDULED

    # Breaking-news extras
    headline: Optional[str] = None
    summary: Optional[str] = None
    source_reliability: str = "CONFIRMED"  # "CONFIRMED" | "UNCONFIRMED"

    # Groq analysis (populated later)
    groq_direction: Optional[str] = None
    groq_confidence: Optional[float] = None
    groq_magnitude: Optional[str] = None
    groq_action_advice: Optional[str] = None
    groq_reasoning: Optional[str] = None

    # Affected instruments (populated by filter)
    affected_instruments: list[str] = field(default_factory=list)

    # De-duplication
    event_id: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            self.event_id = self._generate_event_id()

    # ── helpers ───────────────────────────────────────────────

    def _generate_event_id(self) -> str:
        """
        Deterministic event ID.
        Format: CURRENCY_TITLE_DATE_HHMM  (slugified).
        Scheduled example: USD_CPI_2026-09-25_1930
        Breaking example:  hash-based to avoid collisions.
        """
        if self.category == NewsCategory.SCHEDULED:
            ts = self.timestamp.strftime("%Y-%m-%d_%H%M")
            slug = self.title.upper().replace(" ", "_")[:40]
            return f"{self.currency}_{slug}_{ts}"
        else:
            raw = f"{self.source}_{self.title}_{self.timestamp.isoformat()}"
            return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def as_dict(self) -> dict:
        """Flat dict suitable for DB insertion or JSON serialisation."""
        return {
            "event_id": self.event_id,
            "title": self.title,
            "currency": self.currency,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "actual": self.actual,
            "forecast": self.forecast,
            "previous": self.previous,
            "source_impact": self.source_impact.value,
            "final_impact": self.final_impact.value,
            "category": self.category.value,
            "headline": self.headline,
            "summary": self.summary,
            "source_reliability": self.source_reliability,
            "groq_direction": self.groq_direction,
            "groq_confidence": self.groq_confidence,
            "groq_magnitude": self.groq_magnitude,
            "groq_action_advice": self.groq_action_advice,
            "groq_reasoning": self.groq_reasoning,
            "affected_instruments": ",".join(self.affected_instruments),
        }


def normalize_impact(raw: str) -> ImpactLevel:
    """Map source-specific impact strings to our enum."""
    mapping = {
        # Forex Factory colours
        "high": ImpactLevel.HIGH,
        "red": ImpactLevel.HIGH,
        "medium": ImpactLevel.MEDIUM,
        "orange": ImpactLevel.MEDIUM,
        "low": ImpactLevel.LOW,
        "yellow": ImpactLevel.LOW,
        "non-economic": ImpactLevel.LOW,
        "gray": ImpactLevel.LOW,
        "grey": ImpactLevel.LOW,
        # Trading Economics
        "3": ImpactLevel.HIGH,
        "2": ImpactLevel.MEDIUM,
        "1": ImpactLevel.LOW,
    }
    return mapping.get(raw.lower().strip(), ImpactLevel.MEDIUM)
