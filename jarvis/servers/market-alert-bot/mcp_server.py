import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Import your existing scraper logic!
from database.event_store import EventStore

# Create the MCP server
app = Server("market-news-server")

@app.list_tools()
async def list_tools() -> list[Tool]:
    """Tell Jimmy what tools are available."""
    return [
        Tool(
            name="get_latest_market_news",
            description="Fetches the latest critical market news and forex events.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of news items to fetch"}
                }
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute the tool when Jimmy requests it."""
    if name == "get_latest_market_news":
        limit = arguments.get("limit", 5)
        
        # Call your existing database or scraper!
        store = EventStore()
        recent_events = store.get_recent_events(limit=limit) 
        
        # Format the result for Jimmy
        result_text = "Latest News:\n"
        for e in recent_events:
            result_text += f"- {e.currency}: {e.final_impact} impact\n"
            
        return [TextContent(type="text", text=result_text)]
    
    raise ValueError(f"Unknown tool: {name}")

async def main():
    # Start the server communicating via standard input/output
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())

