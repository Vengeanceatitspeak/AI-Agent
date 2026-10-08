"""Rate limits and budgets for tool calls."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


@dataclass
class RateLimitState:
    """State for a rate limiter."""

    calls_per_minute: int = 60
    max_concurrent: int = 10
    _call_times: list[float] = field(default_factory=list)
    _active_calls: int = 0

    def check_and_record(self) -> bool:
        """Check if a call is allowed and record it.

        Returns:
            True if allowed, False if rate limited.
        """
        now = time.monotonic()

        # Prune old entries (older than 60s)
        cutoff = now - 60.0
        self._call_times = [t for t in self._call_times if t > cutoff]

        # Check rate limit
        if len(self._call_times) >= self.calls_per_minute:
            return False

        # Check concurrent limit
        if self._active_calls >= self.max_concurrent:
            return False

        self._call_times.append(now)
        self._active_calls += 1
        return True

    def release(self) -> None:
        """Release a concurrent call slot."""
        self._active_calls = max(0, self._active_calls - 1)


class RateLimiter:
    """Rate limiter for tool calls, per-server and global."""

    def __init__(
        self,
        global_calls_per_minute: int = 60,
        global_max_concurrent: int = 10,
        per_server_calls_per_minute: int = 30,
        per_server_max_concurrent: int = 5,
    ) -> None:
        self._global = RateLimitState(
            calls_per_minute=global_calls_per_minute,
            max_concurrent=global_max_concurrent,
        )
        self._per_server: dict[str, RateLimitState] = {}
        self._default_cpm = per_server_calls_per_minute
        self._default_concurrent = per_server_max_concurrent

    def _get_server_limiter(self, server: str) -> RateLimitState:
        if server not in self._per_server:
            self._per_server[server] = RateLimitState(
                calls_per_minute=self._default_cpm,
                max_concurrent=self._default_concurrent,
            )
        return self._per_server[server]

    def check(self, server: str) -> tuple[bool, str]:
        """Check if a call to a server is allowed.

        Returns:
            Tuple of (allowed, reason_if_denied).
        """
        # Check global
        if not self._global.check_and_record():
            return False, "Global rate limit exceeded"

        # Check per-server
        server_limiter = self._get_server_limiter(server)
        if not server_limiter.check_and_record():
            self._global.release()  # Undo global increment
            return False, f"Rate limit exceeded for server '{server}'"

        return True, ""

    def release(self, server: str) -> None:
        """Release call slots after completion."""
        self._global.release()
        if server in self._per_server:
            self._per_server[server].release()
