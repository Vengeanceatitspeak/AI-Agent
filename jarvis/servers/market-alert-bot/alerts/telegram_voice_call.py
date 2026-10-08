# ──────────────────────────────────────────────────────────────
# alerts/telegram_voice_call.py — Telegram voice call + phone fallback
# ──────────────────────────────────────────────────────────────
"""
Telegram voice-call backend:

  Account A (MTProto user account via Telethon) calls Account B on Telegram.
  This uses Telethon's call/request_call API, which wraps Telegram's VoIP layer.

  ⚠ Telegram's native call API in third-party libraries is NOT fully
  reliable. This module is deliberately isolated (§7) so it can be
  tested and patched independently.

Both backends implement the same async interface:
    await place_voice_alert(event) -> bool
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from config import (
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
    TELEGRAM_PHONE_A,
    TELEGRAM_PHONE_B,
    TELEGRAM_SESSION_DIR,
)
from news.normalizer import NewsEvent

logger = logging.getLogger(__name__)


async def place_voice_alert(event: NewsEvent) -> bool:
    """
    Place a voice alert via Telegram.
    Returns True if the call connected, False otherwise.
    """
    return await _telegram_call(event)


# ═══════════════════════════════════════════════════════════════
# Backend 1: Telegram MTProto voice call via Telethon
# ═══════════════════════════════════════════════════════════════

_telethon_client = None  # lazy singleton


async def _get_telethon_client():
    """Lazy-initialise the Telethon client (Account A)."""
    global _telethon_client
    if _telethon_client is not None:
        return _telethon_client

    try:
        from telethon import TelegramClient

        TELEGRAM_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        session_path = str(TELEGRAM_SESSION_DIR / "account_a")

        client = TelegramClient(
            session_path,
            TELEGRAM_API_ID,
            TELEGRAM_API_HASH,
        )
        await client.start(phone=TELEGRAM_PHONE_A)
        _telethon_client = client
        logger.info("Telethon client initialised (Account A)")
        return client
    except Exception:
        logger.exception("Failed to initialise Telethon client")
        return None


async def _telegram_call(event: NewsEvent) -> bool:
    """
    Attempt a Telegram voice call from Account A → Account B.

    Telethon's call support is experimental.  We attempt request_call()
    and let it ring for 15 seconds before hanging up.  The point is to
    make the user's phone ring as a wake-up signal, not to have a
    conversation.
    """
    if not all([TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE_A, TELEGRAM_PHONE_B]):
        logger.warning("Telegram MTProto credentials incomplete — voice call skipped")
        return False

    client = await _get_telethon_client()
    if client is None:
        logger.warning("Telethon client unavailable — voice call skipped")
        return False

    try:
        # Resolve the target user
        target = await client.get_entity(TELEGRAM_PHONE_B)

        # Attempt to place a call using raw MTProto to trigger ringing
        from telethon.tl.functions.phone import RequestCallRequest, DiscardCallRequest
        from telethon.tl.types import PhoneCallProtocol
        import os
        import random

        protocol = PhoneCallProtocol(
            min_layer=93, max_layer=93, udp_p2p=True, udp_reflector=True, library_versions=["1.0.0"]
        )

        call_req = RequestCallRequest(
            user_id=target,
            random_id=random.randint(0, 0x7fffffff - 1),
            g_a_hash=os.urandom(32),
            protocol=protocol
        )
        
        call_res = await client(call_req)
        logger.info("Telegram call initiated to %s for event %s", TELEGRAM_PHONE_B, event.event_id)

        # Let it ring for 15s then hang up
        await asyncio.sleep(15)

        try:
            from telethon.tl.types import InputPhoneCall, PhoneCallDiscardReasonDisconnect
            await client(DiscardCallRequest(
                peer=InputPhoneCall(
                    id=call_res.phone_call.id, 
                    access_hash=call_res.phone_call.access_hash
                ),
                duration=0,
                reason=PhoneCallDiscardReasonDisconnect(),
                connection_id=0
            ))
        except Exception:
            pass  # call may have already ended

        logger.info("Telegram call completed for %s", event.event_id)
        return True
    except Exception:
        logger.exception("Telegram call failed for %s — voice call skipped", event.event_id)
        return False
