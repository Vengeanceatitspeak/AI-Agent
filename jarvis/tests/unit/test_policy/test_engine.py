"""Tests for the policy engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config import AppConfig, PolicyAction, RiskTier
from jarvis.llm.base import ToolCall
from jarvis.policy.audit import AuditLog
from jarvis.policy.engine import PolicyDecision, PolicyEngine
from jarvis.policy.taint import TaintTracker


@pytest.fixture
async def audit_log(tmp_path: Path) -> AuditLog:
    log = AuditLog(tmp_path / "test_audit.db")
    await log.initialize()
    return log


@pytest.fixture
def policy_engine(app_config: AppConfig, audit_log: AuditLog) -> PolicyEngine:
    return PolicyEngine(config=app_config, audit_log=audit_log)


class TestPolicyEngine:
    """Test the policy engine evaluation flow."""

    @pytest.mark.asyncio
    async def test_l0_auto_allow(self, policy_engine: PolicyEngine) -> None:
        """L0 tools are auto-allowed."""
        tc = ToolCall(id="tc1", name="fs.list_dir", arguments={"path": "."})
        decision = await policy_engine.evaluate(tc, trace_id="tr_test")
        assert decision.is_allowed

    @pytest.mark.asyncio
    async def test_unknown_tool_gets_l3(self, policy_engine: PolicyEngine) -> None:
        """Unknown tools default to L3 (confirm)."""
        tc = ToolCall(id="tc1", name="unknown.mysterious_tool", arguments={})
        decision = await policy_engine.evaluate(tc, trace_id="tr_test")
        assert decision.tier == RiskTier.L3
        assert decision.needs_confirmation

    @pytest.mark.asyncio
    async def test_panic_mode_denies_l2_plus(self, policy_engine: PolicyEngine) -> None:
        """Panic mode denies all L2+ actions."""
        policy_engine.activate_panic()
        tc = ToolCall(id="tc1", name="fs.write_file", arguments={"path": "test.txt"})
        decision = await policy_engine.evaluate(tc, trace_id="tr_test")
        assert decision.is_denied
        assert "PANIC" in decision.reason
        policy_engine.deactivate_panic()

    @pytest.mark.asyncio
    async def test_taint_escalation(self, policy_engine: PolicyEngine) -> None:
        """Tainted context escalates L2+ to CONFIRM."""
        policy_engine.mark_tainted("web.fetch_page")
        tc = ToolCall(id="tc1", name="fs.write_file", arguments={"path": "test.txt"})
        decision = await policy_engine.evaluate(tc, trace_id="tr_test")
        # Should require confirmation due to taint
        assert decision.needs_confirmation or decision.is_denied

    @pytest.mark.asyncio
    async def test_path_sandbox(self, policy_engine: PolicyEngine) -> None:
        """Blocked path prefixes are denied."""
        tc = ToolCall(id="tc1", name="fs.read_file", arguments={"path": "/etc/passwd"})
        decision = await policy_engine.evaluate(tc, trace_id="tr_test")
        assert decision.is_denied
        assert "blocked" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_tool_override_tier(self, policy_engine: PolicyEngine) -> None:
        """Tool overrides in servers.yaml are respected."""
        # fs server has list_dir overridden to L1 in servers.yaml
        tc = ToolCall(id="tc1", name="fs.list_dir", arguments={"path": "."})
        decision = await policy_engine.evaluate(tc, trace_id="tr_test")
        assert decision.tier == RiskTier.L1
        assert decision.is_allowed  # L1 = auto-allow


class TestTaintTracker:
    """Test taint tracking."""

    def test_initially_clean(self) -> None:
        tracker = TaintTracker()
        assert tracker.is_tainted is False

    def test_mark_tainted(self) -> None:
        tracker = TaintTracker()
        tracker.mark_tainted("web.fetch")
        assert tracker.is_tainted is True
        assert tracker.taint_source == "web.fetch"

    def test_check_source_untrusted(self) -> None:
        tracker = TaintTracker(untrusted_sources=["web"])
        result = tracker.check_source("web", "fetch")
        assert result is True
        assert tracker.is_tainted is True

    def test_check_source_trusted(self) -> None:
        tracker = TaintTracker(untrusted_sources=["web"])
        result = tracker.check_source("fs", "read_file")
        assert result is False
        assert tracker.is_tainted is False

    def test_reset(self) -> None:
        tracker = TaintTracker()
        tracker.mark_tainted("web")
        tracker.reset()
        assert tracker.is_tainted is False

    def test_wrap_content(self) -> None:
        tracker = TaintTracker()
        wrapped = tracker.wrap_untrusted_content("Hello", "web.fetch")
        assert "UNTRUSTED CONTENT" in wrapped
        assert "Hello" in wrapped
        assert tracker.is_tainted is True


class TestAuditLog:
    """Test audit log operations."""

    @pytest.mark.asyncio
    async def test_record_and_tail(self, tmp_path: Path) -> None:
        log = AuditLog(tmp_path / "audit.db")
        await log.initialize()

        row_id = await log.record(
            trace_id="tr_123",
            server="fs",
            tool="read_file",
            arguments={"path": "test.txt"},
            tier=RiskTier.L1,
            decision="allow",
        )
        assert row_id > 0

        entries = await log.tail(limit=5)
        assert len(entries) == 1
        assert entries[0]["trace_id"] == "tr_123"
        assert entries[0]["tool"] == "read_file"

    @pytest.mark.asyncio
    async def test_search(self, tmp_path: Path) -> None:
        log = AuditLog(tmp_path / "audit.db")
        await log.initialize()

        await log.record(
            trace_id="tr_a", server="fs", tool="read_file",
            arguments={}, tier=RiskTier.L1, decision="allow",
        )
        await log.record(
            trace_id="tr_b", server="web", tool="search",
            arguments={}, tier=RiskTier.L0, decision="allow",
        )

        results = await log.search("web")
        assert len(results) == 1
        assert results[0]["server"] == "web"

    @pytest.mark.asyncio
    async def test_secret_redaction(self, tmp_path: Path) -> None:
        log = AuditLog(tmp_path / "audit.db")
        await log.initialize()

        await log.record(
            trace_id="tr_secret",
            server="api",
            tool="call",
            arguments={"api_key": "sk-super-secret-key-12345"},
            tier=RiskTier.L2,
            decision="allow",
        )

        entries = await log.tail()
        assert "[REDACTED]" in entries[0]["arguments"]

    @pytest.mark.asyncio
    async def test_get_by_trace(self, tmp_path: Path) -> None:
        log = AuditLog(tmp_path / "audit.db")
        await log.initialize()

        await log.record(
            trace_id="tr_same", server="fs", tool="a",
            arguments={}, tier=RiskTier.L0, decision="allow",
        )
        await log.record(
            trace_id="tr_same", server="fs", tool="b",
            arguments={}, tier=RiskTier.L1, decision="allow",
        )
        await log.record(
            trace_id="tr_other", server="fs", tool="c",
            arguments={}, tier=RiskTier.L0, decision="allow",
        )

        entries = await log.get_by_trace("tr_same")
        assert len(entries) == 2
