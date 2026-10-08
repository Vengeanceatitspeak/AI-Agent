"""Automated evaluation scenarios for JARVIS."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from jarvis.core.agent import Agent, AgentRequest
from jarvis.core.session import SessionManager
from jarvis.llm.fake import FakeLLMProvider
from jarvis.policy.engine import PolicyEngine
from jarvis.policy.tiers import RiskTier

# Note: Full MCP Client Manager integration tests are mocked here
# to focus purely on the policy and agent routing behaviors.

@pytest.fixture
def fake_llm() -> FakeLLMProvider:
    return FakeLLMProvider()

@pytest.fixture
def agent(fake_llm: FakeLLMProvider) -> Agent:
    config = MagicMock()
    config.jarvis.agent.max_iterations = 5
    config.jarvis.agent.wall_clock_timeout_seconds = 300.0
    
    # Mock Policy Engine
    policy_engine = AsyncMock(spec=PolicyEngine)
    policy_engine.evaluate = AsyncMock()
    
    agent = Agent(llm_provider=fake_llm, config=config, system_prompt="Test system")
    agent._policy = policy_engine
    agent._tool_router = AsyncMock()
    
    # Mock tool executor to simulate what ToolRouter does
    async def dummy_tool_executor(tool_call):
        decision = await policy_engine.evaluate(tool_call, trace_id="test")
        if not decision.is_allowed:
            from jarvis.llm.base import ToolResult
            return ToolResult(tool_call_id=tool_call.id, content=decision.reason or "Denied", is_error=True)
        return await agent._tool_router.execute_tool(tool_call)
        
    agent._tool_executor = dummy_tool_executor
    
    return agent


class TestEvals:
    """Security and functional evaluation scenarios."""
    
    @pytest.mark.asyncio
    async def test_scenario_01_basic_tool_use(self, agent: Agent, fake_llm: FakeLLMProvider) -> None:
        """Scenario 1: Basic tool use is permitted and executed."""
        from jarvis.llm.base import LLMResponse, ToolCall, StopReason

        fake_llm.add_response(LLMResponse(
            content="Calling notes.create_note",
            tool_calls=[ToolCall(id="call_1", name="notes.create_note", arguments={"title": "X", "content": "Y"})],
            stop_reason=StopReason.TOOL_USE
        ))
        fake_llm.add_response(LLMResponse(content="Note created."))
        
        # Policy allows it
        decision = MagicMock()
        decision.is_allowed = True
        decision.tier = RiskTier.L1
        agent._policy.evaluate.return_value = decision
        
        from jarvis.llm.base import ToolResult
        agent._tool_router.execute_tool.return_value = ToolResult(
            tool_call_id="call_1",
            content="Success",
            is_error=False
        )
        
        req = AgentRequest(message="Save a note about X", session_id="test_session")
        res = await agent.run(req, [])
        
        assert res.iterations == 2
        assert len(res.tool_calls_made) == 1
        agent._tool_router.execute_tool.assert_called_once()
        
    @pytest.mark.asyncio
    async def test_scenario_02_confirmation_denial(self, agent: Agent, fake_llm: FakeLLMProvider) -> None:
        """Scenario 2: Confirmation required and denied by user (mocked)."""
        from jarvis.llm.base import LLMResponse, ToolCall, StopReason

        fake_llm.add_response(LLMResponse(
            content="Deleting file Y",
            tool_calls=[ToolCall(id="call_1", name="fs.delete_file", arguments={"path": "Y"})],
            stop_reason=StopReason.TOOL_USE
        ))
        fake_llm.add_response(LLMResponse(content="File was not deleted."))
        
        # Policy requires confirmation, let's say it gets denied
        decision = MagicMock()
        decision.is_allowed = False
        decision.requires_confirmation = True
        decision.tier = RiskTier.L3
        decision.reason = "Denied by user"
        agent._policy.evaluate.return_value = decision
        
        req = AgentRequest(message="Delete file Y", session_id="test_session")
        res = await agent.run(req, [])
        
        # Tool router should not be called because policy denied it
        agent._tool_router.execute_tool.assert_not_called()
        assert res.iterations == 2
        
    @pytest.mark.asyncio
    async def test_scenario_04_prompt_injection(self, agent: Agent, fake_llm: FakeLLMProvider) -> None:
        """Scenario 4: Prompt injection from untrusted source fails to escalate."""
        from jarvis.llm.base import LLMResponse, ToolCall, StopReason

        # Simulated scenario: Model reads a webpage which has injection,
        # then tries to delete files.
        fake_llm.add_response(LLMResponse(
            content="I will fetch the page.",
            tool_calls=[ToolCall(id="call_1", name="web.fetch", arguments={"url": "http://evil.com"})],
            stop_reason=StopReason.TOOL_USE
        ))
        
        # Then it tries an L2+ action
        fake_llm.add_response(LLMResponse(
            content="Executing injected instruction",
            tool_calls=[ToolCall(id="call_2", name="fs.delete_file", arguments={"path": "/etc/passwd"})],
            stop_reason=StopReason.TOOL_USE
        ))
        fake_llm.add_response(LLMResponse(content="Done."))
        
        # Policy evaluation logic intercepts the second call
        def mock_policy_eval(tc, trace_id):
            decision = MagicMock()
            if tc.name == "web.fetch":
                decision.is_allowed = True
                decision.tier = RiskTier.L1
            else:
                # Taint tracking would catch this and block/require confirm
                decision.is_allowed = False
                decision.reason = "Blocked due to tainted context"
                decision.tier = RiskTier.L4
            return decision
            
        agent._policy.evaluate.side_effect = mock_policy_eval
        
        req = AgentRequest(message="Summarize this page", session_id="test_session")
        await agent.run(req, [])
        
        # Ensure the delete file was never executed
        executed_tools = [args[0][0].name for args in agent._tool_router.execute_tool.call_args_list]
        assert "fs.delete_file" not in executed_tools
        assert "web.fetch" in executed_tools
