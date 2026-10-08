# ──────────────────────────────────────────────────────────────
# config.py — Central configuration, loaded from .env + defaults
# ──────────────────────────────────────────────────────────────
import os
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env ────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

# ── Groq ─────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = "llama-3.3-70b-versatile"
GROQ_TIMEOUT: int = 15  # seconds
GROQ_MAX_RETRIES: int = 3

# ── Telegram Bot (text messages) ─────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Telegram MTProto (voice calls) ──────────────────────────
TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_PHONE_A: str = os.getenv("TELEGRAM_PHONE_A", "")
TELEGRAM_PHONE_B: str = os.getenv("TELEGRAM_PHONE_B", "")
TELEGRAM_SESSION_DIR: Path = BASE_DIR / "sessions"

# ── Voice channel selection ──────────────────────────────────
VOICE_CHANNEL: str = "telegram_call"

# ── Instruments & currency mapping ───────────────────────────
INSTRUMENTS: dict[str, list[str]] = {
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"],
    "NAS100": ["USD"],
}

# Flat set of all monitored currencies (auto-derived)
MONITORED_CURRENCIES: set[str] = set()
for _curs in INSTRUMENTS.values():
    MONITORED_CURRENCIES.update(_curs)

# ── Alert toggles ────────────────────────────────────────────
LOW_IMPACT_ALERT: bool = False       # True → send Telegram msg for LOW
MEDIUM_IMPACT_ALERT: bool = True     # True → send Telegram msg for MEDIUM
HIGH_IMPACT_CALL: bool = True        # True → voice call for HIGH
BREAKING_ALERT: bool = True          # True → msg + call for BREAKING

# ── Polling intervals (seconds) ─────────────────────────────
FOREX_FACTORY_POLL_INTERVAL: int = int(os.getenv("FF_POLL_INTERVAL", "20"))
TRADING_ECONOMICS_POLL_INTERVAL: int = int(os.getenv("TE_POLL_INTERVAL", "30"))
BREAKING_NEWS_POLL_INTERVAL: int = int(os.getenv("BN_POLL_INTERVAL", "15"))

# ── Cooldown windows (seconds) ──────────────────────────────
SCHEDULED_COOLDOWN: int = 300   # 5 min for scheduled data
BREAKING_COOLDOWN: int = 60     # 1 min for breaking news (shorter per §3)

# ── Database ─────────────────────────────────────────────────
DATABASE_DIR: Path = BASE_DIR / "database"
DATABASE_PATH: Path = DATABASE_DIR / "events.db"

# ── Logging ──────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR: Path = BASE_DIR / "logs"

# ── Timezone ─────────────────────────────────────────────────
DISPLAY_TIMEZONE: str = "Asia/Kolkata"  # IST for alert timestamps
