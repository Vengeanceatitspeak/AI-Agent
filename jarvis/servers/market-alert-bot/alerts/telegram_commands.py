# ──────────────────────────────────────────────────────────────
# alerts/telegram_commands.py — Telegram bot command handler
# ──────────────────────────────────────────────────────────────
"""
Listens for incoming Telegram messages from the user (via Bot API long
polling) and handles commands:

  /mute <event_id_prefix>  — silence further alerts for a matching event
  /ack <event_id_prefix>   — alias for /mute
  /status                  — show bot health / uptime
  /instruments             — list currently monitored instruments

This runs as a background asyncio task alongside the main polling loops.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from config import INSTRUMENTS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from alerts.telegram_message import mute_event

logger = logging.getLogger(__name__)

_LAST_UPDATE_ID = 0
_START_TIME = datetime.now(timezone.utc)


async def start_command_listener() -> None:
    """Long-poll Telegram Bot API for incoming commands."""
    global _LAST_UPDATE_ID

    if not TELEGRAM_BOT_TOKEN:
        logger.warning("No TELEGRAM_BOT_TOKEN — command listener disabled")
        return

    logger.info("Telegram command listener started")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    while True:
        try:
            params = {
                "offset": _LAST_UPDATE_ID + 1,
                "timeout": 30,
                "allowed_updates": ["message"],
            }
            timeout = aiohttp.ClientTimeout(total=40)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(5)
                        continue
                    data = await resp.json()

            for update in data.get("result", []):
                _LAST_UPDATE_ID = update["update_id"]
                msg = update.get("message", {})
                text = (msg.get("text") or "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                # Only accept commands from our configured chat
                if chat_id != TELEGRAM_CHAT_ID:
                    continue

                if text.startswith("/mute") or text.startswith("/ack"):
                    await _handle_mute(text, chat_id)
                elif text == "/status":
                    await _handle_status(chat_id)
                elif text == "/instruments":
                    await _handle_instruments(chat_id)

        except asyncio.CancelledError:
            break
        except Exception:
            logger.debug("Command listener error", exc_info=True)
            await asyncio.sleep(5)


async def _handle_mute(text: str, chat_id: str) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await _reply(chat_id, "Usage: /mute <event_id_prefix>")
        return
    prefix = parts[1].strip()
    mute_event(prefix)
    await _reply(chat_id, f"✅ Muted events matching: {prefix}")


async def _handle_status(chat_id: str) -> None:
    uptime = datetime.now(timezone.utc) - _START_TIME
    hours = int(uptime.total_seconds() // 3600)
    minutes = int((uptime.total_seconds() % 3600) // 60)
    await _reply(
        chat_id,
        f"🟢 Market Alert Bot running\n"
        f"⏱ Uptime: {hours}h {minutes}m\n"
        f"📊 Instruments: {', '.join(INSTRUMENTS.keys())}",
    )


async def _handle_instruments(chat_id: str) -> None:
    lines = ["📊 <b>Monitored Instruments:</b>"]
    for inst, curs in INSTRUMENTS.items():
        lines.append(f"  • {inst} → {', '.join(curs)}")
    await _reply(chat_id, "\n".join(lines))


async def _reply(chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await session.post(url, json=payload)
    except Exception:
        logger.debug("Failed to send reply", exc_info=True)
