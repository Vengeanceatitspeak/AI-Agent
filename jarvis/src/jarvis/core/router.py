"""Tool router — selects relevant toolsets per request.

Implements toolset-based tool selection to avoid sending too many tools
to the LLM (which bloats context and confuses models).

Strategies (implemented in order):
1. Static: always include tools from toolsets marked 'always_on'
2. Intent-based: fast model or embedding similarity selects relevant toolsets
3. Escalation: meta-tool 'jarvis.list_tools' for mid-task discovery

Full implementation in Phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.llm.base import ToolDefinition


@dataclass
class ToolEntry:
    """A registered tool with metadata.

    Attributes:
        definition: The tool definition (name, description, schema).
        server_name: Which MCP server provides this tool.
        toolsets: Tags for toolset-based routing.
        always_on: Whether this tool is always included.
    """

    definition: ToolDefinition
    server_name: str
    toolsets: list[str] = field(default_factory=list)
    always_on: bool = False


class ToolRouter:
    """Routes and selects tools per request.

    Full implementation in Phase 4.
    """

    def __init__(self, max_tools_per_call: int = 25) -> None:
        self._tools: dict[str, ToolEntry] = {}
        self._max_tools = max_tools_per_call

    def register_tool(self, entry: ToolEntry) -> None:
        """Register a tool."""
        self._tools[entry.definition.name] = entry

    def unregister_server(self, server_name: str) -> None:
        """Remove all tools from a server."""
        to_remove = [
            name for name, entry in self._tools.items()
            if entry.server_name == server_name
        ]
        for name in to_remove:
            del self._tools[name]

    def get_all_tools(self) -> list[ToolDefinition]:
        """Get all registered tool definitions."""
        return [entry.definition for entry in self._tools.values()]

    async def select_tools(
        self,
        user_message: str,
        *,
        include_toolsets: list[str] | None = None,
    ) -> list[ToolDefinition]:
        """Select relevant tools for a request.

        Phase 0-1: returns all tools (capped).
        Phase 4: implements intelligent routing.
        """
        tools = self.get_all_tools()
        return tools[: self._max_tools]
