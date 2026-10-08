"""Tests for the scheduler."""

from __future__ import annotations

import pytest
from unittest.mock import Mock

from jarvis.core.events import EventBus
from jarvis.core.scheduler import Scheduler, TriggerSpec


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()

@pytest.fixture
def scheduler(event_bus: EventBus) -> Scheduler:
    # Use a dummy config where quiet hours are disabled
    config = Mock()
    config.policy.proactive.quiet_hours.enabled = False
    return Scheduler(event_bus=event_bus, config=config)


class TestScheduler:
    
    def test_parse_cron(self, scheduler: Scheduler) -> None:
        kwargs = scheduler._parse_cron("0 8 * * 1-5")
        assert kwargs == {
            "minute": "0",
            "hour": "8",
            "day": "*",
            "month": "*",
            "day_of_week": "1-5"
        }

    def test_parse_invalid_cron(self, scheduler: Scheduler) -> None:
        with pytest.raises(ValueError):
            scheduler._parse_cron("0 8 *")

    @pytest.mark.asyncio
    async def test_trigger_job_emits_event(self, scheduler: Scheduler, event_bus: EventBus) -> None:
        events = []
        
        async def handler(event):
            events.append(event)
            
        event_bus.subscribe("scheduler.trigger", handler)
        
        spec = TriggerSpec(name="test_trigger", schedule="* * * * *", prompt="Do something")
        scheduler.add_trigger(spec)
        
        await scheduler._trigger_job("test_trigger")
        
        assert len(events) == 1
        assert events[0].payload["name"] == "test_trigger"
        assert events[0].payload["prompt"] == "Do something"
