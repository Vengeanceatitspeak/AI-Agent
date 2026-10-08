"""Event bus — simple async pub/sub for internal events.

Typed events with source tracking for decoupled communication between
Core components (scheduler, MCP notifications, file watchers, etc.).

Full implementation in Phase 6.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine


class EventSeverity(str, Enum):
    """Event severity levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Event:
    """A typed event in the system.

    Attributes:
        type: Event type string (e.g., 'market.alert', 'server.started').
        source: Who emitted this event.
        payload: Event-specific data.
        severity: How important this event is.
        timestamp: When the event occurred.
    """

    type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    severity: EventSeverity = EventSeverity.INFO
    timestamp: datetime = field(default_factory=datetime.now)


# Type alias for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Simple async pub/sub event bus.

    Full implementation in Phase 6.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._global_handlers: list[EventHandler] = []

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe to events of a specific type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe to all events."""
        self._global_handlers.append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Unsubscribe from an event type."""
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h is not handler
            ]

    async def emit(self, event: Event) -> None:
        """Emit an event to all subscribers."""
        handlers = list(self._global_handlers)
        if event.type in self._handlers:
            handlers.extend(self._handlers[event.type])

        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                # Log but don't crash — events should not break the system
                pass
