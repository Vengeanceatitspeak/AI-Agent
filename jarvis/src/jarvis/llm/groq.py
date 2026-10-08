"""Groq LLM provider with round-robin API key rotation.

Uses the OpenAI-compatible API endpoint at api.groq.com.
Automatically rotates through multiple API keys to avoid rate limits.
"""

from __future__ import annotations

import itertools
import json
import os
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

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _load_groq_keys() -> list[str]:
    """Load Groq API keys from GROQ_API_KEYS env var (comma-separated)."""
    raw = os.environ.get("GROQ_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise ValueError(
            "No Groq API keys found. Set GROQ_API_KEYS in .env "
            "(comma-separated for round-robin rotation)."
        )
    return keys


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
    """Convert JARVIS messages to OpenAI/Groq format."""
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


class GroqProvider:
    """Groq LLM provider with automatic API key round-robin.

    Rotates through multiple API keys on each request to avoid
    hitting per-key rate limits.

    Usage:
        provider = GroqProvider(model="openai/gpt-oss-120b")
    """

    def __init__(
        self,
        model: str = "openai/gpt-oss-120b",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        max_retries: int = 3,
        timeout: float = 120.0,
        api_keys: list[str] | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError(
                "openai package not installed. Install with: pip install openai"
            )

        self._keys = api_keys or _load_groq_keys()
        self._key_cycle = itertools.cycle(self._keys)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_retries = max_retries
        self._timeout = timeout
        self._AsyncOpenAI = AsyncOpenAI

        logger.info(
            "groq_provider_init",
            model=model,
            num_keys=len(self._keys),
            rotation="round-robin",
        )

    def _get_client(self):
        """Get an OpenAI client with the next API key in rotation."""
        key = next(self._key_cycle)
        return self._AsyncOpenAI(
            api_key=key,
            base_url=GROQ_BASE_URL,
            max_retries=self._max_retries,
            timeout=self._timeout,
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """Send messages and get a complete response with key rotation."""
        client = self._get_client()

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

        response = await client.chat.completions.create(**kwargs)

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
                output_tokens=response.usage.completion_tokens or 0,
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
        """Send messages and get a streamed response with key rotation."""
        client = self._get_client()

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": _convert_messages(messages),
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
            "stream": True,
        }

        if tools:
            kwargs["tools"] = _convert_tools(tools)

        if stop_sequences:
            kwargs["stop"] = stop_sequences

        # Track tool calls being assembled
        tool_call_buffers: dict[int, dict[str, str]] = {}
        finish_reason = None
        usage = TokenUsage()

        stream = await client.chat.completions.create(**kwargs)

        async for chunk in stream:
            if not chunk.choices:
                # Usage chunk at end
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = TokenUsage(
                        input_tokens=chunk.usage.prompt_tokens or 0,
                        output_tokens=chunk.usage.completion_tokens or 0,
                    )
                continue

            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            # Text content
            if delta.content:
                yield StreamChunk(text=delta.content)

            # Tool calls (accumulated across chunks)
            if hasattr(delta, "tool_calls") and delta.tool_calls:
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
        """Estimate token count (rough approximation for Groq models)."""
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // 4  # rough estimate

    def model_name(self) -> str:
        """Return the model name."""
        return self._model
