"""Tests for policy tiers."""

from __future__ import annotations

import pytest

from jarvis.config import PolicyAction, PolicyConfig, RiskTier
from jarvis.policy.tiers import (
    DEFAULT_TIER_BEHAVIORS,
    TierBehavior,
    resolve_tier_behavior,
)


class TestDefaultTierBehaviors:
    """Test that default tier behaviors match the spec."""

    def test_l0_auto_allow_no_audit(self) -> None:
        b = DEFAULT_TIER_BEHAVIORS[RiskTier.L0]
        assert b.action == PolicyAction.ALLOW
        assert b.audit is False
        assert b.confirm is False

    def test_l1_auto_allow_with_audit(self) -> None:
        b = DEFAULT_TIER_BEHAVIORS[RiskTier.L1]
        assert b.action == PolicyAction.ALLOW
        assert b.audit is True
        assert b.confirm is False

    def test_l2_auto_allow_with_audit(self) -> None:
        b = DEFAULT_TIER_BEHAVIORS[RiskTier.L2]
        assert b.action == PolicyAction.ALLOW
        assert b.audit is True
        assert b.confirm is False

    def test_l3_requires_confirmation(self) -> None:
        b = DEFAULT_TIER_BEHAVIORS[RiskTier.L3]
        assert b.action == PolicyAction.CONFIRM
        assert b.audit is True
        assert b.confirm is True

    def test_l4_deny_by_default(self) -> None:
        b = DEFAULT_TIER_BEHAVIORS[RiskTier.L4]
        assert b.action == PolicyAction.DENY
        assert b.audit is True
        assert b.confirm is True
        assert b.cooldown_seconds == 30


class TestResolveTierBehavior:
    """Test tier behavior resolution from config."""

    def test_uses_defaults_when_no_config(self) -> None:
        policy = PolicyConfig()
        behavior = resolve_tier_behavior(RiskTier.L0, policy)
        assert behavior.action == PolicyAction.ALLOW

    def test_config_overrides_defaults(self) -> None:
        from jarvis.config import RiskTierConfig

        policy = PolicyConfig(
            risk_tiers={
                "L1": RiskTierConfig(
                    action=PolicyAction.CONFIRM,
                    audit=True,
                    confirm=True,
                    description="Custom L1",
                ),
            }
        )
        behavior = resolve_tier_behavior(RiskTier.L1, policy)
        assert behavior.action == PolicyAction.CONFIRM
        assert behavior.confirm is True
