# Writing a New MCP Server

This guide walks you through creating a new JARVIS capability as an MCP server.

## Overview

Each MCP server is an independent process that exposes tools via the Model Context Protocol. JARVIS discovers and connects to servers through `config/servers.yaml`.

**Time estimate:** ~30 minutes for a basic server with 2-3 tools.

## Step 1: Scaffold

Use the built-in scaffold script:

```bash
python scripts/new_server.py my_server
```

This creates:

```
servers/my_server/
├── pyproject.toml
├── __init__.py
├── __main__.py
├── server.py       # Your tool implementations
├── tests/
│   └── test_server.py
└── README.md
```

## Step 2: Implement Tools

Edit `servers/my_server/server.py`:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("MyServer")

@mcp.tool()
async def my_tool(arg1: str, arg2: int = 10) -> str:
    """Description of what this tool does.

    Args:
        arg1: Description of arg1.
        arg2: Description of arg2.
    """
    # Your implementation here
    return f"Result: {arg1} x {arg2}"
```

### Tool Guidelines

- **Clear descriptions:** The LLM uses your docstring to decide when/how to call the tool
- **Type hints:** Required — the SDK generates JSON schemas from them
- **Structured errors:** Return error messages, don't raise exceptions
- **Input validation:** Validate and sanitize all inputs
- **Size limits:** Cap response sizes to avoid context bloat

## Step 3: Test Standalone

```bash
cd servers/my_server
python -m my_server
# Server starts on stdio, ready for MCP clients
```

Run tests:

```bash
pytest servers/my_server/tests/
```

## Step 4: Register in servers.yaml

Add an entry to `config/servers.yaml`:

```yaml
  - name: my_server
    enabled: true
    transport: stdio
    command: python
    args: ["-m", "my_server"]
    cwd: servers/my_server
    timeout_seconds: 30
    startup_timeout_seconds: 15
    restart: on_failure
    toolsets: [my_category]
    risk_default: L2  # Choose appropriate default tier
    tool_overrides:
      my_tool: { risk: L1 }  # Override per-tool if needed
```

## Step 5: Assign Risk Tiers

Choose tiers based on what each tool can do:

| Tier | When to use | Example |
|---|---|---|
| L0 | Pure computation, public data | Math, time, public search |
| L1 | Reads personal data | Read notes, list files |
| L2 | Reversible writes | Create note, add calendar event |
| L3 | Hard to reverse, sensitive | Delete files, send email |
| L4 | Critical, financial | Execute trades, system config |

**When in doubt, use L3.** You can always lower it later.

## Step 6: Reload

```bash
jarvis servers reload
# Or restart JARVIS
```

Verify:

```bash
jarvis servers list
jarvis servers tools my_server
```

## Best Practices

1. **One concern per server.** Don't mix filesystem and web tools in one server.
2. **Sandbox inputs.** Validate file paths, URLs, and other inputs.
3. **Cap output size.** Large responses waste context tokens.
4. **Include a README.** Document every tool, its arguments, and risk tiers.
5. **Write tests.** At minimum, test each tool with valid and invalid inputs.
6. **Don't import Core.** Servers must be completely independent.
