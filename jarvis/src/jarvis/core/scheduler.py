"""Scheduler — time and condition-based triggers.

Wraps APScheduler for cron-like and interval triggers that emit events
through the event bus. Proactive runs go through the agent loop with
restricted policy tiers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import structlog
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from jarvis.core.events import EventBus, Event

logger = structlog.get_logger()


@dataclass
class TriggerSpec:
    """Specification for a scheduled trigger."""
    name: str
    schedule: str  # e.g. "0 8 * * 1-5"
    prompt: str
    tier_limit: str = "L1"
    enabled: bool = True


class Scheduler:
    """Time-based trigger scheduler."""

    def __init__(self, event_bus: EventBus, config: Any = None) -> None:
        self._event_bus = event_bus
        self._config = config
        self._triggers: dict[str, TriggerSpec] = {}
        self._scheduler = AsyncIOScheduler()
        self._running = False

    def _parse_cron(self, expr: str) -> dict[str, Any]:
        """Parse 5-part cron into kwargs for CronTrigger."""
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {expr}")
        return {
            "minute": parts[0],
            "hour": parts[1],
            "day": parts[2],
            "month": parts[3],
            "day_of_week": parts[4]
        }

    def _is_quiet_hours(self) -> bool:
        """Check if quiet hours are currently active."""
        if not self._config or not self._config.policy.proactive.quiet_hours.enabled:
            return False
            
        qh = self._config.policy.proactive.quiet_hours
        now = datetime.now()
        start_time = datetime.strptime(qh.start, "%H:%M").time()
        end_time = datetime.strptime(qh.end, "%H:%M").time()
        current_time = now.time()

        if start_time <= end_time:
            return start_time <= current_time <= end_time
        else: # Crosses midnight
            return current_time >= start_time or current_time <= end_time

    async def _trigger_job(self, trigger_name: str) -> None:
        """Execute a trigger by emitting an event."""
        if self._is_quiet_hours():
            logger.info("trigger_skipped_quiet_hours", trigger=trigger_name)
            return

        spec = self._triggers.get(trigger_name)
        if not spec or not spec.enabled:
            return
            
        logger.info("trigger_fired", trigger=trigger_name)
        await self._event_bus.emit(Event(
            type="scheduler.trigger",
            source="scheduler",
            payload={"name": trigger_name, "prompt": spec.prompt, "tier_limit": spec.tier_limit}
        ))

    def add_trigger(self, trigger: TriggerSpec) -> None:
        """Register a trigger."""
        self._triggers[trigger.name] = trigger
        if self._running:
            self._schedule_trigger(trigger)

    def _schedule_trigger(self, trigger: TriggerSpec) -> None:
        if not trigger.enabled:
            return
        cron_kwargs = self._parse_cron(trigger.schedule)
        self._scheduler.add_job(
            self._trigger_job,
            CronTrigger(**cron_kwargs),
            args=[trigger.name],
            id=trigger.name,
            replace_existing=True
        )

    def remove_trigger(self, name: str) -> None:
        """Remove a trigger."""
        self._triggers.pop(name, None)
        if self._scheduler.get_job(name):
            self._scheduler.remove_job(name)

    async def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            return
            
        self._running = True
        for trigger in self._triggers.values():
            self._schedule_trigger(trigger)
            
        self._scheduler.start()
        logger.info("scheduler_started", triggers=len(self._triggers))

    async def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return
            
        self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("scheduler_stopped")
