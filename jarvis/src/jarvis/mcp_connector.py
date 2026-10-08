import asyncio
import os
import json
import logging
from typing import Any, AsyncGenerator
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

class JarvisMCPClient:
    """Client for connecting to external MCP servers."""

    def __init__(self, command: str, args: list[str] = None):
        self.command = command
        self.args = args or []
        self._session = None
        self._exit_stack = None

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[ClientSession, None]:
        """Connect to the MCP server via stdio."""
        from contextlib import AsyncExitStack
        
        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=os.environ.copy()
        )

        async with AsyncExitStack() as stack:
            logger.info(f"Connecting to MCP server: {self.command} {' '.join(self.args)}")
            stdio_transport = await stack.enter_async_context(stdio_client(server_params))
            read, write = stdio_transport
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            
            yield session

    @classmethod
    async def get_tools_from_session(cls, session: ClientSession) -> list[dict]:
        """Convert MCP tools to Groq/OpenAI tool schemas."""
        mcp_tools = await session.list_tools()
        
        groq_tools = []
        for t in mcp_tools.tools:
            groq_tools.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.inputSchema,
                }
            })
        return groq_tools
