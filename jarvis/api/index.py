"""Vercel entry point for Jimmy."""

import sys
import os

# Add src to path so Vercel can find the jarvis package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from jarvis.server import create_app

app = create_app()
