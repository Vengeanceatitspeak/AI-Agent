"""Market Data MCP Server for JARVIS.

Read-only access to mock market data.
"""

import random
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("MarketData")

@mcp.tool()
async def get_stock_price(symbol: str) -> str:
    """Get current stock price for a given symbol."""
    symbol = symbol.upper()
    # Mock data
    price = 100.0 + (random.random() * 50)
    change = (random.random() * 10) - 5
    return f"Stock: {symbol}\nPrice: ${price:.2f}\nChange: {'+' if change > 0 else ''}{change:.2f}%"

@mcp.tool()
async def get_market_summary() -> str:
    """Get a summary of major market indices."""
    return (
        "S&P 500: 5,100.50 (+0.4%)\n"
        "NASDAQ: 16,000.20 (+0.8%)\n"
        "DOW: 39,000.10 (-0.1%)"
    )
