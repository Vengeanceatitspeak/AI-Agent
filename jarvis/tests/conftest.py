"""Shared test fixtures for JARVIS tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.config import AppConfig
from jarvis.llm.fake import FakeLLMProvider


@pytest.fixture
def config_dir() -> Path:
    """Path to the test config directory (uses the real config files)."""
    # Walk up to find the project root
    current = Path(__file__).parent
    while current != current.parent:
        candidate = current / "config"
        if (candidate / "jarvis.yaml").exists():
            return candidate
        current = current.parent
    # Fallback
    return Path("config")


@pytest.fixture
def app_config(config_dir: Path) -> AppConfig:
    """Load the app configuration from test config files."""
    return AppConfig.load(config_dir=config_dir)


@pytest.fixture
def fake_llm() -> FakeLLMProvider:
    """Create a fresh FakeLLMProvider instance."""
    return FakeLLMProvider()


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory."""
    data_dir = tmp_path / "jarvis_data"
    data_dir.mkdir()
    return data_dir
