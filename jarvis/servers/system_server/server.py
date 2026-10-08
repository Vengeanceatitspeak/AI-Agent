"""System MCP Server for JARVIS.

Read-only access to system information.
"""

import os
import platform
import psutil
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("System")

@mcp.tool()
async def get_system_info() -> str:
    """Get basic system information (OS, CPU, Memory)."""
    uname = platform.uname()
    mem = psutil.virtual_memory()
    return (
        f"System: {uname.system} {uname.release} ({uname.machine})\n"
        f"Node: {uname.node}\n"
        f"CPU Cores: {psutil.cpu_count(logical=True)}\n"
        f"Memory: {mem.total / (1024**3):.1f} GB total, {mem.available / (1024**3):.1f} GB available"
    )

@mcp.tool()
async def list_processes(limit: int = 10) -> str:
    """List top processes by CPU usage."""
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
        try:
            procs.append(p.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    
    # Sort by CPU usage
    procs.sort(key=lambda x: x.get('cpu_percent', 0) or 0, reverse=True)
    
    result = []
    for p in procs[:limit]:
        result.append(f"PID {p['pid']}: {p['name']} (CPU: {p.get('cpu_percent', 0)}%, Mem: {p.get('memory_percent', 0):.1f}%)")
    
    return "\n".join(result) if result else "No processes found."
