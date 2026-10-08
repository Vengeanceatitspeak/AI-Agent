#!/usr/bin/env python3
"""JARVIS Doctor — standalone entry point.

Can also be run via: jarvis doctor
"""

import sys
from pathlib import Path

# Add src to path if running standalone
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jarvis.scripts.doctor import run_doctor


if __name__ == "__main__":
    # Find config dir
    script_dir = Path(__file__).parent.parent
    config_dir = script_dir / "config"
    if not config_dir.exists():
        config_dir = Path.cwd() / "config"
    run_doctor(config_dir)
