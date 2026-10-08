"""JARVIS error types.

Centralized error hierarchy for clear, actionable error messages.
"""

from __future__ import annotations


class JarvisError(Exception):
    """Base exception for all JARVIS errors."""

    def __init__(self, message: str, *, details: str = "") -> None:
        self.details = details
        super().__init__(message)


# ---------------------------------------------------------------------------
# Config errors
# ---------------------------------------------------------------------------


class ConfigError(JarvisError):
    """Configuration is invalid or missing."""


class ConfigFileNotFoundError(ConfigError):
    """A required configuration file was not found."""


class ConfigValidationError(ConfigError):
    """Configuration values failed validation."""


# ---------------------------------------------------------------------------
# LLM errors
# ---------------------------------------------------------------------------


class LLMError(JarvisError):
    """Base error for LLM provider issues."""


class LLMProviderNotFoundError(LLMError):
    """Requested LLM provider is not available."""


class LLMConnectionError(LLMError):
    """Failed to connect to LLM provider."""


class LLMRateLimitError(LLMError):
    """LLM provider rate limit exceeded."""


class LLMTimeoutError(LLMError):
    """LLM request timed out."""


class LLMResponseError(LLMError):
    """LLM returned an invalid or unexpected response."""


# ---------------------------------------------------------------------------
# MCP errors
# ---------------------------------------------------------------------------


class MCPError(JarvisError):
    """Base error for MCP client/server issues."""


class MCPServerStartError(MCPError):
    """Failed to start an MCP server."""


class MCPServerTimeoutError(MCPError):
    """MCP server did not respond within the timeout."""


class MCPServerCrashError(MCPError):
    """MCP server process terminated unexpectedly."""


class MCPToolNotFoundError(MCPError):
    """Requested tool was not found in any server."""


class MCPToolExecutionError(MCPError):
    """Tool execution failed."""


class MCPCircuitOpenError(MCPError):
    """Circuit breaker is open for this server."""


# ---------------------------------------------------------------------------
# Policy errors
# ---------------------------------------------------------------------------


class PolicyError(JarvisError):
    """Base error for policy engine issues."""


class PolicyDeniedError(PolicyError):
    """Tool call was denied by the policy engine."""

    def __init__(self, message: str, *, tool: str = "", tier: str = "", reason: str = "") -> None:
        self.tool = tool
        self.tier = tier
        self.reason = reason
        super().__init__(message)


class PolicyConfirmationRequired(PolicyError):
    """Tool call requires user confirmation."""

    def __init__(
        self,
        message: str,
        *,
        tool: str = "",
        args_summary: str = "",
        effect: str = "",
    ) -> None:
        self.tool = tool
        self.args_summary = args_summary
        self.effect = effect
        super().__init__(message)


class PanicModeError(PolicyError):
    """System is in panic mode — all L2+ operations denied."""


# ---------------------------------------------------------------------------
# Agent errors
# ---------------------------------------------------------------------------


class AgentError(JarvisError):
    """Base error for agent loop issues."""


class AgentMaxIterationsError(AgentError):
    """Agent exceeded maximum iterations."""


class AgentTimeoutError(AgentError):
    """Agent exceeded wall-clock timeout."""


class AgentCancelledError(AgentError):
    """Agent request was cancelled by user."""


# ---------------------------------------------------------------------------
# Memory errors
# ---------------------------------------------------------------------------


class MemoryError(JarvisError):
    """Base error for memory system issues."""


class MemorySensitiveDataError(MemoryError):
    """Attempted to store sensitive data (secrets, credentials, etc.)."""


# ---------------------------------------------------------------------------
# Voice errors
# ---------------------------------------------------------------------------


class VoiceError(JarvisError):
    """Base error for voice pipeline issues."""


class VoiceNotAvailableError(VoiceError):
    """Voice dependencies are not installed."""


class VoiceDeviceError(VoiceError):
    """Audio device error."""
