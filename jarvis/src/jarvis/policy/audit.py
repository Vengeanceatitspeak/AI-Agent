"""Audit log — append-only record of all policy decisions and tool calls.

Stores: timestamp, trace_id, user input hash, server, tool, arguments
(secrets redacted), tier, decision, confirmer, result status, duration.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

from jarvis.config import RiskTier

logger = structlog.get_logger()

# Patterns that look like secrets (redacted in audit)
_SECRET_PATTERNS = [
    re.compile(r'(?i)(api[_-]?key|token|secret|password|credential|auth)["\']?\s*[:=]\s*["\']?([^"\'}\s,]{8,})'),
    re.compile(r'(?i)sk-[a-zA-Z0-9]{20,}'),
    re.compile(r'(?i)Bearer\s+[a-zA-Z0-9\-._~+/]+=*'),
]


def _redact_secrets(text: str) -> str:
    """Redact potential secrets from text."""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets from tool arguments."""
    redacted = {}
    for key, value in args.items():
        if any(s in key.lower() for s in ("key", "token", "secret", "password", "auth")):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, str):
            redacted[key] = _redact_secrets(value)
        else:
            redacted[key] = value
    return redacted


class AuditLog:
    """Append-only audit log backed by SQLite.

    Records all tool call policy decisions and outcomes.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._initialized = False

    async def initialize(self) -> None:
        """Create the audit table if it doesn't exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    input_hash TEXT DEFAULT '',
                    server TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    arguments TEXT DEFAULT '{}',
                    tier TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    confirmer TEXT DEFAULT '',
                    result_status TEXT DEFAULT '',
                    duration_ms REAL DEFAULT 0,
                    origin TEXT DEFAULT 'user',
                    tainted INTEGER DEFAULT 0,
                    notes TEXT DEFAULT ''
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_trace
                ON audit_log(trace_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp
                ON audit_log(timestamp)
            """)
            await db.commit()
        self._initialized = True

    async def record(
        self,
        trace_id: str,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        tier: RiskTier | str,
        decision: str,
        *,
        input_hash: str = "",
        confirmer: str = "",
        result_status: str = "",
        duration_ms: float = 0,
        origin: str = "user",
        tainted: bool = False,
        notes: str = "",
    ) -> int:
        """Record an audit entry.

        Returns:
            The row ID of the new entry.
        """
        if not self._initialized:
            await self.initialize()

        tier_str = tier.value if isinstance(tier, RiskTier) else str(tier)
        redacted_args = json.dumps(_redact_args(arguments))
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                """
                INSERT INTO audit_log
                (timestamp, trace_id, input_hash, server, tool, arguments,
                 tier, decision, confirmer, result_status, duration_ms,
                 origin, tainted, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now, trace_id, input_hash, server, tool, redacted_args,
                    tier_str, decision, confirmer, result_status, duration_ms,
                    origin, int(tainted), notes,
                ),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def tail(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get the most recent audit entries."""
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in reversed(rows)]

    async def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search audit log by tool name, server, or trace_id."""
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM audit_log
                WHERE tool LIKE ? OR server LIKE ? OR trace_id LIKE ?
                    OR notes LIKE ?
                ORDER BY id DESC LIMIT ?
                """,
                (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """Get all audit entries for a trace."""
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM audit_log WHERE trace_id = ? ORDER BY id",
                (trace_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
