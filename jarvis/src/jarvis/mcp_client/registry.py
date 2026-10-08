"""MCP Tool Registry — tracks discovered tools from all connected servers.

Manages tool namespacing (server.tool), schema caching, and provides
lookup for the agent loop and router.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from typing import Any

from jarvis.llm.base import ToolDefinition

logger = structlog.get_logger()


@dataclass
class RegisteredTool:
    """A tool registered from an MCP server.

    Attributes:
        server_name: The server that provides this tool.
        original_name: Tool name as reported by the server.
        namespaced_name: Full name as exposed to the model (server.tool).
        description: Tool description.
        input_schema: JSON Schema for the tool's parameters.
        toolsets: Tags for toolset-based routing.
    """

    server_name: str
    original_name: str
    namespaced_name: str
    description: str
    input_schema: dict[str, Any]
    toolsets: list[str] = field(default_factory=list)

    def to_definition(self) -> ToolDefinition:
        """Convert to a ToolDefinition for the LLM."""
        return ToolDefinition(
            name=self.namespaced_name,
            description=self.description,
            input_schema=self.input_schema,
        )


class ToolRegistry:
    """Registry of all tools discovered from MCP servers.

    Provides namespaced access and filtering by server or toolset.
    """

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}  # namespaced_name → tool
        self._by_server: dict[str, list[str]] = {}  # server_name → [namespaced_names]

    def register_tools(
        self,
        server_name: str,
        tools: list[dict[str, Any]],
        toolsets: list[str] | None = None,
        expose_filter: list[str] | None = None,
    ) -> int:
        """Register tools from a server.

        Args:
            server_name: Server name for namespacing.
            tools: Raw tool definitions from MCP list_tools.
            toolsets: Tags for toolset routing.
            expose_filter: Optional allow-list of tool names to expose.

        Returns:
            Number of tools registered.
        """
        count = 0
        server_tools: list[str] = []

        for tool_data in tools:
            original_name = tool_data.get("name", "")
            if not original_name:
                continue

            # Apply expose filter
            if expose_filter is not None and original_name not in expose_filter:
                logger.debug(
                    "tool_filtered_by_expose",
                    server=server_name,
                    tool=original_name,
                )
                continue

            namespaced = f"{server_name}.{original_name}"

            registered = RegisteredTool(
                server_name=server_name,
                original_name=original_name,
                namespaced_name=namespaced,
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
                toolsets=list(toolsets or []),
            )

            self._tools[namespaced] = registered
            server_tools.append(namespaced)
            count += 1

        self._by_server[server_name] = server_tools
        logger.info(
            "tools_registered",
            server=server_name,
            count=count,
            tools=[t.split(".")[-1] for t in server_tools],
        )
        return count

    def unregister_server(self, server_name: str) -> int:
        """Remove all tools from a server.

        Returns:
            Number of tools removed.
        """
        tool_names = self._by_server.pop(server_name, [])
        for name in tool_names:
            self._tools.pop(name, None)
        logger.info("tools_unregistered", server=server_name, count=len(tool_names))
        return len(tool_names)

    def get_tool(self, namespaced_name: str) -> RegisteredTool | None:
        """Look up a tool by namespaced name."""
        return self._tools.get(namespaced_name)

    def resolve_tool(self, namespaced_name: str) -> tuple[str, str] | None:
        """Resolve a namespaced tool name to (server_name, original_name).

        Returns:
            Tuple of (server_name, original_name), or None if not found.
        """
        tool = self._tools.get(namespaced_name)
        if tool:
            return (tool.server_name, tool.original_name)
        return None

    def get_all_definitions(self) -> list[ToolDefinition]:
        """Get all registered tool definitions."""
        return [tool.to_definition() for tool in self._tools.values()]

    def get_server_definitions(self, server_name: str) -> list[ToolDefinition]:
        """Get tool definitions for a specific server."""
        tool_names = self._by_server.get(server_name, [])
        return [
            self._tools[name].to_definition()
            for name in tool_names
            if name in self._tools
        ]

    def get_by_toolset(self, toolset: str) -> list[ToolDefinition]:
        """Get tools matching a toolset tag."""
        return [
            tool.to_definition()
            for tool in self._tools.values()
            if toolset in tool.toolsets
        ]

    @property
    def tool_count(self) -> int:
        """Total number of registered tools."""
        return len(self._tools)

    @property
    def server_names(self) -> list[str]:
        """List of servers that have registered tools."""
        return list(self._by_server.keys())

    def summary(self) -> dict[str, int]:
        """Get a summary of tools per server."""
        return {
            server: len(tools) for server, tools in self._by_server.items()
        }
