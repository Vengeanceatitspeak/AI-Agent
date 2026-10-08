"""Tests for the fake LLM provider."""

from __future__ import annotations

import pytest

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
from jarvis.llm.fake import FakeLLMProvider


class TestFakeLLMProvider:
    """Test the deterministic fake LLM provider."""

    @pytest.mark.asyncio
    async def test_complete_returns_scripted_response(self) -> None:
        provider = FakeLLMProvider(responses=[
            LLMResponse(content="Hello, sir."),
        ])
        messages = [Message(role=MessageRole.USER, content="Hi")]
        result = await provider.complete(messages)
        assert result.content == "Hello, sir."

    @pytest.mark.asyncio
    async def test_complete_sequences_responses(self) -> None:
        provider = FakeLLMProvider(responses=[
            LLMResponse(content="First"),
            LLMResponse(content="Second"),
        ])
        messages = [Message(role=MessageRole.USER, content="")]
        r1 = await provider.complete(messages)
        r2 = await provider.complete(messages)
        assert r1.content == "First"
        assert r2.content == "Second"

    @pytest.mark.asyncio
    async def test_complete_fallback_when_exhausted(self) -> None:
        provider = FakeLLMProvider(responses=[
            LLMResponse(content="Only one"),
        ])
        messages = [Message(role=MessageRole.USER, content="")]
        await provider.complete(messages)
        r2 = await provider.complete(messages)
        assert "scripted response" in r2.content

    @pytest.mark.asyncio
    async def test_tool_call_response(self) -> None:
        provider = FakeLLMProvider(responses=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="tc_1", name="fs.read_file", arguments={"path": "test.txt"})
                ],
                stop_reason=StopReason.TOOL_USE,
            ),
        ])
        messages = [Message(role=MessageRole.USER, content="Read test.txt")]
        result = await provider.complete(messages)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "fs.read_file"
        assert result.stop_reason == StopReason.TOOL_USE

    @pytest.mark.asyncio
    async def test_stream_yields_chunks(self) -> None:
        provider = FakeLLMProvider(responses=[
            LLMResponse(content="Hello world"),
        ])
        messages = [Message(role=MessageRole.USER, content="Hi")]
        chunks: list[StreamChunk] = []
        async for chunk in provider.stream(messages):
            chunks.append(chunk)

        # Should have text chunks + final
        assert len(chunks) >= 2
        assert chunks[-1].is_final
        text = "".join(c.text for c in chunks)
        assert "Hello world" in text

    @pytest.mark.asyncio
    async def test_call_history_recorded(self) -> None:
        provider = FakeLLMProvider(responses=[
            LLMResponse(content="Ok"),
        ])
        messages = [Message(role=MessageRole.USER, content="Test")]
        await provider.complete(messages)
        assert len(provider.call_history) == 1
        assert provider.call_history[0]["call_index"] == 0

    @pytest.mark.asyncio
    async def test_reset_clears_state(self) -> None:
        provider = FakeLLMProvider(responses=[
            LLMResponse(content="First"),
            LLMResponse(content="Second"),
        ])
        messages = [Message(role=MessageRole.USER, content="")]
        await provider.complete(messages)
        provider.reset()
        result = await provider.complete(messages)
        assert result.content == "First"  # Back to start

    @pytest.mark.asyncio
    async def test_count_tokens(self) -> None:
        provider = FakeLLMProvider()
        messages = [Message(role=MessageRole.USER, content="Hello world test")]
        count = await provider.count_tokens(messages)
        assert count > 0

    def test_model_name(self) -> None:
        provider = FakeLLMProvider(model="test-v2")
        assert provider.model_name() == "test-v2"
