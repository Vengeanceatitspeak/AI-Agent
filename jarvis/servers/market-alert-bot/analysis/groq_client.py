# ──────────────────────────────────────────────────────────────
# analysis/groq_client.py — LLM-powered direction/impact analysis
# ──────────────────────────────────────────────────────────────
"""
Sends normalised events to Groq's API (llama-3.3-70b-versatile) with a
strict system prompt that forces structured JSON output.

Safety:
  • Timeouts + retries — if Groq fails, we fall back to the rule-based
    impact tag so the alert is never blocked.
  • The Groq analysis is combined with the rule-based tag to produce the
    FINAL impact level used for routing.

Groq JSON schema returned:
{
    "direction": "bullish" | "bearish" | "neutral",
    "currency": "USD",
    "confidence": 0.0–1.0,
    "magnitude": "low" | "medium" | "high",
    "reasoning": "one short sentence"
}
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import aiohttp

from config import GROQ_API_KEY, GROQ_MAX_RETRIES, GROQ_MODEL, GROQ_TIMEOUT
from news.normalizer import ImpactLevel, NewsCategory, NewsEvent

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

_SYSTEM_PROMPT = """\
You are a professional forex / macro-economic analyst.
You will receive a financial news event with its details.

Your job: analyse the event and predict its likely directional impact on the
specified currency.

You MUST respond ONLY with valid JSON in this exact schema — no markdown,
no explanation, no wrapper text:
{
    "direction": "bullish" | "bearish" | "neutral",
    "currency": "<3-letter code>",
    "confidence": <float 0.0 to 1.0>,
    "magnitude": "low" | "medium" | "high",
    "action_advice": "<short imperative like 'Close USD longs' or 'Hold positions'>",
    "reasoning": "<one short sentence>"
}

Rules:
- "direction" is the expected impact on the SPECIFIED CURRENCY (not a pair).
- "confidence" reflects how certain you are (0 = pure guess, 1 = near-certain).
- "magnitude" is how large the expected price move is.
- "action_advice" must be a quick, direct instruction for a trader (e.g., "Close exposed positions", "Hold positions", "Prepare for volatility").
- Keep "reasoning" under 100 characters.
- If data is insufficient, set direction to "neutral" and confidence to 0.3.
"""


async def analyze(event: NewsEvent) -> NewsEvent:
    """
    Call Groq LLM to analyse the event.  Populates groq_* fields on the
    event and may adjust final_impact.  Falls back gracefully on failure.
    """
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set — skipping LLM analysis")
        return event

    user_msg = _build_user_message(event)

    for attempt in range(1, GROQ_MAX_RETRIES + 1):
        try:
            result = await _call_groq(user_msg)
            if result:
                event.groq_direction = result.get("direction")
                event.groq_confidence = result.get("confidence")
                event.groq_magnitude = result.get("magnitude")
                event.groq_action_advice = result.get("action_advice")
                event.groq_reasoning = result.get("reasoning")
                _adjust_final_impact(event, result)
                logger.info(
                    "Groq analysis for %s: %s %s (conf=%.2f)",
                    event.event_id,
                    result.get("direction"),
                    result.get("currency"),
                    result.get("confidence", 0),
                )
                return event
        except asyncio.TimeoutError:
            logger.warning("Groq timeout (attempt %d/%d)", attempt, GROQ_MAX_RETRIES)
        except Exception:
            logger.exception("Groq error (attempt %d/%d)", attempt, GROQ_MAX_RETRIES)

        if attempt < GROQ_MAX_RETRIES:
            await asyncio.sleep(1.5 * attempt)  # exponential-ish backoff

    logger.warning(
        "Groq failed after %d attempts for %s — using rule-based impact only",
        GROQ_MAX_RETRIES,
        event.event_id,
    )
    return event


# ── Internal helpers ─────────────────────────────────────────

def _build_user_message(event: NewsEvent) -> str:
    """Compose a concise prompt from the event's data."""
    parts = [f"Event: {event.title}", f"Currency: {event.currency}"]

    if event.category == NewsCategory.SCHEDULED:
        if event.actual:
            parts.append(f"Actual: {event.actual}")
        if event.forecast:
            parts.append(f"Forecast: {event.forecast}")
        if event.previous:
            parts.append(f"Previous: {event.previous}")
    else:
        if event.headline:
            parts.append(f"Headline: {event.headline}")
        if event.summary:
            parts.append(f"Summary: {event.summary[:300]}")
        parts.append(f"Source reliability: {event.source_reliability}")

    parts.append(f"Rule-based impact: {event.final_impact.value}")
    return "\n".join(parts)


async def _call_groq(user_msg: str) -> Optional[dict]:
    """Make a single Groq API call and parse the JSON response."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
    }

    timeout = aiohttp.ClientTimeout(total=GROQ_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(GROQ_API_URL, json=payload, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Groq API HTTP %d: %s", resp.status, body[:200])
                return None
            data = await resp.json()

    content = data["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Groq returned non-JSON: %s", content[:200])
        return None


def _adjust_final_impact(event: NewsEvent, groq: dict) -> None:
    """
    Combine rule-based impact with Groq's magnitude+confidence to produce
    the final routing-level impact.

    Logic:
      • If Groq says "high" magnitude with confidence ≥ 0.6,
        upgrade to HIGH (or keep BREAKING).
      • If Groq says "low" magnitude with confidence ≥ 0.7,
        downgrade HIGH → MEDIUM (but never downgrade BREAKING).
      • Otherwise keep the rule-based tag.
    """
    magnitude = (groq.get("magnitude") or "").lower()
    confidence = groq.get("confidence", 0.5)

    # Never downgrade BREAKING (§3)
    if event.final_impact == ImpactLevel.BREAKING:
        return

    if magnitude == "high" and confidence >= 0.6:
        if event.final_impact in (ImpactLevel.LOW, ImpactLevel.MEDIUM):
            event.final_impact = ImpactLevel.HIGH
    elif magnitude == "low" and confidence >= 0.7:
        if event.final_impact == ImpactLevel.HIGH:
            event.final_impact = ImpactLevel.MEDIUM
