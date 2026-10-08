"""Entry point for `python -m market_data_server`."""
from market_data_server.server import mcp

if __name__ == "__main__":
    mcp.run()
