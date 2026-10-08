#!/usr/bin/env python3
"""Scaffold a new MCP server from a template.

Usage:
    python scripts/new_server.py <server_name>

Creates a new server directory under servers/ with all boilerplate files.
Prints a suggested servers.yaml snippet to stdout (not auto-applied).
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent


def create_server(name: str, base_dir: Path | None = None) -> None:
    """Create a new MCP server skeleton.

    Args:
        name: Server name (e.g., 'my_server'). Must be a valid Python identifier.
        base_dir: Base directory for the project. Defaults to CWD.
    """
    if not name.isidentifier():
        print(f"Error: '{name}' is not a valid Python identifier.", file=sys.stderr)
        sys.exit(1)

    if base_dir is None:
        base_dir = Path.cwd()

    server_dir = base_dir / "servers" / name
    if server_dir.exists():
        print(f"Error: {server_dir} already exists.", file=sys.stderr)
        sys.exit(1)

    # Create directories
    server_dir.mkdir(parents=True)
    (server_dir / "tests").mkdir()

    # pyproject.toml
    (server_dir / "pyproject.toml").write_text(dedent(f"""\
        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [project]
        name = "{name}"
        version = "0.1.0"
        description = "JARVIS MCP Server: {name}"
        requires-python = ">=3.11"
        dependencies = [
            "mcp>=1.0,<2.0",
        ]
    """))

    # __init__.py
    (server_dir / "__init__.py").write_text(
        f'"""JARVIS MCP Server: {name}."""\n'
    )

    # __main__.py
    (server_dir / "__main__.py").write_text(dedent(f"""\
        \"\"\"Entry point for `python -m {name}`.\"\"\"

        from {name}.server import mcp

        if __name__ == "__main__":
            mcp.run()
    """))

    # server.py
    class_name = "".join(word.capitalize() for word in name.split("_"))
    (server_dir / "server.py").write_text(dedent(f"""\
        \"\"\"{class_name} — MCP server for JARVIS.

        Tools:
            example_tool: A placeholder tool to get you started.
        \"\"\"

        from __future__ import annotations

        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("{class_name}")


        @mcp.tool()
        async def example_tool(input_text: str) -> str:
            \"\"\"An example tool — replace with your implementation.

            Args:
                input_text: The input to process.

            Returns:
                Processed result.
            \"\"\"
            return f"Processed: {{input_text}}"
    """))

    # tests/__init__.py
    (server_dir / "tests" / "__init__.py").write_text("")

    # tests/test_server.py
    (server_dir / "tests" / "test_server.py").write_text(dedent(f"""\
        \"\"\"Tests for {name} server.\"\"\"

        import pytest


        class TestExampleTool:
            \"\"\"Tests for the example_tool.\"\"\"

            @pytest.mark.asyncio
            async def test_basic(self) -> None:
                from {name}.server import example_tool

                result = await example_tool("hello")
                assert "hello" in result

            @pytest.mark.asyncio
            async def test_empty_input(self) -> None:
                from {name}.server import example_tool

                result = await example_tool("")
                assert isinstance(result, str)
    """))

    # README.md
    (server_dir / "README.md").write_text(dedent(f"""\
        # {class_name} — JARVIS MCP Server

        ## Tools

        | Tool | Description | Risk Tier |
        |---|---|---|
        | `example_tool` | An example tool | L1 |

        ## Running Standalone

        ```bash
        cd servers/{name}
        python -m {name}
        ```

        ## Testing

        ```bash
        pytest servers/{name}/tests/
        ```

        ## Configuration

        Add to `config/servers.yaml`:

        ```yaml
          - name: {name.replace('_server', '')}
            enabled: true
            transport: stdio
            command: python
            args: ["-m", "{name}"]
            cwd: servers/{name}
            timeout_seconds: 30
            startup_timeout_seconds: 15
            restart: on_failure
            toolsets: []
            risk_default: L2
        ```
    """))

    print(f"✓ Created server scaffold at: {server_dir}")
    print()
    print("Suggested servers.yaml entry (add to config/servers.yaml):")
    print()
    short_name = name.replace("_server", "")
    print(dedent(f"""\
        - name: {short_name}
          enabled: true
          transport: stdio
          command: python
          args: ["-m", "{name}"]
          cwd: servers/{name}
          timeout_seconds: 30
          startup_timeout_seconds: 15
          restart: on_failure
          toolsets: []
          risk_default: L2
    """))
    print(f"Next steps:")
    print(f"  1. Edit servers/{name}/server.py — add your tools")
    print(f"  2. Edit servers/{name}/tests/test_server.py — add tests")
    print(f"  3. Add the YAML entry above to config/servers.yaml")
    print(f"  4. Run: jarvis servers reload")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/new_server.py <server_name>")
        sys.exit(1)
    create_server(sys.argv[1])
