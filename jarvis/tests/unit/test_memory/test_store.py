"""Tests for the memory store."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.memory.store import Fact, MemoryStore, SearchResult


@pytest.fixture
async def memory(tmp_path: Path) -> MemoryStore:
    store = MemoryStore(tmp_path / "test_memory.db")
    await store.initialize()
    return store


class TestMemoryStore:
    """Test memory storage operations."""

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, memory: MemoryStore) -> None:
        fact_id = await memory.store_fact(
            key="user.name",
            value="Tony Stark",
            source="user",
            confidence=1.0,
            tags=["personal"],
        )
        assert fact_id > 0

        fact = await memory.get_fact("user.name")
        assert fact is not None
        assert fact.value == "Tony Stark"
        assert fact.source == "user"
        assert "personal" in fact.tags

    @pytest.mark.asyncio
    async def test_upsert(self, memory: MemoryStore) -> None:
        """Same key updates rather than duplicates."""
        id1 = await memory.store_fact(key="color", value="blue")
        id2 = await memory.store_fact(key="color", value="red")
        assert id1 == id2  # Same ID = update

        fact = await memory.get_fact("color")
        assert fact is not None
        assert fact.value == "red"

    @pytest.mark.asyncio
    async def test_search_fts(self, memory: MemoryStore) -> None:
        await memory.store_fact(key="pref.coffee", value="Double espresso, no sugar")
        await memory.store_fact(key="pref.tea", value="Earl Grey, hot")
        await memory.store_fact(key="work.project", value="Arc reactor redesign")

        results = await memory.search("espresso")
        assert len(results) >= 1
        assert results[0].fact.key == "pref.coffee"

    @pytest.mark.asyncio
    async def test_list_facts(self, memory: MemoryStore) -> None:
        await memory.store_fact(key="a", value="1")
        await memory.store_fact(key="b", value="2", tags=["test"])
        await memory.store_fact(key="c", value="3", tags=["test"])

        all_facts = await memory.list_facts()
        assert len(all_facts) == 3

        tagged = await memory.list_facts(tag="test")
        assert len(tagged) == 2

    @pytest.mark.asyncio
    async def test_delete(self, memory: MemoryStore) -> None:
        fact_id = await memory.store_fact(key="temp", value="delete me")
        assert await memory.delete_fact(fact_id) is True
        assert await memory.get_fact("temp") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, memory: MemoryStore) -> None:
        assert await memory.delete_fact(99999) is False

    @pytest.mark.asyncio
    async def test_store_summary(self, memory: MemoryStore) -> None:
        sid = await memory.store_summary(
            session_id="s_test",
            summary="User asked about weather and I provided a forecast.",
            turn_range="1-5",
            token_count=150,
        )
        assert sid > 0

        summaries = await memory.get_summaries(session_id="s_test")
        assert len(summaries) == 1
        assert "weather" in summaries[0]["summary"]

    @pytest.mark.asyncio
    async def test_export(self, memory: MemoryStore) -> None:
        await memory.store_fact(key="x", value="y")
        await memory.store_summary(session_id="s_1", summary="Test")

        data = await memory.export_all()
        assert len(data["facts"]) == 1
        assert len(data["summaries"]) == 1

    @pytest.mark.asyncio
    async def test_get_by_id(self, memory: MemoryStore) -> None:
        fact_id = await memory.store_fact(key="byid", value="test")
        fact = await memory.get_fact_by_id(fact_id)
        assert fact is not None
        assert fact.key == "byid"

    @pytest.mark.asyncio
    async def test_confidence(self, memory: MemoryStore) -> None:
        await memory.store_fact(key="guess", value="maybe", confidence=0.6)
        fact = await memory.get_fact("guess")
        assert fact is not None
        assert fact.confidence == 0.6
