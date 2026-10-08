"""Tests for the MCP tool registry."""

from __future__ import annotations

import pytest

from jarvis.mcp_client.registry import ToolRegistry


class TestToolRegistry:
    """Test tool registry operations."""

    def test_register_tools(self) -> None:
        registry = ToolRegistry()
        count = registry.register_tools(
            server_name="fs",
            tools=[
                {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
                {"name": "write_file", "description": "Write a file", "inputSchema": {"type": "object"}},
            ],
            toolsets=["files"],
        )
        assert count == 2
        assert registry.tool_count == 2

    def test_namespacing(self) -> None:
        registry = ToolRegistry()
        registry.register_tools("fs", [{"name": "read_file", "description": ""}])
        tool = registry.get_tool("fs.read_file")
        assert tool is not None
        assert tool.namespaced_name == "fs.read_file"
        assert tool.original_name == "read_file"
        assert tool.server_name == "fs"

    def test_resolve_tool(self) -> None:
        registry = ToolRegistry()
        registry.register_tools("fs", [{"name": "read_file", "description": ""}])
        result = registry.resolve_tool("fs.read_file")
        assert result == ("fs", "read_file")

    def test_resolve_unknown_tool(self) -> None:
        registry = ToolRegistry()
        assert registry.resolve_tool("unknown.tool") is None

    def test_unregister_server(self) -> None:
        registry = ToolRegistry()
        registry.register_tools("fs", [
            {"name": "read_file", "description": ""},
            {"name": "write_file", "description": ""},
        ])
        assert registry.tool_count == 2
        removed = registry.unregister_server("fs")
        assert removed == 2
        assert registry.tool_count == 0

    def test_expose_filter(self) -> None:
        registry = ToolRegistry()
        count = registry.register_tools(
            server_name="fs",
            tools=[
                {"name": "read_file", "description": ""},
                {"name": "write_file", "description": ""},
                {"name": "delete_path", "description": ""},
            ],
            expose_filter=["read_file", "write_file"],
        )
        assert count == 2
        assert registry.get_tool("fs.read_file") is not None
        assert registry.get_tool("fs.delete_path") is None

    def test_no_collision_across_servers(self) -> None:
        registry = ToolRegistry()
        registry.register_tools("fs", [{"name": "search", "description": "FS search"}])
        registry.register_tools("web", [{"name": "search", "description": "Web search"}])
        assert registry.tool_count == 2
        fs_tool = registry.get_tool("fs.search")
        web_tool = registry.get_tool("web.search")
        assert fs_tool is not None
        assert web_tool is not None
        assert fs_tool.description == "FS search"
        assert web_tool.description == "Web search"

    def test_get_by_toolset(self) -> None:
        registry = ToolRegistry()
        registry.register_tools("fs", [{"name": "read_file", "description": ""}], toolsets=["files"])
        registry.register_tools("web", [{"name": "search", "description": ""}], toolsets=["web"])
        file_tools = registry.get_by_toolset("files")
        assert len(file_tools) == 1
        assert file_tools[0].name == "fs.read_file"

    def test_summary(self) -> None:
        registry = ToolRegistry()
        registry.register_tools("fs", [{"name": "a", "description": ""}, {"name": "b", "description": ""}])
        registry.register_tools("web", [{"name": "c", "description": ""}])
        summary = registry.summary()
        assert summary == {"fs": 2, "web": 1}

    def test_get_all_definitions(self) -> None:
        registry = ToolRegistry()
        registry.register_tools("fs", [
            {"name": "read_file", "description": "Read", "inputSchema": {"type": "object"}},
        ])
        defs = registry.get_all_definitions()
        assert len(defs) == 1
        assert defs[0].name == "fs.read_file"
        assert defs[0].description == "Read"
