# ──────────────────────────────────────────────────────────────
# alerts/telegram_message.py — Telegram Bot API text messages
# ──────────────────────────────────────────────────────────────
"""
Sends formatted Telegram text alerts via Bot API (BotFather token).
Also handles the /mute and /ack commands from the user (§3).

Message format (§6):
🚨 HIGH IMPACT | 🇺🇸 USD | CPI
Actual 3.4% vs Forecast 3.2% (Prev 3.3%)
Groq view: BEARISH USD (confidence 0.78)
Affects: EURUSD, GBPUSD, USDJPY, NAS100
⏱ 19:30 IST — CHECK OPEN POSITIONS
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Set

import aiohttp
import pytz

from config import DISPLAY_TIMEZONE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from news.normalizer import ImpactLevel, NewsCategory, NewsEvent

logger = logging.getLogger(__name__)

# ── Currency flag mapping ────────────────────────────────────
_FLAGS = {
    "USD": "🇺🇸",
    "EUR": "🇪🇺",
    "GBP": "🇬🇧",
    "JPY": "🇯🇵",
    "CAD": "🇨🇦",
    "AUD": "🇦🇺",
    "NZD": "🇳🇿",
    "CHF": "🇨🇭",
    "CNY": "🇨🇳",
}

_IMPACT_EMOJI = {
    ImpactLevel.LOW: "📊",
    ImpactLevel.MEDIUM: "⚠️",
    ImpactLevel.HIGH: "🚨",
    ImpactLevel.BREAKING: "🔴",
}

# Muted event IDs (populated via /mute command)
_muted_events: Set[str] = set()


def mute_event(event_id: str) -> None:
    """Silence further alerts for a specific event (§3 ack/mute)."""
    _muted_events.add(event_id)
    logger.info("Muted event: %s", event_id)


def is_muted(event_id: str) -> bool:
    return event_id in _muted_events


async def send_alert(event: NewsEvent) -> bool:
    """
    Send a formatted Telegram message for the given event.
    Returns True if sent successfully, False otherwise.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not set — skipping text alert")
        return False

    if is_muted(event.event_id):
        logger.info("Event %s is muted — skipping alert", event.event_id)
        return False

    text = _format_message(event)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    logger.info("Telegram alert sent for %s", event.event_id)
                    return True
                body = await resp.text()
                logger.warning(
                    "Telegram sendMessage failed HTTP %d: %s", resp.status, body[:200]
                )
                return False
    except Exception:
        logger.exception("Failed to send Telegram alert for %s", event.event_id)
        return False


# ── Message formatting ───────────────────────────────────────

def _format_message(event: NewsEvent) -> str:
    tz = pytz.timezone(DISPLAY_TIMEZONE)
    local_time = event.timestamp.astimezone(tz).strftime("%H:%M IST")

    emoji = _IMPACT_EMOJI.get(event.final_impact, "📊")
    flag = _FLAGS.get(event.currency, "🏳️")
    impact_label = event.final_impact.value

    lines = [
        f"{emoji} <b>{impact_label} IMPACT</b> | {flag} {event.currency} | {event.title}",
    ]

    # Scheduled data values
    if event.category == NewsCategory.SCHEDULED:
        parts = []
        if event.actual:
            parts.append(f"Actual {event.actual}")
        if event.forecast:
            parts.append(f"Forecast {event.forecast}")
        if event.previous:
            parts.append(f"(Prev {event.previous})")
        if parts:
            lines.append(" vs ".join(parts[:2]) + (" " + parts[2] if len(parts) > 2 else ""))
    else:
        # Breaking news
        if event.headline:
            lines.append(f"📰 {event.headline}")
        if event.source_reliability == "UNCONFIRMED":
            lines.append("⚠️ <b>UNCONFIRMED — single source</b>")
        else:
            lines.append("✅ <b>CONFIRMED</b>")

    # Groq analysis
    if event.groq_direction:
        direction = event.groq_direction.upper()
        conf = f"{event.groq_confidence:.2f}" if event.groq_confidence else "N/A"
        lines.append(f"🤖 Groq: <b>{direction}</b> {event.currency} (confidence {conf})")
        if event.groq_action_advice:
            lines.append(f"   └ ⚡ <b>ACTION:</b> {event.groq_action_advice}")
        if event.groq_reasoning:
            lines.append(f"   └ {event.groq_reasoning}")

    # Affected instruments
    if event.affected_instruments:
        lines.append(f"📈 Affects: {', '.join(event.affected_instruments)}")

    # Timestamp
    lines.append(f"⏱ {local_time} — <b>CHECK OPEN POSITIONS</b>")

    # Mute hint
    lines.append(f"\n<code>/mute {event.event_id[:16]}</code> to silence this event")

    return "\n".join(lines)
