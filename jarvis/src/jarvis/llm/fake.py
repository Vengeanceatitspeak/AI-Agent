"""Fake/mock LLM provider for testing.

Replays scripted responses deterministically. Supports both complete and
streaming modes. Used extensively in unit and integration tests.
"""

from __future__ import annotations

from typing import AsyncIterator

from jarvis.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    StopReason,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class FakeLLMProvider:
    """Deterministic fake LLM provider for testing.

    Usage:
        provider = FakeLLMProvider(responses=[
            LLMResponse(content="Hello!"),
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="1", name="fs.read_file", arguments={"path": "test.txt"})],
                stop_reason=StopReason.TOOL_USE,
            ),
            LLMResponse(content="The file contains: ..."),
        ])

    Each call to complete() or stream() returns the next response in order.
    """

    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
        model: str = "fake-model-v1",
    ) -> None:
        self._responses = list(responses or [])
        self._call_index = 0
        self._model = model
        self.call_history: list[dict[str, object]] = []

    def add_response(self, response: LLMResponse) -> None:
        """Add a response to the queue."""
        self._responses.append(response)

    def _next_response(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
    ) -> LLMResponse:
        """Get the next scripted response."""
        self.call_history.append({
            "messages": messages,
            "tools": tools,
            "call_index": self._call_index,
        })

        if self._call_index >= len(self._responses):
            # Default fallback
            return LLMResponse(
                content="I don't have a scripted response for this.",
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=10, output_tokens=10),
                model=self._model,
            )

        response = self._responses[self._call_index]
        self._call_index += 1
        return response

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """Return the next scripted response."""
        return self._next_response(messages, tools)

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_sequences: list[str] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream the next scripted response word by word."""
        response = self._next_response(messages, tools)

        if response.content:
            # Stream text word by word
            words = response.content.split(" ")
            for i, word in enumerate(words):
                is_last_word = i == len(words) - 1
                text = word if is_last_word else word + " "
                yield StreamChunk(text=text)

        # Yield tool calls if any
        for tool_call in response.tool_calls:
            yield StreamChunk(tool_call=tool_call)

        # Final chunk
        yield StreamChunk(
            is_final=True,
            stop_reason=response.stop_reason,
            usage=response.usage,
        )

    async def count_tokens(self, messages: list[Message]) -> int:
        """Approximate token count (4 chars per token heuristic)."""
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // 4

    def model_name(self) -> str:
        """Return the fake model name."""
        return self._model

    def reset(self) -> None:
        """Reset call index and history for reuse."""
        self._call_index = 0
        self.call_history.clear()
