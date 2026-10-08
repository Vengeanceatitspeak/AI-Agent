"""Policy Engine — the safety gate between the LLM and tool execution.

For every tool call, evaluates:
1. Tier resolution: tool_override → server risk_default → global default (L3)
2. Argument-level rules: path sandboxing, URL filtering, shell patterns
3. Taint escalation: untrusted content → escalate L2+ to CONFIRM
4. Rate limits and budgets
5. Returns ALLOW, CONFIRM(reason), or DENY(reason)
6. Writes audit record regardless of outcome
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from jarvis.config import (
    AppConfig,
    PolicyAction,
    PolicyConfig,
    RiskTier,
    ServerConfig,
)
from jarvis.llm.base import ToolCall
from jarvis.policy.audit import AuditLog
from jarvis.policy.limits import RateLimiter
from jarvis.policy.taint import TaintTracker
from jarvis.policy.tiers import resolve_tier_behavior

logger = structlog.get_logger()


class PolicyDecision:
    """Result of a policy evaluation."""

    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"

    def __init__(
        self,
        action: str,
        tier: RiskTier,
        reason: str = "",
    ) -> None:
        self.action = action
        self.tier = tier
        self.reason = reason

    @property
    def is_allowed(self) -> bool:
        return self.action == self.ALLOW

    @property
    def needs_confirmation(self) -> bool:
        return self.action == self.CONFIRM

    @property
    def is_denied(self) -> bool:
        return self.action == self.DENY

    def __repr__(self) -> str:
        return f"PolicyDecision({self.action}, {self.tier.value}, {self.reason!r})"


class PolicyEngine:
    """Central safety evaluation engine.

    Evaluates every tool call before execution and records the decision
    in the audit log.
    """

    def __init__(
        self,
        config: AppConfig,
        audit_log: AuditLog,
        taint_tracker: TaintTracker | None = None,
    ) -> None:
        self._config = config
        self._policy = config.policy
        self._audit = audit_log
        self._taint = taint_tracker or TaintTracker(
            untrusted_sources=self._policy.taint.untrusted_sources
        )
        self._rate_limiter = RateLimiter(
            global_calls_per_minute=self._policy.rate_limits.global_limits.calls_per_minute,
            global_max_concurrent=self._policy.rate_limits.global_limits.max_concurrent,
            per_server_calls_per_minute=30,  # Default per-server
            per_server_max_concurrent=5,
        )
        self._panic_mode = False

        # Build server config lookup
        self._server_configs: dict[str, ServerConfig] = {
            s.name: s for s in config.servers.servers
        }

    @property
    def panic_mode(self) -> bool:
        return self._panic_mode

    def activate_panic(self) -> None:
        """Activate panic mode — deny all L2+ actions."""
        self._panic_mode = True
        logger.critical("panic_mode_activated")

    def deactivate_panic(self) -> None:
        """Deactivate panic mode."""
        self._panic_mode = False
        logger.info("panic_mode_deactivated")

    def reset_taint(self) -> None:
        """Reset taint state for a new request."""
        self._taint.reset()

    def mark_tainted(self, source: str) -> None:
        """Explicitly mark context as tainted."""
        self._taint.mark_tainted(source)

    @property
    def taint_tracker(self) -> TaintTracker:
        return self._taint

    async def evaluate(
        self,
        tool_call: ToolCall,
        *,
        trace_id: str = "",
        origin: str = "user",
    ) -> PolicyDecision:
        """Evaluate a tool call against the policy.

        Args:
            tool_call: The tool call to evaluate.
            trace_id: Request trace ID for audit.
            origin: Where the request came from.

        Returns:
            PolicyDecision with action and reason.
        """
        log = logger.bind(
            trace_id=trace_id,
            tool=tool_call.name,
        )

        # Parse namespaced name
        server_name, original_tool = self._parse_tool_name(tool_call.name)

        # Step 1: Resolve tier
        tier = self._resolve_tier(server_name, original_tool)

        # Step 2: Panic mode check
        if self._panic_mode and tier.value >= RiskTier.L2.value:
            decision = PolicyDecision(
                PolicyDecision.DENY,
                tier,
                "PANIC MODE: All L2+ actions are denied.",
            )
            await self._audit_decision(
                trace_id, server_name, original_tool,
                tool_call.arguments, tier, decision, origin,
            )
            return decision

        # Step 3: Rate limit check
        allowed, rate_reason = self._rate_limiter.check(server_name)
        if not allowed:
            decision = PolicyDecision(
                PolicyDecision.DENY,
                tier,
                rate_reason,
            )
            await self._audit_decision(
                trace_id, server_name, original_tool,
                tool_call.arguments, tier, decision, origin,
            )
            return decision

        # Step 4: Tier-based behavior
        behavior = resolve_tier_behavior(tier, self._policy)

        # Step 5: Argument-level rules
        arg_result = self._check_argument_rules(
            server_name, original_tool, tool_call.arguments
        )
        if arg_result:
            decision = PolicyDecision(
                PolicyDecision.DENY,
                tier,
                arg_result,
            )
            await self._audit_decision(
                trace_id, server_name, original_tool,
                tool_call.arguments, tier, decision, origin,
            )
            return decision

        # Step 6: Taint escalation
        if (
            self._taint.is_tainted
            and self._policy.taint.enabled
            and tier.value >= RiskTier.L2.value
            and behavior.action == PolicyAction.ALLOW
        ):
            log.info(
                "taint_escalation",
                tier=tier.value,
                taint_source=self._taint.taint_source,
            )
            decision = PolicyDecision(
                PolicyDecision.CONFIRM,
                tier,
                f"Context is tainted (source: {self._taint.taint_source}). "
                f"Confirming L2+ action in tainted context.",
            )
            await self._audit_decision(
                trace_id, server_name, original_tool,
                tool_call.arguments, tier, decision, origin, tainted=True,
            )
            return decision

        # Step 7: Apply tier behavior
        if behavior.action == PolicyAction.DENY:
            decision = PolicyDecision(
                PolicyDecision.DENY,
                tier,
                f"Tool '{original_tool}' is at tier {tier.value} which is "
                f"denied by policy.",
            )
        elif behavior.action == PolicyAction.CONFIRM or behavior.confirm:
            decision = PolicyDecision(
                PolicyDecision.CONFIRM,
                tier,
                f"Tool '{original_tool}' is at tier {tier.value} "
                f"({behavior.description}) — requires confirmation.",
            )
        else:
            decision = PolicyDecision(
                PolicyDecision.ALLOW,
                tier,
            )

        await self._audit_decision(
            trace_id, server_name, original_tool,
            tool_call.arguments, tier, decision, origin,
        )

        log.debug(
            "policy_decision",
            action=decision.action,
            tier=tier.value,
            reason=decision.reason,
        )

        return decision

    async def record_result(
        self,
        tool_call: ToolCall,
        *,
        trace_id: str,
        success: bool,
        duration_ms: float,
    ) -> None:
        """Record the outcome of a tool call (post-execution).

        This releases the rate limit slot.
        """
        server_name, _ = self._parse_tool_name(tool_call.name)
        self._rate_limiter.release(server_name)

    def _parse_tool_name(self, namespaced: str) -> tuple[str, str]:
        """Parse 'server.tool' into (server_name, original_tool)."""
        if "." in namespaced:
            parts = namespaced.split(".", 1)
            return parts[0], parts[1]
        return "", namespaced

    def _resolve_tier(self, server_name: str, tool_name: str) -> RiskTier:
        """Resolve the risk tier for a tool.

        Priority: explicit tool_override → server risk_default → global default.
        """
        server_config = self._server_configs.get(server_name)

        if server_config:
            # Check tool overrides
            if tool_name in server_config.tool_overrides:
                override = server_config.tool_overrides[tool_name]
                return override.risk

            # Server default
            return server_config.risk_default

        # Global default for unknown servers
        return self._policy.defaults.unknown_server_tier

    def _check_argument_rules(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> str:
        """Check argument-level rules (path sandboxing, URL filtering).

        Returns:
            Empty string if OK, or a denial reason.
        """
        rules = self._policy.argument_rules

        # Path sandboxing
        if rules.path_sandboxing.enabled:
            for key, value in arguments.items():
                if key in ("path", "source", "destination", "file", "dir"):
                    if isinstance(value, str):
                        for pattern in rules.path_sandboxing.deny_patterns:
                            if pattern in value:
                                return (
                                    f"Path '{value}' is blocked by sandbox "
                                    f"rule (pattern: {pattern})"
                                )

        # URL filtering
        if rules.url_rules.enabled:
            for key, value in arguments.items():
                if key in ("url", "uri", "endpoint"):
                    if isinstance(value, str):
                        for blocked in rules.url_rules.block_internal_ranges:
                            # Simple prefix check for CIDR ranges
                            range_prefix = blocked.split("/")[0].rsplit(".", 1)[0]
                            if value.startswith(f"http://{range_prefix}") or \
                               value.startswith(f"https://{range_prefix}"):
                                return (
                                    f"URL '{value}' is blocked by "
                                    f"internal range: {blocked}"
                                )

        return ""

    async def _audit_decision(
        self,
        trace_id: str,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        tier: RiskTier,
        decision: PolicyDecision,
        origin: str,
        *,
        tainted: bool = False,
    ) -> None:
        """Write an audit record for a policy decision."""
        try:
            await self._audit.record(
                trace_id=trace_id,
                server=server,
                tool=tool,
                arguments=arguments,
                tier=tier,
                decision=decision.action,
                origin=origin,
                tainted=tainted,
                notes=decision.reason,
            )
        except Exception as e:
            logger.error("audit_record_failed", error=str(e))
