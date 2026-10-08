"""Agent loop — the central request processing engine.

Implements: receive input → assemble context → call LLM → if tool calls:
route through policy → execute via MCP → feed results back → repeat until
final answer.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Coroutine

import structlog

from jarvis.config import AppConfig
from jarvis.core.errors import (
    AgentCancelledError,
    AgentMaxIterationsError,
    AgentTimeoutError,
)
from jarvis.llm.base import (
    LLMResponse,
    Message,
    MessageRole,
    StopReason,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    ToolResult,
    generate_trace_id,
)

logger = structlog.get_logger()


@dataclass
class AgentRequest:
    """A request to the agent."""

    message: str
    session_id: str = ""
    trace_id: str = field(default_factory=generate_trace_id)
    origin: str = "user"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResponse:
    """A response from the agent."""

    content: str = ""
    trace_id: str = ""
    tool_calls_made: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    cancelled: bool = False
    usage: TokenUsage = field(default_factory=TokenUsage)


# Callback types
OnTextChunk = Callable[[str], Coroutine[Any, Any, None]]
OnToolCall = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]
OnToolResult = Callable[[str, str, bool], Coroutine[Any, Any, None]]


class Agent:
    """The JARVIS agent loop.

    Processes requests through an iterative loop of LLM calls and tool
    execution, with streaming output and cancellation support.
    """

    def __init__(
        self,
        llm_provider: Any,  # LLMProvider protocol
        config: AppConfig,
        system_prompt: str = "",
        tools: list[ToolDefinition] | None = None,
        tool_executor: Callable[..., Coroutine[Any, Any, ToolResult]] | None = None,
    ) -> None:
        self._llm = llm_provider
        self._config = config
        self._system_prompt = system_prompt
        self._tools = tools or []
        self._tool_executor = tool_executor
        self._cancelled = False
        self._agent_config = config.jarvis.agent

    async def run(
        self,
        request: AgentRequest,
        messages: list[Message] | None = None,
        *,
        on_text_chunk: OnTextChunk | None = None,
        on_tool_call: OnToolCall | None = None,
        on_tool_result: OnToolResult | None = None,
    ) -> AgentResponse:
        """Process a request through the agent loop.

        Args:
            request: The agent request to process.
            messages: Pre-assembled message history (if None, builds from scratch).
            on_text_chunk: Callback for streaming text chunks.
            on_tool_call: Callback when a tool call is about to execute.
            on_tool_result: Callback when a tool call completes.

        Returns:
            The agent's response.
        """
        self._cancelled = False
        start_time = time.monotonic()

        log = logger.bind(trace_id=request.trace_id, origin=request.origin)
        log.info("agent_request_start", message_preview=request.message[:100])

        # Build initial messages
        if messages is None:
            messages = []
            if self._system_prompt:
                messages.append(
                    Message(role=MessageRole.SYSTEM, content=self._system_prompt)
                )
            messages.append(
                Message(role=MessageRole.USER, content=request.message)
            )

        total_usage = TokenUsage()
        tool_calls_made: list[dict[str, Any]] = []
        final_content = ""
        iterations = 0

        for iteration in range(self._agent_config.max_iterations):
            if self._cancelled:
                log.info("agent_cancelled", iteration=iteration)
                return AgentResponse(
                    content=final_content or "Request cancelled.",
                    trace_id=request.trace_id,
                    tool_calls_made=tool_calls_made,
                    iterations=iteration,
                    cancelled=True,
                    usage=total_usage,
                )

            # Check wall clock timeout
            elapsed = time.monotonic() - start_time
            if elapsed > self._agent_config.wall_clock_timeout_seconds:
                log.warning("agent_timeout", elapsed=elapsed)
                raise AgentTimeoutError(
                    f"Agent exceeded wall-clock timeout of "
                    f"{self._agent_config.wall_clock_timeout_seconds}s "
                    f"(elapsed: {elapsed:.1f}s)"
                )

            iterations = iteration + 1
            log.debug("agent_iteration", iteration=iterations)

            # Call LLM
            if self._agent_config.stream and on_text_chunk:
                response = await self._stream_llm_call(
                    messages, on_text_chunk
                )
            else:
                response = await self._llm.complete(
                    messages, tools=self._tools if self._tools else None
                )

            # Accumulate usage
            total_usage = TokenUsage(
                input_tokens=total_usage.input_tokens + response.usage.input_tokens,
                output_tokens=total_usage.output_tokens + response.usage.output_tokens,
            )

            # Append assistant message
            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            )
            messages.append(assistant_msg)

            # If no tool calls, we're done
            if response.stop_reason != StopReason.TOOL_USE or not response.tool_calls:
                final_content = response.content
                break

            # Execute tool calls
            for tool_call in response.tool_calls:
                if self._cancelled:
                    break

                log.info(
                    "tool_call",
                    tool=tool_call.name,
                    arguments=tool_call.arguments,
                )

                if on_tool_call:
                    await on_tool_call(tool_call.name, tool_call.arguments)

                # Execute the tool
                result = await self._execute_tool(tool_call)

                tool_calls_made.append({
                    "tool": tool_call.name,
                    "arguments": tool_call.arguments,
                    "result_preview": result.content[:200],
                    "is_error": result.is_error,
                })

                if on_tool_result:
                    await on_tool_result(
                        tool_call.name, result.content, result.is_error
                    )

                # Add tool result to messages
                messages.append(
                    Message(
                        role=MessageRole.TOOL_RESULT,
                        tool_result=result,
                    )
                )

        else:
            # Exhausted max iterations
            log.warning(
                "agent_max_iterations",
                max_iterations=self._agent_config.max_iterations,
            )
            if not final_content:
                final_content = (
                    "I've reached the maximum number of processing steps. "
                    "Here's what I have so far — please let me know if you'd "
                    "like me to continue."
                )

        log.info(
            "agent_request_complete",
            iterations=iterations,
            tool_calls=len(tool_calls_made),
            usage=total_usage.total_tokens,
            elapsed=f"{time.monotonic() - start_time:.2f}s",
        )

        return AgentResponse(
            content=final_content,
            trace_id=request.trace_id,
            tool_calls_made=tool_calls_made,
            iterations=iterations,
            usage=total_usage,
        )

    async def _stream_llm_call(
        self,
        messages: list[Message],
        on_text_chunk: OnTextChunk,
    ) -> LLMResponse:
        """Make a streaming LLM call, forwarding text chunks to callback."""
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        stop_reason = StopReason.END_TURN
        usage = TokenUsage()

        async for chunk in self._llm.stream(
            messages, tools=self._tools if self._tools else None
        ):
            if chunk.text:
                content_parts.append(chunk.text)
                await on_text_chunk(chunk.text)

            if chunk.tool_call:
                tool_calls.append(chunk.tool_call)

            if chunk.is_final:
                stop_reason = chunk.stop_reason or StopReason.END_TURN
                usage = chunk.usage or TokenUsage()

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            model=self._llm.model_name(),
        )

    async def _execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call, returning a result."""
        if self._tool_executor:
            try:
                return await self._tool_executor(tool_call)
            except Exception as e:
                logger.error(
                    "tool_execution_error",
                    tool=tool_call.name,
                    error=str(e),
                )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=f"Tool execution error: {e}",
                    is_error=True,
                )
        else:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Tool '{tool_call.name}' is not available — no tool executor configured.",
                is_error=True,
            )

    async def cancel(self) -> None:
        """Cancel the currently running request."""
        self._cancelled = True
        logger.info("agent_cancel_requested")
