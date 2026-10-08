"""Ollama LLM provider (local models).

Uses Ollama's REST API directly via httpx for maximum compatibility.
No SDK dependency — just HTTP calls.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx
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


def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert JARVIS messages to Ollama chat format."""
    ollama_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == MessageRole.SYSTEM:
            ollama_messages.append({"role": "system", "content": msg.content})
        elif msg.role == MessageRole.USER:
            ollama_messages.append({"role": "user", "content": msg.content})
        elif msg.role == MessageRole.ASSISTANT:
            m: dict[str, Any] = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                m["tool_calls"] = [
                    {
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                    }
                    for tc in msg.tool_calls
                ]
            ollama_messages.append(m)
        elif msg.role == MessageRole.TOOL_RESULT:
            if msg.tool_result:
                ollama_messages.append({
                    "role": "tool",
                    "content": msg.tool_result.content,
                })

    return ollama_messages


def _convert_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert tools to Ollama format."""
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


class OllamaProvider:
    """Local Ollama LLM provider.

    Uses Ollama's REST API directly — no SDK needed.

    Usage:
        provider = OllamaProvider(model="llama3.2:3b")
        response = await provider.complete(messages)
    """

    def __init__(
        self,
        model: str = "llama3.2:3b",
        base_url: str = "http://localhost:11434",
        max_tokens: int = 2048,
        temperature: float = 0.3,
        timeout: float = 300.0,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
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
        """Send messages and get a complete response."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _convert_messages(messages),
            "stream": False,
            "options": {
                "num_predict": max_tokens or self._max_tokens,
                "temperature": temperature if temperature is not None else self._temperature,
            },
        }

        if tools:
            payload["tools"] = _convert_tools(tools)

        if stop_sequences:
            payload["options"]["stop"] = stop_sequences

        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()

        # Parse response
        msg_data = data.get("message", {})
        content = msg_data.get("content", "")
        tool_calls: list[ToolCall] = []

        if msg_data.get("tool_calls"):
            for i, tc in enumerate(msg_data["tool_calls"]):
                func = tc.get("function", {})
                tool_calls.append(
                    ToolCall(
                        id=f"ollama_tc_{i}",
                        name=func.get("name", ""),
                        arguments=func.get("arguments", {}),
                    )
                )

        stop_reason = StopReason.TOOL_USE if tool_calls else StopReason.END_TURN

        # Usage
        usage = TokenUsage(
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            model=self._model,
            raw=data,
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
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _convert_messages(messages),
            "stream": True,
            "options": {
                "num_predict": max_tokens or self._max_tokens,
                "temperature": temperature if temperature is not None else self._temperature,
            },
        }

        if tools:
            payload["tools"] = _convert_tools(tools)

        if stop_sequences:
            payload["options"]["stop"] = stop_sequences

        total_input = 0
        total_output = 0

        async with self._client.stream("POST", "/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_data = data.get("message", {})
                content = msg_data.get("content", "")

                if content:
                    yield StreamChunk(text=content)

                # Tool calls in streaming
                if msg_data.get("tool_calls"):
                    for i, tc in enumerate(msg_data["tool_calls"]):
                        func = tc.get("function", {})
                        yield StreamChunk(
                            tool_call=ToolCall(
                                id=f"ollama_tc_{i}",
                                name=func.get("name", ""),
                                arguments=func.get("arguments", {}),
                            )
                        )

                if data.get("done"):
                    total_input = data.get("prompt_eval_count", 0)
                    total_output = data.get("eval_count", 0)

        yield StreamChunk(
            is_final=True,
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(
                input_tokens=total_input,
                output_tokens=total_output,
            ),
        )

    async def count_tokens(self, messages: list[Message]) -> int:
        """Rough token estimate (Ollama doesn't expose tokenization)."""
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // 4

    def model_name(self) -> str:
        """Return the model name."""
        return self._model

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
