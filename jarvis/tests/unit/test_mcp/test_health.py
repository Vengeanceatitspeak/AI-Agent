"""Tests for server health tracking and circuit breaker."""

from __future__ import annotations

import time

import pytest

from jarvis.mcp_client.health import CircuitBreaker, ServerHealth, ServerStatus


class TestCircuitBreaker:
    """Test circuit breaker behavior."""

    def test_starts_closed(self) -> None:
        cb = CircuitBreaker()
        assert cb.state == "closed"
        assert cb.should_allow() is True

    def test_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.state == "open"
        assert cb.should_allow() is False

    def test_success_resets(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == "closed"

    def test_half_open_after_recovery_timeout(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.02)  # Wait past recovery timeout
        assert cb.should_allow() is True
        assert cb.state == "half_open"

    def test_reset(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        cb.reset()
        assert cb.state == "closed"
        assert cb.failure_count == 0


class TestServerHealth:
    """Test server health tracking."""

    def test_initial_state(self) -> None:
        h = ServerHealth(server_name="test")
        assert h.status == ServerStatus.STOPPED
        assert h.is_available is False

    def test_mark_started(self) -> None:
        h = ServerHealth(server_name="test")
        h.mark_started()
        assert h.status == ServerStatus.HEALTHY
        assert h.is_available is True

    def test_call_success(self) -> None:
        h = ServerHealth(server_name="test")
        h.mark_started()
        h.record_call_success()
        assert h.total_calls == 1
        assert h.failed_calls == 0

    def test_degraded_after_failures(self) -> None:
        h = ServerHealth(server_name="test")
        h.mark_started()
        h.circuit_breaker.failure_threshold = 3
        for _ in range(3):
            h.record_call_failure("err")
        assert h.status == ServerStatus.DEGRADED

    def test_recovery_from_degraded(self) -> None:
        h = ServerHealth(server_name="test")
        h.mark_started()
        h.circuit_breaker.failure_threshold = 2
        h.record_call_failure("err")
        h.record_call_failure("err")
        assert h.status == ServerStatus.DEGRADED
        # Reset breaker and record success
        h.circuit_breaker.reset()
        h.record_call_success()
        assert h.status == ServerStatus.HEALTHY

    def test_mark_failed(self) -> None:
        h = ServerHealth(server_name="test")
        h.mark_failed("crash")
        assert h.status == ServerStatus.FAILED
        assert h.is_available is False
        assert h.error_message == "crash"
