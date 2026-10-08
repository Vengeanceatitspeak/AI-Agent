"""Tests for demo_server server."""

import pytest


class TestExampleTool:
    """Tests for the example_tool."""

    @pytest.mark.asyncio
    async def test_basic(self) -> None:
        from demo_server.server import example_tool

        result = await example_tool("hello")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_empty_input(self) -> None:
        from demo_server.server import example_tool

        result = await example_tool("")
        assert isinstance(result, str)
