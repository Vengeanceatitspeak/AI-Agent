"""DemoServer — MCP server for JARVIS.

Tools:
    example_tool: A placeholder tool to get you started.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("DemoServer")


@mcp.tool()
async def example_tool(input_text: str) -> str:
    """An example tool — replace with your implementation.

    Args:
        input_text: The input to process.

    Returns:
        Processed result.
    """
    return f"Processed: {input_text}"
