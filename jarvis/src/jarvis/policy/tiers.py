"""Risk tiers — definitions and lookup.

Maps RiskTier enum values to their configured behaviors.
"""

from __future__ import annotations

from dataclasses import dataclass
from jarvis.config import PolicyAction, PolicyConfig, RiskTier


@dataclass
class TierBehavior:
    """Resolved behavior for a risk tier.

    Attributes:
        tier: The risk tier.
        action: Default action (allow, confirm, deny).
        audit: Whether to write an audit record.
        confirm: Whether user confirmation is needed.
        cooldown_seconds: Cooldown before re-execution.
        description: Human-readable tier description.
    """

    tier: RiskTier
    action: PolicyAction
    audit: bool = True
    confirm: bool = False
    cooldown_seconds: int = 0
    description: str = ""


# Default tier behaviors (used when policy.yaml doesn't define them)
DEFAULT_TIER_BEHAVIORS: dict[RiskTier, TierBehavior] = {
    RiskTier.L0: TierBehavior(
        tier=RiskTier.L0,
        action=PolicyAction.ALLOW,
        audit=False,
        description="Public/harmless read",
    ),
    RiskTier.L1: TierBehavior(
        tier=RiskTier.L1,
        action=PolicyAction.ALLOW,
        audit=True,
        description="Personal data read",
    ),
    RiskTier.L2: TierBehavior(
        tier=RiskTier.L2,
        action=PolicyAction.ALLOW,
        audit=True,
        description="Reversible write / device control",
    ),
    RiskTier.L3: TierBehavior(
        tier=RiskTier.L3,
        action=PolicyAction.CONFIRM,
        audit=True,
        confirm=True,
        description="Sensitive or hard-to-reverse",
    ),
    RiskTier.L4: TierBehavior(
        tier=RiskTier.L4,
        action=PolicyAction.DENY,
        audit=True,
        confirm=True,
        cooldown_seconds=30,
        description="Critical / financial / irreversible",
    ),
}


def resolve_tier_behavior(
    tier: RiskTier,
    policy: PolicyConfig,
) -> TierBehavior:
    """Resolve the behavior for a given risk tier.

    Merges config-defined behavior with defaults.

    Args:
        tier: The risk tier to resolve.
        policy: The policy configuration.

    Returns:
        Resolved TierBehavior.
    """
    default = DEFAULT_TIER_BEHAVIORS.get(tier, DEFAULT_TIER_BEHAVIORS[RiskTier.L3])

    tier_key = tier.value
    if tier_key in policy.risk_tiers:
        config = policy.risk_tiers[tier_key]
        return TierBehavior(
            tier=tier,
            action=config.action,
            audit=config.audit,
            confirm=config.confirm,
            cooldown_seconds=config.cooldown_seconds,
            description=config.description or default.description,
        )

    return default
