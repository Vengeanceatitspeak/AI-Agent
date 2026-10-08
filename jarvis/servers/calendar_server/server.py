"""Calendar MCP Server for JARVIS.

Mock implementation of calendar reading.
"""

from datetime import datetime, timedelta
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Calendar")

@mcp.tool()
async def get_todays_events() -> str:
    """Get events scheduled for today."""
    now = datetime.now()
    # Mock data
    events = [
        f"09:00 AM - Standup Meeting",
        f"12:00 PM - Lunch with Sarah",
        f"03:00 PM - Design Review",
    ]
    return f"Events for {now.strftime('%Y-%m-%d')}:\n" + "\n".join(events)

@mcp.tool()
async def check_availability(date: str) -> str:
    """Check if the user is free on a specific date (YYYY-MM-DD)."""
    return f"Checking availability for {date}... You have 4 hours of free time."
