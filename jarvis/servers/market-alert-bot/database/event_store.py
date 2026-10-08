# ──────────────────────────────────────────────────────────────
# database/event_store.py — SQLite event log (§8)
# ──────────────────────────────────────────────────────────────
"""
Persistent event store backed by SQLite.

Responsibilities:
  • Log every processed event (timestamp, currency, impact, Groq output, etc.)
  • Duplicate prevention — check by event_id before processing
  • Cooldown enforcement — enforce minimum gap between repeated alerts
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import BREAKING_COOLDOWN, DATABASE_DIR, DATABASE_PATH, SCHEDULED_COOLDOWN
from news.normalizer import NewsCategory, NewsEvent

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL,
    title           TEXT NOT NULL,
    currency        TEXT NOT NULL,
    source          TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    actual          TEXT,
    forecast        TEXT,
    previous        TEXT,
    source_impact   TEXT,
    final_impact    TEXT,
    category        TEXT,
    headline        TEXT,
    summary         TEXT,
    source_reliability TEXT,
    groq_direction  TEXT,
    groq_confidence REAL,
    groq_magnitude  TEXT,
    groq_action_advice TEXT,
    groq_reasoning  TEXT,
    affected_instruments TEXT,
    alert_sent      INTEGER DEFAULT 0,
    call_placed     INTEGER DEFAULT 0,
    processed_at    TEXT NOT NULL,
    UNIQUE(event_id)
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_event_id ON events(event_id);
CREATE INDEX IF NOT EXISTS idx_currency_ts ON events(currency, timestamp);
CREATE INDEX IF NOT EXISTS idx_processed_at ON events(processed_at);
"""


class EventStore:
    """Thread-safe SQLite event store."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or DATABASE_PATH
        DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_CREATE_TABLE_SQL + _CREATE_INDEX_SQL)
        self._conn.commit()

    # ── Duplicate check ──────────────────────────────────────

    def is_duplicate(self, event_id: str) -> bool:
        """Return True if this event_id has already been processed."""
        cur = self._conn.execute(
            "SELECT 1 FROM events WHERE event_id = ?", (event_id,)
        )
        return cur.fetchone() is not None

    # ── Cooldown check ───────────────────────────────────────

    def is_in_cooldown(self, event: NewsEvent) -> bool:
        """
        Return True if an alert for the same event title + currency was
        sent within the cooldown window.

        Uses a shorter window for BREAKING (§3).
        """
        if event.category == NewsCategory.BREAKING:
            cooldown = BREAKING_COOLDOWN
        else:
            cooldown = SCHEDULED_COOLDOWN

        cur = self._conn.execute(
            """
            SELECT processed_at FROM events
            WHERE currency = ? AND title = ? AND alert_sent = 1
            ORDER BY processed_at DESC LIMIT 1
            """,
            (event.currency, event.title),
        )
        row = cur.fetchone()
        if row is None:
            return False

        try:
            last_ts = datetime.fromisoformat(row[0])
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
            return elapsed < cooldown
        except Exception:
            return False

    # ── Insert ───────────────────────────────────────────────

    def log_event(
        self,
        event: NewsEvent,
        alert_sent: bool = False,
        call_placed: bool = False,
    ) -> None:
        """Insert or ignore (on duplicate event_id) the event into the DB."""
        d = event.as_dict()
        try:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_id, title, currency, source, timestamp,
                    actual, forecast, previous,
                    source_impact, final_impact, category,
                    headline, summary, source_reliability,
                    groq_direction, groq_confidence, groq_magnitude, groq_action_advice, groq_reasoning,
                    affected_instruments, alert_sent, call_placed, processed_at
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    d["event_id"], d["title"], d["currency"], d["source"],
                    d["timestamp"], d["actual"], d["forecast"], d["previous"],
                    d["source_impact"], d["final_impact"], d["category"],
                    d["headline"], d["summary"], d["source_reliability"],
                    d["groq_direction"], d["groq_confidence"],
                    d["groq_magnitude"], d["groq_action_advice"], d["groq_reasoning"],
                    d["affected_instruments"],
                    int(alert_sent), int(call_placed),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self._conn.commit()
        except Exception:
            logger.exception("Failed to log event %s", event.event_id)

    # ── Cleanup ──────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()
