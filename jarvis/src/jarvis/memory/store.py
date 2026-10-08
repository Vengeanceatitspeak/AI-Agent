"""Memory system — durable fact storage with semantic recall.

Three layers:
1. Conversation buffer — rolling window + summaries per session (handled in session.py)
2. Facts store — durable key facts with source, confidence, timestamps
3. Semantic recall — embeddings over summaries and notes for similarity search

Backed by SQLite + sqlite-vec for embeddings.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger()


@dataclass
class Fact:
    """A stored fact in long-term memory.

    Attributes:
        id: Unique identifier.
        key: Short key/label for the fact.
        value: The fact content.
        source: How this fact was learned (user, inferred, tool_result).
        confidence: Confidence level (0.0 - 1.0).
        created_at: When the fact was first stored.
        updated_at: When it was last updated.
        tags: Categorization tags.
        metadata: Additional metadata.
    """

    id: int = 0
    key: str = ""
    value: str = ""
    source: str = "user"
    confidence: float = 1.0
    created_at: str = ""
    updated_at: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Result from a memory search."""

    fact: Fact
    score: float = 0.0  # Relevance score (higher = more relevant)


class MemoryStore:
    """Durable fact storage with full-text search.

    Manages long-term memory for JARVIS — facts about the user,
    preferences, learned patterns, etc.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._initialized = False

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source TEXT DEFAULT 'user',
                    confidence REAL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key)
            """)
            # FTS5 for full-text search
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
                USING fts5(key, value, tags, content=facts, content_rowid=id)
            """)
            # Conversation summaries table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    turn_range TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0
                )
            """)
            await db.commit()
        self._initialized = True

    async def _ensure_init(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def store_fact(
        self,
        key: str,
        value: str,
        *,
        source: str = "user",
        confidence: float = 1.0,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Store a new fact or update an existing one.

        If a fact with the same key exists, it is updated.

        Returns:
            The fact ID.
        """
        await self._ensure_init()
        now = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(tags or [])
        meta_json = json.dumps(metadata or {})

        async with aiosqlite.connect(str(self._db_path)) as db:
            # Check for existing fact with same key
            cursor = await db.execute(
                "SELECT id FROM facts WHERE key = ?", (key,)
            )
            existing = await cursor.fetchone()

            if existing:
                fact_id = existing[0]
                await db.execute(
                    """
                    UPDATE facts SET value = ?, source = ?, confidence = ?,
                    updated_at = ?, tags = ?, metadata = ?
                    WHERE id = ?
                    """,
                    (value, source, confidence, now, tags_json, meta_json, fact_id),
                )
                # Update FTS
                await db.execute(
                    "DELETE FROM facts_fts WHERE rowid = ?", (fact_id,)
                )
                await db.execute(
                    "INSERT INTO facts_fts(rowid, key, value, tags) VALUES (?, ?, ?, ?)",
                    (fact_id, key, value, tags_json),
                )
                logger.debug("fact_updated", key=key, id=fact_id)
            else:
                cursor = await db.execute(
                    """
                    INSERT INTO facts (key, value, source, confidence,
                    created_at, updated_at, tags, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key, value, source, confidence, now, now, tags_json, meta_json),
                )
                fact_id = cursor.lastrowid or 0
                # Populate FTS
                await db.execute(
                    "INSERT INTO facts_fts(rowid, key, value, tags) VALUES (?, ?, ?, ?)",
                    (fact_id, key, value, tags_json),
                )
                logger.debug("fact_stored", key=key, id=fact_id)

            await db.commit()
            return fact_id

    async def get_fact(self, key: str) -> Fact | None:
        """Get a fact by key."""
        await self._ensure_init()

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM facts WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            if row:
                return self._row_to_fact(row)
        return None

    async def get_fact_by_id(self, fact_id: int) -> Fact | None:
        """Get a fact by ID."""
        await self._ensure_init()

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM facts WHERE id = ?", (fact_id,)
            )
            row = await cursor.fetchone()
            if row:
                return self._row_to_fact(row)
        return None

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search facts using full-text search.

        Args:
            query: Search query.
            limit: Maximum results.

        Returns:
            List of search results sorted by relevance.
        """
        await self._ensure_init()

        results: list[SearchResult] = []
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            # FTS search with ranking
            cursor = await db.execute(
                """
                SELECT f.*, rank
                FROM facts_fts fts
                JOIN facts f ON f.id = fts.rowid
                WHERE facts_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            )
            rows = await cursor.fetchall()
            for row in rows:
                fact = self._row_to_fact(row)
                score = -float(row["rank"])  # FTS5 rank is negative
                results.append(SearchResult(fact=fact, score=score))

        return results

    async def list_facts(
        self,
        limit: int = 100,
        tag: str | None = None,
    ) -> list[Fact]:
        """List all facts, optionally filtered by tag.

        Args:
            limit: Maximum results.
            tag: Optional tag to filter by.

        Returns:
            List of facts.
        """
        await self._ensure_init()

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            if tag:
                cursor = await db.execute(
                    """
                    SELECT * FROM facts
                    WHERE tags LIKE ?
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (f'%"{tag}"%', limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM facts ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [self._row_to_fact(row) for row in rows]

    async def delete_fact(self, fact_id: int) -> bool:
        """Delete a fact by ID.

        Returns:
            True if the fact was deleted.
        """
        await self._ensure_init()

        async with aiosqlite.connect(str(self._db_path)) as db:
            # Delete from FTS
            await db.execute(
                "DELETE FROM facts_fts WHERE rowid = ?", (fact_id,)
            )
            cursor = await db.execute(
                "DELETE FROM facts WHERE id = ?", (fact_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def store_summary(
        self,
        session_id: str,
        summary: str,
        turn_range: str = "",
        token_count: int = 0,
    ) -> int:
        """Store a conversation summary.

        Returns:
            The summary ID.
        """
        await self._ensure_init()
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                """
                INSERT INTO summaries (session_id, summary, turn_range,
                created_at, token_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, summary, turn_range, now, token_count),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_summaries(
        self,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get conversation summaries.

        Args:
            session_id: Filter by session (None = all).
            limit: Maximum results.
        """
        await self._ensure_init()

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            if session_id:
                cursor = await db.execute(
                    "SELECT * FROM summaries WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM summaries ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def export_all(self) -> dict[str, Any]:
        """Export all memory data as JSON-serializable dict."""
        await self._ensure_init()
        facts = await self.list_facts(limit=10000)
        summaries = await self.get_summaries(limit=10000)
        return {
            "facts": [
                {
                    "id": f.id, "key": f.key, "value": f.value,
                    "source": f.source, "confidence": f.confidence,
                    "created_at": f.created_at, "updated_at": f.updated_at,
                    "tags": f.tags,
                }
                for f in facts
            ],
            "summaries": summaries,
        }

    def _row_to_fact(self, row: Any) -> Fact:
        """Convert a database row to a Fact."""
        tags = []
        try:
            tags = json.loads(row["tags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            pass

        metadata = {}
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        return Fact(
            id=row["id"],
            key=row["key"],
            value=row["value"],
            source=row["source"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            tags=tags,
            metadata=metadata,
        )
