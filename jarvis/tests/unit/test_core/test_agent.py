"""Tests for the agent loop."""

from __future__ import annotations

import pytest

from jarvis.config import AppConfig
from jarvis.core.agent import Agent, AgentRequest, AgentResponse
from jarvis.llm.base import (
    LLMResponse,
    Message,
    MessageRole,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from jarvis.llm.fake import FakeLLMProvider


@pytest.fixture
def simple_config(config_dir) -> AppConfig:
    """Load config for tests."""
    return AppConfig.load(config_dir=config_dir)


class TestAgentBasic:
    """Test basic agent loop behavior."""

    @pytest.mark.asyncio
    async def test_simple_text_response(self, simple_config: AppConfig) -> None:
        """Agent returns text response with no tool calls."""
        provider = FakeLLMProvider(responses=[
            LLMResponse(
                content="Good evening, sir.",
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=50, output_tokens=10),
            ),
        ])

        agent = Agent(
            llm_provider=provider,
            config=simple_config,
            system_prompt="You are JARVIS.",
        )

        response = await agent.run(AgentRequest(message="Hello"))
        assert response.content == "Good evening, sir."
        assert response.iterations == 1
        assert response.cancelled is False
        assert response.usage.total_tokens == 60

    @pytest.mark.asyncio
    async def test_tool_call_loop(self, simple_config: AppConfig) -> None:
        """Agent handles tool call → result → final answer."""
        provider = FakeLLMProvider(responses=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="tc1", name="fs.read_file", arguments={"path": "test.txt"}),
                ],
                stop_reason=StopReason.TOOL_USE,
                usage=TokenUsage(input_tokens=100, output_tokens=20),
            ),
            LLMResponse(
                content="The file contains: Hello World",
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=150, output_tokens=15),
            ),
        ])

        async def fake_executor(tool_call: ToolCall) -> ToolResult:
            return ToolResult(
                tool_call_id=tool_call.id,
                content="Hello World",
            )

        agent = Agent(
            llm_provider=provider,
            config=simple_config,
            system_prompt="You are JARVIS.",
            tools=[
                ToolDefinition(
                    name="fs.read_file",
                    description="Read a file",
                    input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                ),
            ],
            tool_executor=fake_executor,
        )

        response = await agent.run(AgentRequest(message="Read test.txt"))
        assert response.content == "The file contains: Hello World"
        assert response.iterations == 2
        assert len(response.tool_calls_made) == 1
        assert response.tool_calls_made[0]["tool"] == "fs.read_file"

    @pytest.mark.asyncio
    async def test_tool_execution_error(self, simple_config: AppConfig) -> None:
        """Agent handles tool execution errors gracefully."""
        provider = FakeLLMProvider(responses=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="tc1", name="fs.read_file", arguments={"path": "bad.txt"}),
                ],
                stop_reason=StopReason.TOOL_USE,
                usage=TokenUsage(input_tokens=100, output_tokens=20),
            ),
            LLMResponse(
                content="I couldn't read the file due to an error.",
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=200, output_tokens=15),
            ),
        ])

        async def failing_executor(tool_call: ToolCall) -> ToolResult:
            raise FileNotFoundError("File not found: bad.txt")

        agent = Agent(
            llm_provider=provider,
            config=simple_config,
            system_prompt="You are JARVIS.",
            tool_executor=failing_executor,
        )

        response = await agent.run(AgentRequest(message="Read bad.txt"))
        assert response.content == "I couldn't read the file due to an error."
        assert response.tool_calls_made[0]["is_error"] is True

    @pytest.mark.asyncio
    async def test_no_tool_executor(self, simple_config: AppConfig) -> None:
        """Tool calls without executor return error result."""
        provider = FakeLLMProvider(responses=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="tc1", name="fs.read_file", arguments={"path": "test.txt"}),
                ],
                stop_reason=StopReason.TOOL_USE,
                usage=TokenUsage(input_tokens=100, output_tokens=20),
            ),
            LLMResponse(
                content="Tools are not available right now.",
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=150, output_tokens=10),
            ),
        ])

        agent = Agent(
            llm_provider=provider,
            config=simple_config,
            system_prompt="You are JARVIS.",
        )

        response = await agent.run(AgentRequest(message="Read test.txt"))
        assert response.tool_calls_made[0]["is_error"] is True

    @pytest.mark.asyncio
    async def test_cancellation(self, simple_config: AppConfig) -> None:
        """Agent respects cancellation during tool execution."""
        provider = FakeLLMProvider(responses=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="tc1", name="slow.task", arguments={}),
                    ToolCall(id="tc2", name="slow.task2", arguments={}),
                ],
                stop_reason=StopReason.TOOL_USE,
            ),
            LLMResponse(content="Done"),
        ])

        agent_ref: list[Agent] = []
        call_count = 0

        async def cancelling_executor(tool_call: ToolCall) -> ToolResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await agent_ref[0].cancel()
            return ToolResult(tool_call_id=tool_call.id, content="ok")

        agent = Agent(
            llm_provider=provider,
            config=simple_config,
            system_prompt="You are JARVIS.",
            tool_executor=cancelling_executor,
        )
        agent_ref.append(agent)

        response = await agent.run(
            AgentRequest(message="Do something"),
        )
        assert response.cancelled is True



    @pytest.mark.asyncio
    async def test_streaming_callbacks(self, simple_config: AppConfig) -> None:
        """Agent calls streaming callbacks correctly."""
        provider = FakeLLMProvider(responses=[
            LLMResponse(
                content="Hello sir, how can I help?",
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=50, output_tokens=8),
            ),
        ])

        agent = Agent(
            llm_provider=provider,
            config=simple_config,
            system_prompt="You are JARVIS.",
        )

        chunks_received: list[str] = []

        async def on_chunk(text: str) -> None:
            chunks_received.append(text)

        response = await agent.run(
            AgentRequest(message="Hi"),
            on_text_chunk=on_chunk,
        )

        assert len(chunks_received) > 0
        assert "Hello" in "".join(chunks_received)

    @pytest.mark.asyncio
    async def test_trace_id_propagated(self, simple_config: AppConfig) -> None:
        """Trace ID is carried through the response."""
        provider = FakeLLMProvider(responses=[
            LLMResponse(content="Ok"),
        ])

        agent = Agent(
            llm_provider=provider,
            config=simple_config,
            system_prompt="You are JARVIS.",
        )

        request = AgentRequest(message="Hi", trace_id="tr_test123")
        response = await agent.run(request)
        assert response.trace_id == "tr_test123"
