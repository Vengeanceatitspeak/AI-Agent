"""Anthropic LLM provider (Claude models).

Implements the LLMProvider protocol for Anthropic's API.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import structlog

from jarvis.llm.base import (
    LLMResponse,
    Message,
    MessageRole,
    StopReason,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)

logger = structlog.get_logger()


def _convert_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert JARVIS tool definitions to Anthropic format."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


def _convert_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Convert JARVIS messages to Anthropic format.

    Returns:
        Tuple of (system_prompt, message_list).
    """
    system_prompt = ""
    anthropic_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == MessageRole.SYSTEM:
            system_prompt = msg.content
            continue

        if msg.role == MessageRole.USER:
            anthropic_messages.append({
                "role": "user",
                "content": msg.content,
            })
        elif msg.role == MessageRole.ASSISTANT:
            content: list[dict[str, Any]] = []
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                })
            anthropic_messages.append({
                "role": "assistant",
                "content": content if content else msg.content,
            })
        elif msg.role == MessageRole.TOOL_RESULT:
            if msg.tool_result:
                anthropic_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_result.tool_call_id,
                            "content": msg.tool_result.content,
                            "is_error": msg.tool_result.is_error,
                        }
                    ],
                })

    return system_prompt, anthropic_messages


def _map_stop_reason(stop_reason: str | None) -> StopReason:
    """Map Anthropic stop reason to JARVIS StopReason."""
    mapping = {
        "end_turn": StopReason.END_TURN,
        "tool_use": StopReason.TOOL_USE,
        "max_tokens": StopReason.MAX_TOKENS,
        "stop_sequence": StopReason.STOP_SEQUENCE,
    }
    return mapping.get(stop_reason or "", StopReason.END_TURN)


class AnthropicProvider:
    """Anthropic (Claude) LLM provider.

    Usage:
        provider = AnthropicProvider(api_key="...", model="claude-sonnet-4-20250514")
        response = await provider.complete(messages)
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        max_retries: int = 3,
        timeout: float = 120.0,
    ) -> None:
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package not installed. Install with: pip install 'jarvis[anthropic]'"
            )

        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
        )
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """Send messages and get a complete response."""
        system_prompt, anthropic_messages = _convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        if tools:
            kwargs["tools"] = _convert_tools(tools)

        if stop_sequences:
            kwargs["stop_sequences"] = stop_sequences

        response = await self._client.messages.create(**kwargs)

        # Parse response
        content_text = ""
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input) if isinstance(block.input, dict) else {},
                    )
                )

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            stop_reason=_map_stop_reason(response.stop_reason),
            usage=TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            model=response.model,
            raw=response,
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_sequences: list[str] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Send messages and get a streamed response."""
        system_prompt, anthropic_messages = _convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        if tools:
            kwargs["tools"] = _convert_tools(tools)

        if stop_sequences:
            kwargs["stop_sequences"] = stop_sequences

        # Track tool call assembly during streaming
        current_tool_id = ""
        current_tool_name = ""
        current_tool_json = ""

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_start":
                    if hasattr(event.content_block, "type"):
                        if event.content_block.type == "tool_use":
                            current_tool_id = event.content_block.id
                            current_tool_name = event.content_block.name
                            current_tool_json = ""

                elif event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        yield StreamChunk(text=event.delta.text)
                    elif hasattr(event.delta, "partial_json"):
                        current_tool_json += event.delta.partial_json

                elif event.type == "content_block_stop":
                    if current_tool_id:
                        try:
                            args = json.loads(current_tool_json) if current_tool_json else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield StreamChunk(
                            tool_call=ToolCall(
                                id=current_tool_id,
                                name=current_tool_name,
                                arguments=args,
                            )
                        )
                        current_tool_id = ""
                        current_tool_name = ""
                        current_tool_json = ""

                elif event.type == "message_stop":
                    pass

            # Final chunk with usage
            final_message = await stream.get_final_message()
            yield StreamChunk(
                is_final=True,
                stop_reason=_map_stop_reason(final_message.stop_reason),
                usage=TokenUsage(
                    input_tokens=final_message.usage.input_tokens,
                    output_tokens=final_message.usage.output_tokens,
                ),
            )

    async def count_tokens(self, messages: list[Message]) -> int:
        """Estimate token count using Anthropic's token counting."""
        system_prompt, anthropic_messages = _convert_messages(messages)
        try:
            result = await self._client.messages.count_tokens(
                model=self._model,
                messages=anthropic_messages,
                system=system_prompt if system_prompt else "",
            )
            return result.input_tokens
        except Exception:
            # Fallback: rough estimate
            total_chars = sum(len(m.content) for m in messages)
            return total_chars // 4

    def model_name(self) -> str:
        """Return the model name."""
        return self._model
