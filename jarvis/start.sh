#!/bin/bash

# Start the News Scraper background worker in the background (&)
echo "Starting Market Alert Bot..."
python servers/market-alert-bot/main.py &

# Start Jimmy's Web Server in the foreground
echo "Starting Jimmy API Server..."
uvicorn src.jarvis.server:create_app --host 0.0.0.0 --port ${PORT:-10000}
