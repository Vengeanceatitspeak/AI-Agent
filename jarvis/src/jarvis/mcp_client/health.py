"""MCP Server health checks, reconnection, and circuit breaker."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

import structlog

logger = structlog.get_logger()


class ServerStatus(str, Enum):
    """Status of an MCP server."""

    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass
class CircuitBreaker:
    """Circuit breaker for an MCP server.

    After `failure_threshold` consecutive failures, the circuit opens
    and stops routing to the server. After `recovery_timeout` seconds,
    it enters half-open state and allows a single probe call.

    Attributes:
        failure_threshold: Number of consecutive failures before opening.
        recovery_timeout: Seconds to wait before probing.
        failure_count: Current consecutive failure count.
        last_failure_time: Timestamp of most recent failure.
        state: Current circuit state (closed, open, half_open).
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    failure_count: int = 0
    last_failure_time: float = 0.0
    state: str = "closed"  # closed, open, half_open

    def record_success(self) -> None:
        """Record a successful call — reset the breaker."""
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self) -> None:
        """Record a failed call — potentially open the circuit."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.warning(
                "circuit_breaker_opened",
                failure_count=self.failure_count,
            )

    def should_allow(self) -> bool:
        """Check if a call should be allowed through."""
        if self.state == "closed":
            return True
        if self.state == "open":
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = "half_open"
                logger.info("circuit_breaker_half_open")
                return True
            return False
        # half_open: allow one probe
        return True

    def reset(self) -> None:
        """Force-reset the circuit breaker."""
        self.failure_count = 0
        self.state = "closed"
        self.last_failure_time = 0.0


@dataclass
class ServerHealth:
    """Health state for an MCP server.

    Attributes:
        server_name: Server identifier.
        status: Current status.
        circuit_breaker: Per-server circuit breaker.
        last_health_check: Timestamp of last health check.
        uptime_start: When the server started.
        total_calls: Total tool calls made.
        failed_calls: Total failed tool calls.
        error_message: Most recent error message.
    """

    server_name: str
    status: ServerStatus = ServerStatus.STOPPED
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    last_health_check: float = 0.0
    uptime_start: float = 0.0
    total_calls: int = 0
    failed_calls: int = 0
    error_message: str = ""

    def record_call_success(self) -> None:
        """Record a successful tool call."""
        self.total_calls += 1
        self.circuit_breaker.record_success()
        if self.status == ServerStatus.DEGRADED:
            self.status = ServerStatus.HEALTHY
            logger.info("server_recovered", server=self.server_name)

    def record_call_failure(self, error: str = "") -> None:
        """Record a failed tool call."""
        self.total_calls += 1
        self.failed_calls += 1
        self.error_message = error
        self.circuit_breaker.record_failure()
        if self.circuit_breaker.state == "open":
            self.status = ServerStatus.DEGRADED
            logger.warning(
                "server_degraded",
                server=self.server_name,
                error=error,
            )

    def mark_started(self) -> None:
        """Mark server as started."""
        self.status = ServerStatus.HEALTHY
        self.uptime_start = time.monotonic()
        self.circuit_breaker.reset()
        self.error_message = ""

    def mark_stopped(self) -> None:
        """Mark server as stopped."""
        self.status = ServerStatus.STOPPED

    def mark_failed(self, error: str = "") -> None:
        """Mark server as failed."""
        self.status = ServerStatus.FAILED
        self.error_message = error

    @property
    def is_available(self) -> bool:
        """Whether the server is available for tool calls."""
        return (
            self.status in (ServerStatus.HEALTHY, ServerStatus.DEGRADED)
            and self.circuit_breaker.should_allow()
        )
