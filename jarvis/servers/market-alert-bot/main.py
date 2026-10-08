# ──────────────────────────────────────────────────────────────
# main.py — 24/7 Market Alert Bot orchestrator
# ──────────────────────────────────────────────────────────────
"""
Entry point for the Market Alert Bot.

Runs an asyncio event loop with concurrent tasks:
  • Forex Factory poller     (primary, highest frequency)
  • Trading Economics poller  (secondary)
  • Breaking News RSS poller  (multiple feeds, shortest cooldown)
  • Telegram command listener (long-poll for /mute, /status, etc.)

Each poller fetches → filters → classifies → analyses (Groq) → routes alerts
→ logs to SQLite.  The pipeline is:

  fetch() → currency_filter → instrument_tagger → impact_classifier
          → groq_analysis → dedup/cooldown check → alert router → DB log
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List

# ── Project imports ──────────────────────────────────────────
import config
from analysis.groq_client import analyze as groq_analyze
from alerts.router import route_alert
from alerts.telegram_commands import start_command_listener
from alerts.telegram_message import is_muted
from database.event_store import EventStore
from filters.currency_filter import filter_events
from filters.impact_filter import classify_all
from filters.instrument_filter import tag_all
from news.forex_factory import ForexFactorySource
from news.trading_economics import TradingEconomicsSource
from news.breaking_news import BreakingNewsSource
from news.normalizer import NewsEvent

# ── Logging setup ────────────────────────────────────────────

def _setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.LOG_DIR / "market_alert.log"

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler (10 MB, keep 5 backups)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(fmt)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    root.addHandler(file_handler)
    root.addHandler(console_handler)


logger = logging.getLogger("main")

# ── Pipeline ─────────────────────────────────────────────────

async def process_events(
    events: List[NewsEvent],
    store: EventStore,
) -> None:
    """Run a batch of raw events through the full pipeline."""

    # 1. Currency relevance filter
    relevant = filter_events(events)
    if not relevant:
        return

    # 2. Tag affected instruments
    relevant = tag_all(relevant)

    # 3. Rule-based impact classification
    relevant = classify_all(relevant)

    for event in relevant:
        # 4. Duplicate check
        if store.is_duplicate(event.event_id):
            logger.debug("Duplicate skipped: %s", event.event_id)
            continue

        # 5. Cooldown check
        if store.is_in_cooldown(event):
            logger.debug("Cooldown active for: %s", event.event_id)
            continue

        # 6. Mute check (§3)
        if is_muted(event.event_id):
            logger.debug("Muted: %s", event.event_id)
            continue

        # 7. Groq LLM analysis (second pass)
        event = await groq_analyze(event)

        # 8. Route alert
        result = await route_alert(event)

        # 9. Log to database
        store.log_event(
            event,
            alert_sent=result.get("text_sent", False),
            call_placed=result.get("call_placed", False),
        )

        logger.info(
            "Processed: %s | %s | %s | alert=%s call=%s",
            event.event_id,
            event.currency,
            event.final_impact.value,
            result.get("text_sent"),
            result.get("call_placed"),
        )


# ── Poller tasks ─────────────────────────────────────────────

async def poll_source(source, interval: int, store: EventStore, name: str) -> None:
    """Generic polling loop for any BaseNewsSource."""
    logger.info("Starting %s poller (interval=%ds)", name, interval)
    while True:
        try:
            events = await source.fetch()
            if events:
                logger.info("%s returned %d new event(s)", name, len(events))
                await process_events(events, store)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in %s poller", name)

        await asyncio.sleep(interval)


# ── Main ─────────────────────────────────────────────────────

async def main() -> None:
    _setup_logging()
    logger.info("=" * 60)
    logger.info("  Market Alert Bot starting")
    logger.info("  Instruments: %s", ", ".join(config.INSTRUMENTS.keys()))
    logger.info("  Currencies:  %s", ", ".join(sorted(config.MONITORED_CURRENCIES)))
    logger.info("  Voice channel: %s", config.VOICE_CHANNEL)
    logger.info("  Groq model: %s", config.GROQ_MODEL)
    logger.info("=" * 60)

    # Ensure directories exist
    config.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    config.TELEGRAM_SESSION_DIR.mkdir(parents=True, exist_ok=True)

    # Initialise event store
    store = EventStore()

    # Initialise sources
    ff_source = ForexFactorySource()
    te_source = TradingEconomicsSource()
    bn_source = BreakingNewsSource()

    # Create tasks
    tasks = [
        asyncio.create_task(
            poll_source(
                ff_source,
                config.FOREX_FACTORY_POLL_INTERVAL,
                store,
                "ForexFactory",
            ),
            name="ff_poller",
        ),
        asyncio.create_task(
            poll_source(
                te_source,
                config.TRADING_ECONOMICS_POLL_INTERVAL,
                store,
                "TradingEconomics",
            ),
            name="te_poller",
        ),
        asyncio.create_task(
            poll_source(
                bn_source,
                config.BREAKING_NEWS_POLL_INTERVAL,
                store,
                "BreakingNews",
            ),
            name="bn_poller",
        ),
        asyncio.create_task(
            start_command_listener(),
            name="cmd_listener",
        ),
    ]

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("All pollers running — press Ctrl+C to stop")
    await shutdown_event.wait()

    # Cancel all tasks
    logger.info("Shutting down…")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # Cleanup
    await ff_source.close()
    await te_source.close()
    await bn_source.close()
    store.close()
    logger.info("Market Alert Bot stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
