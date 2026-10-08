"""MCP Client Manager — lifecycle management for all server connections.

Handles: server startup, tool discovery, connection management,
hot-reload, health checks, graceful shutdown, and tool execution.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import structlog

from jarvis.config import (
    AppConfig,
    RestartPolicy,
    ServerConfig,
    ServersConfig,
    TransportType,
)
from jarvis.core.errors import (
    MCPCircuitOpenError,
    MCPServerCrashError,
    MCPServerStartError,
    MCPServerTimeoutError,
    MCPToolExecutionError,
    MCPToolNotFoundError,
)
from jarvis.llm.base import ToolCall, ToolDefinition, ToolResult
from jarvis.mcp_client.health import ServerHealth, ServerStatus
from jarvis.mcp_client.registry import ToolRegistry

logger = structlog.get_logger()


class MCPServerConnection:
    """A connection to a single MCP server.

    Manages the lifecycle of a server subprocess/HTTP connection,
    including tool discovery and call execution.
    """

    def __init__(
        self,
        config: ServerConfig,
        project_root: Path,
    ) -> None:
        self.config = config
        self.name = config.name
        self.health = ServerHealth(server_name=config.name)
        self._project_root = project_root
        self._session: Any = None
        self._client_context: Any = None
        self._session_context: Any = None
        self._tools_raw: list[dict[str, Any]] = []

    async def start(self) -> list[dict[str, Any]]:
        """Start the server and discover its tools.

        Returns:
            List of raw tool definitions from the server.
        """
        log = logger.bind(server=self.name)
        log.info("server_starting", transport=self.config.transport.value)

        try:
            if self.config.transport == TransportType.STDIO:
                tools = await self._start_stdio()
            else:
                tools = await self._start_http()

            self.health.mark_started()
            self._tools_raw = tools
            log.info("server_started", tool_count=len(tools))
            return tools

        except Exception as e:
            self.health.mark_failed(str(e))
            log.error("server_start_failed", error=str(e))
            raise MCPServerStartError(
                f"Failed to start server '{self.name}': {e}"
            ) from e

    async def _start_stdio(self) -> list[dict[str, Any]]:
        """Start a stdio-transport server."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        # Build environment — only explicitly listed vars
        env = dict(os.environ) if not self.config.env else {}
        # Always include PATH so the command can be found
        if "PATH" not in env:
            env["PATH"] = os.environ.get("PATH", "")
        # Add inherited env vars
        for var in self.config.inherit_env:
            if var in os.environ:
                env[var] = os.environ[var]
        # Add server-specific env vars
        for key, value in self.config.env.items():
            env[key] = value

        # Resolve CWD
        cwd = None
        if self.config.cwd:
            cwd = str(self._project_root / self.config.cwd)

        server_params = StdioServerParameters(
            command=self.config.command or "python",
            args=self.config.args,
            env=env,
            cwd=cwd,
        )

        # Start the client — keep context managers alive
        self._client_context = stdio_client(server_params)
        read, write = await self._client_context.__aenter__()

        self._session_context = ClientSession(read, write)
        self._session = await self._session_context.__aenter__()

        # Initialize
        await asyncio.wait_for(
            self._session.initialize(),
            timeout=self.config.startup_timeout_seconds,
        )

        # Discover tools
        tools_result = await self._session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema if hasattr(t, "inputSchema") else {},
            }
            for t in tools_result.tools
        ]

    async def _start_http(self) -> list[dict[str, Any]]:
        """Start an HTTP-transport server connection."""
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        url = self.config.url or ""
        headers: dict[str, str] = {}
        if self.config.auth:
            token_env = self.config.auth.token_env
            token = os.environ.get(token_env, "")
            if token:
                headers["Authorization"] = f"Bearer {token}"

        self._client_context = sse_client(url, headers=headers)
        read, write = await self._client_context.__aenter__()

        self._session_context = ClientSession(read, write)
        self._session = await self._session_context.__aenter__()

        await asyncio.wait_for(
            self._session.initialize(),
            timeout=self.config.startup_timeout_seconds,
        )

        tools_result = await self._session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema if hasattr(t, "inputSchema") else {},
            }
            for t in tools_result.tools
        ]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on this server.

        Args:
            tool_name: The original (non-namespaced) tool name.
            arguments: Tool arguments.

        Returns:
            Tool result as a string.
        """
        if not self._session:
            raise MCPServerCrashError(f"Server '{self.name}' is not connected")

        if not self.health.is_available:
            raise MCPCircuitOpenError(
                f"Server '{self.name}' circuit breaker is open — "
                f"server is degraded after {self.health.circuit_breaker.failure_count} failures"
            )

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments),
                timeout=self.config.timeout_seconds,
            )

            # Extract text content from result
            content_parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    content_parts.append(block.text)
                elif hasattr(block, "data"):
                    content_parts.append(str(block.data))
                else:
                    content_parts.append(str(block))

            result_text = "\n".join(content_parts)

            if result.isError:
                self.health.record_call_failure(result_text)
                return f"Error: {result_text}"

            self.health.record_call_success()
            return result_text

        except asyncio.TimeoutError:
            self.health.record_call_failure("timeout")
            raise MCPServerTimeoutError(
                f"Tool '{tool_name}' on server '{self.name}' timed out "
                f"after {self.config.timeout_seconds}s"
            )
        except Exception as e:
            self.health.record_call_failure(str(e))
            raise MCPToolExecutionError(
                f"Tool '{tool_name}' on server '{self.name}' failed: {e}"
            ) from e

    async def stop(self) -> None:
        """Stop the server connection."""
        log = logger.bind(server=self.name)
        try:
            if self._session_context:
                await self._session_context.__aexit__(None, None, None)
            if self._client_context:
                await self._client_context.__aexit__(None, None, None)
        except Exception as e:
            log.warning("server_stop_error", error=str(e))
        finally:
            self._session = None
            self._session_context = None
            self._client_context = None
            self.health.mark_stopped()
            log.info("server_stopped")

    @property
    def is_connected(self) -> bool:
        """Whether the server has an active session."""
        return self._session is not None


class MCPClientManager:
    """Manages all MCP server connections.

    Handles lifecycle, tool registry, hot-reload, and tool execution routing.
    """

    def __init__(self, config: AppConfig, project_root: Path | None = None) -> None:
        self._config = config
        self._project_root = project_root or Path.cwd()
        self._connections: dict[str, MCPServerConnection] = {}
        self._registry = ToolRegistry()
        self._started = False

    @property
    def registry(self) -> ToolRegistry:
        """Access the tool registry."""
        return self._registry

    async def start_all(self) -> dict[str, bool]:
        """Start all enabled servers in parallel.

        Returns:
            Dict of server_name → success status.
        """
        enabled_servers = self._config.get_enabled_servers()
        if not enabled_servers:
            logger.info("no_servers_enabled")
            return {}

        results: dict[str, bool] = {}
        tasks = []

        for server_config in enabled_servers:
            tasks.append(self._start_server(server_config))

        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        for server_config, outcome in zip(enabled_servers, outcomes):
            if isinstance(outcome, Exception):
                logger.error(
                    "server_start_failed",
                    server=server_config.name,
                    error=str(outcome),
                )
                results[server_config.name] = False
            else:
                results[server_config.name] = True

        self._started = True
        logger.info(
            "servers_started",
            total=len(results),
            succeeded=sum(results.values()),
            failed=sum(1 for v in results.values() if not v),
        )
        return results

    async def _start_server(self, config: ServerConfig) -> None:
        """Start a single server and register its tools."""
        conn = MCPServerConnection(config, self._project_root)
        tools = await conn.start()

        self._connections[config.name] = conn
        self._registry.register_tools(
            server_name=config.name,
            tools=tools,
            toolsets=config.toolsets,
            expose_filter=config.expose,
        )

    async def stop_all(self) -> None:
        """Stop all connected servers."""
        tasks = []
        for name, conn in self._connections.items():
            tasks.append(conn.stop())
            self._registry.unregister_server(name)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._connections.clear()
        self._started = False
        logger.info("all_servers_stopped")

    async def stop_server(self, name: str) -> None:
        """Stop a specific server."""
        conn = self._connections.pop(name, None)
        if conn:
            await conn.stop()
            self._registry.unregister_server(name)

    async def reload_config(self, new_servers_config: ServersConfig) -> dict[str, str]:
        """Hot-reload server configuration.

        Diffs the new config against current state and applies changes:
        - New servers → start
        - Removed/disabled servers → stop
        - Changed servers → restart

        Returns:
            Dict of server_name → action taken (started, stopped, restarted, unchanged).
        """
        actions: dict[str, str] = {}
        new_by_name = {s.name: s for s in new_servers_config.servers}
        current_names = set(self._connections.keys())

        # Determine which servers to add, remove, or update
        new_enabled = {
            s.name for s in new_servers_config.servers if s.enabled
        }
        to_start = new_enabled - current_names
        to_stop = current_names - new_enabled

        # Check for config changes in existing servers
        to_restart: set[str] = set()
        for name in current_names & new_enabled:
            new_cfg = new_by_name.get(name)
            if new_cfg and self._server_config_changed(name, new_cfg):
                to_restart.add(name)

        # Stop removed/disabled servers
        for name in to_stop:
            await self.stop_server(name)
            actions[name] = "stopped"

        # Restart changed servers
        for name in to_restart:
            await self.stop_server(name)
            cfg = new_by_name[name]
            try:
                await self._start_server(cfg)
                actions[name] = "restarted"
            except Exception as e:
                logger.error("server_restart_failed", server=name, error=str(e))
                actions[name] = f"restart_failed: {e}"

        # Start new servers
        for name in to_start:
            cfg = new_by_name.get(name)
            if cfg:
                try:
                    await self._start_server(cfg)
                    actions[name] = "started"
                except Exception as e:
                    logger.error("server_start_failed", server=name, error=str(e))
                    actions[name] = f"start_failed: {e}"

        # Mark unchanged
        for name in (current_names & new_enabled) - to_restart:
            actions[name] = "unchanged"

        logger.info("config_reloaded", actions=actions)
        return actions

    def _server_config_changed(self, name: str, new_config: ServerConfig) -> bool:
        """Check if a server's config has changed enough to require restart."""
        conn = self._connections.get(name)
        if not conn:
            return True
        old = conn.config
        # Compare key fields
        return (
            old.command != new_config.command
            or old.args != new_config.args
            or old.cwd != new_config.cwd
            or old.env != new_config.env
            or old.transport != new_config.transport
            or old.url != new_config.url
        )

    async def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call by routing to the appropriate server.

        This is the main entry point called by the agent loop.

        Args:
            tool_call: The tool call to execute.

        Returns:
            Tool result.
        """
        # Resolve namespaced name to server + original name
        resolution = self._registry.resolve_tool(tool_call.name)
        if not resolution:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Tool '{tool_call.name}' not found in any connected server.",
                is_error=True,
            )

        server_name, original_name = resolution
        conn = self._connections.get(server_name)
        if not conn or not conn.is_connected:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Server '{server_name}' is not available. "
                f"The tool '{original_name}' cannot be executed right now.",
                is_error=True,
            )

        try:
            result_text = await conn.call_tool(original_name, tool_call.arguments)
            return ToolResult(
                tool_call_id=tool_call.id,
                content=result_text,
                is_error=False,
            )
        except (MCPCircuitOpenError, MCPServerTimeoutError, MCPServerCrashError) as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(e),
                is_error=True,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Tool execution error: {e}",
                is_error=True,
            )

    def get_all_tools(self) -> list[ToolDefinition]:
        """Get all registered tool definitions."""
        return self._registry.get_all_definitions()

    def get_server_status(self) -> dict[str, dict[str, Any]]:
        """Get status for all connections."""
        status: dict[str, dict[str, Any]] = {}
        for name, conn in self._connections.items():
            h = conn.health
            status[name] = {
                "status": h.status.value,
                "connected": conn.is_connected,
                "total_calls": h.total_calls,
                "failed_calls": h.failed_calls,
                "circuit_breaker": h.circuit_breaker.state,
                "error": h.error_message,
                "tools": len(self._registry.get_server_definitions(name)),
            }
        return status
