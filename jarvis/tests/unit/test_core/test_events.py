"""Tests for the event bus."""

from __future__ import annotations

import pytest

from jarvis.core.events import Event, EventBus, EventSeverity


class TestEventBus:
    """Test the async event bus."""

    @pytest.mark.asyncio
    async def test_subscribe_and_emit(self) -> None:
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test.event", handler)
        event = Event(type="test.event", source="test", payload={"key": "value"})
        await bus.emit(event)

        assert len(received) == 1
        assert received[0].payload["key"] == "value"

    @pytest.mark.asyncio
    async def test_subscribe_all(self) -> None:
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe_all(handler)
        await bus.emit(Event(type="a", source="test"))
        await bus.emit(Event(type="b", source="test"))

        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_unsubscribe(self) -> None:
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test", handler)
        bus.unsubscribe("test", handler)
        await bus.emit(Event(type="test", source="test"))

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_handler_error_does_not_crash(self) -> None:
        bus = EventBus()
        call_count = 0

        async def bad_handler(event: Event) -> None:
            raise RuntimeError("Handler error")

        async def good_handler(event: Event) -> None:
            nonlocal call_count
            call_count += 1

        bus.subscribe("test", bad_handler)
        bus.subscribe("test", good_handler)
        await bus.emit(Event(type="test", source="test"))

        # Good handler still called despite bad handler error
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_no_subscribers_is_fine(self) -> None:
        bus = EventBus()
        # Should not raise
        await bus.emit(Event(type="nobody.listening", source="test"))
