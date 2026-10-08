"""LLM Provider abstraction — base protocol, types, and factory.

Defines the provider-agnostic interface that all LLM providers must implement.
Normalizes messages, tool calls, and responses across providers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------


class MessageRole(str, Enum):
    """Normalized message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_RESULT = "tool_result"


@dataclass
class ToolDefinition:
    """A tool definition to be sent to the LLM.

    Attributes:
        name: Namespaced tool name (e.g., 'fs.read_file').
        description: Human-readable description of what the tool does.
        input_schema: JSON Schema for the tool's input parameters.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    """A tool call requested by the LLM.

    Attributes:
        id: Unique identifier for this tool call.
        name: Namespaced tool name (e.g., 'fs.read_file').
        arguments: Parsed arguments dict.
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result of executing a tool call.

    Attributes:
        tool_call_id: ID of the ToolCall this is a result for.
        content: The result content (text, JSON, or error).
        is_error: Whether this result represents an error.
    """

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """A normalized message in the conversation.

    Attributes:
        role: The role of the message sender.
        content: Text content (may be empty for tool-call-only messages).
        tool_calls: Tool calls requested by the assistant.
        tool_result: Result of a tool call (for tool_result role).
        name: Optional name for the message sender.
    """

    role: MessageRole
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_result: ToolResult | None = None
    name: str | None = None


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


class StopReason(str, Enum):
    """Why the LLM stopped generating."""

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    ERROR = "error"


@dataclass
class TokenUsage:
    """Token usage statistics."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResponse:
    """Complete response from an LLM provider.

    Attributes:
        content: Text content of the response.
        tool_calls: Tool calls requested by the model.
        stop_reason: Why the model stopped generating.
        usage: Token usage statistics.
        model: The model that generated this response.
        raw: Provider-specific raw response (for debugging).
    """

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: StopReason = StopReason.END_TURN
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    raw: Any = None


@dataclass
class StreamChunk:
    """A chunk of streamed LLM output.

    Attributes:
        text: Text content in this chunk (may be empty).
        tool_call: Partial or complete tool call (may be None).
        is_final: Whether this is the last chunk.
        stop_reason: Set on the final chunk.
        usage: Set on the final chunk.
    """

    text: str = ""
    tool_call: ToolCall | None = None
    is_final: bool = False
    stop_reason: StopReason | None = None
    usage: TokenUsage | None = None


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol that all LLM providers must implement.

    Providers normalize provider-specific APIs into the JARVIS message format.
    """

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """Send messages and get a complete response.

        Args:
            messages: Conversation history in normalized format.
            tools: Available tool definitions.
            temperature: Override default temperature.
            max_tokens: Override default max tokens.
            stop_sequences: Optional stop sequences.

        Returns:
            Complete LLM response.
        """
        ...

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_sequences: list[str] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Send messages and get a streamed response.

        Args:
            messages: Conversation history in normalized format.
            tools: Available tool definitions.
            temperature: Override default temperature.
            max_tokens: Override default max tokens.
            stop_sequences: Optional stop sequences.

        Yields:
            StreamChunk objects with partial content.
        """
        ...

    async def count_tokens(self, messages: list[Message]) -> int:
        """Estimate the token count for a list of messages.

        This is an approximation — exact counts are provider-specific.

        Args:
            messages: Messages to count tokens for.

        Returns:
            Estimated token count.
        """
        ...

    def model_name(self) -> str:
        """Return the model identifier string."""
        ...


# ---------------------------------------------------------------------------
# Trace ID generation
# ---------------------------------------------------------------------------


def generate_trace_id() -> str:
    """Generate a unique trace ID for request tracking."""
    return f"tr_{uuid.uuid4().hex[:12]}"
