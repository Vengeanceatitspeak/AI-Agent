"""Entry point for `python -m system_server`."""
from system_server.server import mcp

if __name__ == "__main__":
    mcp.run()
