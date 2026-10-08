# ──────────────────────────────────────────────────────────────
# alerts/router.py — Alert routing engine (§6)
# ──────────────────────────────────────────────────────────────
"""
Routes each classified event to the correct alert channel(s):

  LOW      → log only (no notification)
  MEDIUM   → Telegram text message only
  HIGH     → Telegram text message + voice call
  BREAKING → Telegram text message + voice call (always, §3)
"""
from __future__ import annotations

import logging

from config import (
    BREAKING_ALERT,
    HIGH_IMPACT_CALL,
    LOW_IMPACT_ALERT,
    MEDIUM_IMPACT_ALERT,
)
from news.normalizer import ImpactLevel, NewsEvent

from alerts.telegram_message import send_alert as send_text
from alerts.telegram_voice_call import place_voice_alert

logger = logging.getLogger(__name__)


async def route_alert(event: NewsEvent) -> dict:
    """
    Send alert(s) based on the event's final_impact.
    Returns a dict with the outcome of each channel.
    """
    result = {"text_sent": False, "call_placed": False, "skipped": False}

    level = event.final_impact

    if level == ImpactLevel.LOW:
        if LOW_IMPACT_ALERT:
            result["text_sent"] = await send_text(event)
        else:
            logger.info("LOW impact — log only: %s", event.event_id)
            result["skipped"] = True
        return result

    if level == ImpactLevel.MEDIUM:
        if MEDIUM_IMPACT_ALERT:
            result["text_sent"] = await send_text(event)
        return result

    if level == ImpactLevel.HIGH:
        result["text_sent"] = await send_text(event)
        if HIGH_IMPACT_CALL:
            result["call_placed"] = await place_voice_alert(event)
        return result

    if level == ImpactLevel.BREAKING:
        # §3: ALWAYS both text and call, never downgrade
        if BREAKING_ALERT:
            result["text_sent"] = await send_text(event)
            result["call_placed"] = await place_voice_alert(event)
        return result

    return result
