"""OpenAI-compatible LLM provider.

Works with OpenAI, Azure OpenAI, and any OpenAI-compatible endpoint
(LM Studio, vLLM, etc.).
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
    """Convert JARVIS tools to OpenAI format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert JARVIS messages to OpenAI format."""
    openai_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == MessageRole.SYSTEM:
            openai_messages.append({
                "role": "system",
                "content": msg.content,
            })
        elif msg.role == MessageRole.USER:
            openai_messages.append({
                "role": "user",
                "content": msg.content,
            })
        elif msg.role == MessageRole.ASSISTANT:
            m: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or None,
            }
            if msg.tool_calls:
                m["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            openai_messages.append(m)
        elif msg.role == MessageRole.TOOL_RESULT:
            if msg.tool_result:
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_result.tool_call_id,
                    "content": msg.tool_result.content,
                })

    return openai_messages


def _map_stop_reason(finish_reason: str | None) -> StopReason:
    """Map OpenAI finish reason to JARVIS StopReason."""
    mapping = {
        "stop": StopReason.END_TURN,
        "tool_calls": StopReason.TOOL_USE,
        "length": StopReason.MAX_TOKENS,
    }
    return mapping.get(finish_reason or "", StopReason.END_TURN)


class OpenAICompatProvider:
    """OpenAI-compatible LLM provider.

    Works with OpenAI, Azure OpenAI, LM Studio, vLLM, and similar.

    Usage:
        provider = OpenAICompatProvider(api_key="...", model="gpt-4o")
        provider = OpenAICompatProvider(
            api_key="...", model="local-model",
            base_url="http://localhost:1234/v1"
        )
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        base_url: str | None = None,
        max_retries: int = 3,
        timeout: float = 120.0,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError(
                "openai package not installed. Install with: pip install 'jarvis[openai]'"
            )

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "max_retries": max_retries,
            "timeout": timeout,
        }
        if base_url:
            kwargs["base_url"] = base_url

        self._client = AsyncOpenAI(**kwargs)
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
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": _convert_messages(messages),
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
        }

        if tools:
            kwargs["tools"] = _convert_tools(tools)

        if stop_sequences:
            kwargs["stop"] = stop_sequences

        response = await self._client.chat.completions.create(**kwargs)

        choice = response.choices[0]

        # Parse tool calls
        tool_calls: list[ToolCall] = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        usage = TokenUsage()
        if response.usage:
            usage = TokenUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )

        return LLMResponse(
            content=choice.message.content or "",
            tool_calls=tool_calls,
            stop_reason=_map_stop_reason(choice.finish_reason),
            usage=usage,
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
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": _convert_messages(messages),
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if tools:
            kwargs["tools"] = _convert_tools(tools)

        if stop_sequences:
            kwargs["stop"] = stop_sequences

        # Track tool calls being assembled
        tool_call_buffers: dict[int, dict[str, str]] = {}
        finish_reason = None
        usage = TokenUsage()

        stream = await self._client.chat.completions.create(**kwargs)

        async for chunk in stream:
            if not chunk.choices and chunk.usage:
                usage = TokenUsage(
                    input_tokens=chunk.usage.prompt_tokens,
                    output_tokens=chunk.usage.completion_tokens,
                )
                continue

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            # Text content
            if delta.content:
                yield StreamChunk(text=delta.content)

            # Tool calls (accumulated across chunks)
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_buffers:
                        tool_call_buffers[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }

                    buf = tool_call_buffers[idx]
                    if tc_delta.id:
                        buf["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            buf["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            buf["arguments"] += tc_delta.function.arguments

        # Emit assembled tool calls
        for idx in sorted(tool_call_buffers.keys()):
            buf = tool_call_buffers[idx]
            try:
                args = json.loads(buf["arguments"]) if buf["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            yield StreamChunk(
                tool_call=ToolCall(
                    id=buf["id"],
                    name=buf["name"],
                    arguments=args,
                )
            )

        # Final chunk
        yield StreamChunk(
            is_final=True,
            stop_reason=_map_stop_reason(finish_reason),
            usage=usage,
        )

    async def count_tokens(self, messages: list[Message]) -> int:
        """Estimate token count using tiktoken."""
        try:
            import tiktoken

            encoding = tiktoken.encoding_for_model(self._model)
            total = 0
            for msg in messages:
                total += len(encoding.encode(msg.content))
                total += 4  # per-message overhead
            return total
        except Exception:
            total_chars = sum(len(m.content) for m in messages)
            return total_chars // 4

    def model_name(self) -> str:
        """Return the model name."""
        return self._model
