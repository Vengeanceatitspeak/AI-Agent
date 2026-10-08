"""Google Gemini LLM provider.

Implements the LLMProvider protocol for Google's Gemini API.
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


def _convert_tools_to_gemini(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert JARVIS tools to Gemini function declarations."""
    function_declarations = []
    for t in tools:
        schema = dict(t.input_schema)
        # Gemini wants properties without the outer 'type: object' wrapper sometimes
        func_decl: dict[str, Any] = {
            "name": t.name.replace(".", "_"),  # Gemini doesn't allow dots in names
            "description": t.description,
        }
        if schema.get("properties"):
            func_decl["parameters"] = schema
        function_declarations.append(func_decl)
    return function_declarations


def _build_tool_name_map(tools: list[ToolDefinition]) -> dict[str, str]:
    """Build mapping from Gemini-safe names back to original names."""
    return {t.name.replace(".", "_"): t.name for t in tools}


class GeminiProvider:
    """Google Gemini LLM provider.

    Usage:
        provider = GeminiProvider(api_key="...", model="gemini-2.0-flash")
        response = await provider.complete(messages)
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        timeout: float = 120.0,
    ) -> None:
        try:
            from google import genai
        except ImportError:
            raise ImportError(
                "google-genai package not installed. Install with: pip install 'jarvis[gemini]'"
            )

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def _build_contents(
        self, messages: list[Message]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert messages to Gemini format."""
        system_instruction = None
        contents: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_instruction = msg.content
            elif msg.role == MessageRole.USER:
                contents.append({
                    "role": "user",
                    "parts": [{"text": msg.content}],
                })
            elif msg.role == MessageRole.ASSISTANT:
                parts: list[dict[str, Any]] = []
                if msg.content:
                    parts.append({"text": msg.content})
                for tc in msg.tool_calls:
                    parts.append({
                        "function_call": {
                            "name": tc.name.replace(".", "_"),
                            "args": tc.arguments,
                        }
                    })
                contents.append({"role": "model", "parts": parts})
            elif msg.role == MessageRole.TOOL_RESULT:
                if msg.tool_result:
                    contents.append({
                        "role": "user",
                        "parts": [{
                            "function_response": {
                                "name": "tool_result",
                                "response": {
                                    "content": msg.tool_result.content,
                                    "is_error": msg.tool_result.is_error,
                                },
                            }
                        }],
                    })

        return system_instruction, contents

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
        from google.genai import types

        system_instruction, contents = self._build_contents(messages)
        tool_name_map = _build_tool_name_map(tools) if tools else {}

        config = types.GenerateContentConfig(
            temperature=temperature if temperature is not None else self._temperature,
            max_output_tokens=max_tokens or self._max_tokens,
        )

        if system_instruction:
            config.system_instruction = system_instruction

        if tools:
            gemini_tools = _convert_tools_to_gemini(tools)
            config.tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(**fd) for fd in gemini_tools
            ])]

        if stop_sequences:
            config.stop_sequences = stop_sequences

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )

        # Parse response
        content_text = ""
        tool_calls: list[ToolCall] = []

        if response.candidates:
            candidate = response.candidates[0]
            for part in candidate.content.parts:
                if part.text:
                    content_text += part.text
                elif part.function_call:
                    original_name = tool_name_map.get(
                        part.function_call.name, part.function_call.name
                    )
                    tool_calls.append(
                        ToolCall(
                            id=f"gemini_{part.function_call.name}",
                            name=original_name,
                            arguments=dict(part.function_call.args) if part.function_call.args else {},
                        )
                    )

        # Determine stop reason
        stop_reason = StopReason.END_TURN
        if tool_calls:
            stop_reason = StopReason.TOOL_USE

        usage = TokenUsage()
        if response.usage_metadata:
            usage = TokenUsage(
                input_tokens=response.usage_metadata.prompt_token_count or 0,
                output_tokens=response.usage_metadata.candidates_token_count or 0,
            )

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            model=self._model,
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
        from google.genai import types

        system_instruction, contents = self._build_contents(messages)
        tool_name_map = _build_tool_name_map(tools) if tools else {}

        config = types.GenerateContentConfig(
            temperature=temperature if temperature is not None else self._temperature,
            max_output_tokens=max_tokens or self._max_tokens,
        )

        if system_instruction:
            config.system_instruction = system_instruction

        if tools:
            gemini_tools = _convert_tools_to_gemini(tools)
            config.tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(**fd) for fd in gemini_tools
            ])]

        if stop_sequences:
            config.stop_sequences = stop_sequences

        total_input_tokens = 0
        total_output_tokens = 0

        async for chunk in await self._client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=config,
        ):
            if chunk.candidates:
                candidate = chunk.candidates[0]
                for part in candidate.content.parts:
                    if part.text:
                        yield StreamChunk(text=part.text)
                    elif part.function_call:
                        original_name = tool_name_map.get(
                            part.function_call.name, part.function_call.name
                        )
                        yield StreamChunk(
                            tool_call=ToolCall(
                                id=f"gemini_{part.function_call.name}",
                                name=original_name,
                                arguments=dict(part.function_call.args) if part.function_call.args else {},
                            )
                        )

            if chunk.usage_metadata:
                total_input_tokens = chunk.usage_metadata.prompt_token_count or 0
                total_output_tokens = chunk.usage_metadata.candidates_token_count or 0

        yield StreamChunk(
            is_final=True,
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            ),
        )

    async def count_tokens(self, messages: list[Message]) -> int:
        """Estimate token count."""
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // 4

    def model_name(self) -> str:
        """Return the model name."""
        return self._model
