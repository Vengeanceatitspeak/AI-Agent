# ──────────────────────────────────────────────────────────────
# news/__init__.py — Pluggable source registry
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from news.normalizer import NewsEvent


class BaseNewsSource(ABC):
    """Every news source module must implement this interface."""

    name: str = "base"

    @abstractmethod
    async def fetch(self) -> List[NewsEvent]:
        """Return a list of *new* normalised events since the last call."""
        ...
