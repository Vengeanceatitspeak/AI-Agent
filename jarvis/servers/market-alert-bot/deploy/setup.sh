#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# deploy/setup.sh — One-shot VPS setup script
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "═══════════════════════════════════════════════"
echo "  Market Alert Bot — VPS Setup"
echo "═══════════════════════════════════════════════"

# 1. Create virtual environment
echo "[1/5] Creating Python virtual environment…"
cd "$PROJECT_DIR"
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
echo "[2/5] Installing dependencies…"
pip install --upgrade pip
pip install -r requirements.txt

# 3. Create directories
echo "[3/5] Creating directories…"
mkdir -p database logs sessions

# 4. Check for .env
if [ ! -f .env ]; then
    echo "[4/5] ⚠ No .env file found. Copying template…"
    cp .env.example .env
    echo "     → EDIT .env with your real credentials before starting!"
else
    echo "[4/5] ✓ .env file exists"
fi

# 5. Install systemd service
echo "[5/5] Installing systemd service…"
sudo cp "$SCRIPT_DIR/market-alert-bot.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable market-alert-bot
echo "     → Service installed and enabled"

echo ""
echo "═══════════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "    1. Edit .env with your real API keys"
echo "    2. Test manually:  python main.py"
echo "    3. Start service:  sudo systemctl start market-alert-bot"
echo "    4. View logs:      sudo journalctl -u market-alert-bot -f"
echo "═══════════════════════════════════════════════"
