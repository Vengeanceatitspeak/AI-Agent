"""JARVIS CLI — main entry point.

Subcommands:
    jarvis chat          — Start an interactive chat session
    jarvis servers       — Manage MCP servers
    jarvis audit         — View audit log
    jarvis memory        — Manage memory
    jarvis doctor        — Diagnose environment and config
    jarvis panic         — Kill switch: deny all L2+ operations
    jarvis trace         — View request traces
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from jarvis import __version__

console = Console()
err_console = Console(stderr=True)


def _resolve_config_dir(config_dir: str | None) -> Path:
    """Resolve config directory path."""
    if config_dir:
        return Path(config_dir)
    # Walk up from CWD looking for config/jarvis.yaml
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / "config" / "jarvis.yaml"
        if candidate.exists():
            return parent / "config"
    return cwd / "config"


@click.group()
@click.version_option(version=__version__, prog_name="JARVIS")
@click.option(
    "--config-dir",
    envvar="JARVIS_CONFIG_DIR",
    default=None,
    help="Path to config directory (default: auto-detect)",
)
@click.pass_context
def main(ctx: click.Context, config_dir: str | None) -> None:
    """JARVIS — Modular AI Assistant."""
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = _resolve_config_dir(config_dir)


# ---------------------------------------------------------------------------
# jarvis doctor
# ---------------------------------------------------------------------------


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Diagnose environment, dependencies, and configuration."""
    from jarvis.scripts.doctor import run_doctor

    config_dir = ctx.obj["config_dir"]
    run_doctor(config_dir)


# ---------------------------------------------------------------------------
# jarvis chat
# ---------------------------------------------------------------------------


@main.command()
@click.option("--model", default=None, help="Override reasoning model")
@click.option("--persona", default=None, help="Override active persona")
@click.pass_context
def chat(ctx: click.Context, model: str | None, persona: str | None) -> None:
    """Start an interactive chat session."""
    import asyncio

    config_dir = ctx.obj["config_dir"]
    try:
        from jarvis.config import AppConfig

        config = AppConfig.load(config_dir=config_dir)
    except Exception as e:
        err_console.print(f"[red]Config error: {e}[/red]")
        sys.exit(1)

    # Override persona if specified
    if persona:
        if persona in config.persona.personas:
            config.persona.active = persona
        else:
            err_console.print(
                f"[red]Persona '{persona}' not found. "
                f"Available: {list(config.persona.personas.keys())}[/red]"
            )
            sys.exit(1)

    # Setup logging
    from jarvis.core.logging import setup_logging

    setup_logging(
        level=config.jarvis.general.log_level,
        format=config.jarvis.general.log_format.value,
    )

    asyncio.run(_run_chat(config, model_override=model))


async def _run_chat(config: "AppConfig", model_override: str | None = None) -> None:
    """Run the interactive chat loop."""
    from jarvis.config import ModelConfig
    from jarvis.core.agent import Agent, AgentRequest
    from jarvis.core.session import SessionManager
    from jarvis.llm.factory import LLMFactory

    # Override model if specified
    if model_override:
        config.jarvis.models.reasoning_model = ModelConfig(
            provider=config.jarvis.models.reasoning_model.provider,
            model=model_override,
            max_tokens=config.jarvis.models.reasoning_model.max_tokens,
            temperature=config.jarvis.models.reasoning_model.temperature,
        )

    # Create components
    try:
        factory = LLMFactory(config)
        llm = factory.get_reasoning_model()
    except Exception as e:
        err_console.print(f"[red]Failed to initialize LLM: {e}[/red]")
        err_console.print(
            "[dim]Make sure your API key is set in .env and the provider is configured.[/dim]"
        )
        return

    session_mgr = SessionManager(config)
    session = session_mgr.create_session()

    system_prompt = session_mgr.build_system_prompt()

    agent = Agent(
        llm_provider=llm,
        config=config,
        system_prompt=system_prompt,
    )

    # Print welcome
    persona_name = config.persona.get_active().name
    console.print()
    console.print(
        f"[bold cyan]━━━ {persona_name} Online ━━━[/bold cyan]"
    )
    console.print(
        f"[dim]Model: {config.jarvis.models.reasoning_model.model} | "
        f"Session: {session.session_id}[/dim]"
    )
    console.print("[dim]Type 'exit' or 'quit' to end. Ctrl+C to cancel a response.[/dim]")
    console.print()

    while True:
        try:
            user_input = console.input("[bold green]You:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            console.print("[dim]Goodbye, sir.[/dim]")
            break

        # Build messages with session history
        messages = session_mgr.assemble_messages(
            session, user_input, system_prompt
        )

        # Streaming output
        console.print(f"[bold cyan]{persona_name}:[/bold cyan] ", end="")

        response_text_parts: list[str] = []

        async def on_text_chunk(text: str) -> None:
            response_text_parts.append(text)
            console.print(text, end="", highlight=False)

        async def on_tool_call(name: str, args: dict) -> None:
            console.print(f"\n  [dim]⚡ Calling {name}...[/dim]", end="")

        async def on_tool_result(name: str, result: str, is_error: bool) -> None:
            status = "[red]✗[/red]" if is_error else "[green]✓[/green]"
            console.print(f" {status}", end="")

        try:
            response = await agent.run(
                AgentRequest(message=user_input),
                messages=messages,
                on_text_chunk=on_text_chunk,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
            )

            # If streaming didn't produce text (non-streaming mode)
            if not response_text_parts and response.content:
                console.print(response.content, highlight=False)

            console.print()  # newline after response

            # Record assistant response in session
            session.add_assistant_message(response.content)

            # Show usage if verbose
            if response.usage.total_tokens > 0:
                console.print(
                    f"  [dim]tokens: {response.usage.input_tokens}→"
                    f"{response.usage.output_tokens} | "
                    f"steps: {response.iterations}[/dim]"
                )

        except KeyboardInterrupt:
            await agent.cancel()
            console.print("\n[yellow]Cancelled.[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")

        console.print()


# ---------------------------------------------------------------------------
# jarvis servers (stub — Phase 2)
# ---------------------------------------------------------------------------


@main.group()
@click.pass_context
def servers(ctx: click.Context) -> None:
    """Manage MCP servers."""
    pass


@servers.command("list")
@click.pass_context
def servers_list(ctx: click.Context) -> None:
    """List all configured MCP servers."""
    from jarvis.config import AppConfig

    config_dir = ctx.obj["config_dir"]
    try:
        config = AppConfig.load(config_dir=config_dir)
    except Exception as e:
        err_console.print(f"[red]Config error: {e}[/red]")
        sys.exit(1)

    table = Table(title="MCP Servers")
    table.add_column("Name", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Transport", style="blue")
    table.add_column("Toolsets")
    table.add_column("Risk Default", style="yellow")

    for server in config.servers.servers:
        table.add_row(
            server.name,
            "✓" if server.enabled else "✗",
            server.transport.value,
            ", ".join(server.toolsets),
            server.risk_default.value,
        )

    console.print(table)


@servers.command("status")
@click.pass_context
def servers_status(ctx: click.Context) -> None:
    """Show status of running servers."""
    console.print("[yellow]Server status not yet implemented (Phase 2).[/yellow]")


@servers.command("reload")
@click.pass_context
def servers_reload(ctx: click.Context) -> None:
    """Reload server configuration."""
    console.print("[yellow]Server reload not yet implemented (Phase 2).[/yellow]")


@servers.command("enable")
@click.argument("name")
@click.pass_context
def servers_enable(ctx: click.Context, name: str) -> None:
    """Enable a server."""
    console.print(f"[yellow]Server enable not yet implemented (Phase 2): {name}[/yellow]")


@servers.command("disable")
@click.argument("name")
@click.pass_context
def servers_disable(ctx: click.Context, name: str) -> None:
    """Disable a server."""
    console.print(f"[yellow]Server disable not yet implemented (Phase 2): {name}[/yellow]")


@servers.command("tools")
@click.argument("name", required=False)
@click.pass_context
def servers_tools(ctx: click.Context, name: str | None) -> None:
    """List tools for a server (or all servers)."""
    console.print("[yellow]Server tools not yet implemented (Phase 2).[/yellow]")


# ---------------------------------------------------------------------------
# jarvis audit (stub — Phase 3)
# ---------------------------------------------------------------------------


@main.group()
@click.pass_context
def audit(ctx: click.Context) -> None:
    """View audit log."""
    pass


@audit.command("tail")
@click.option("-n", "--lines", default=20, help="Number of recent entries")
@click.pass_context
def audit_tail(ctx: click.Context, lines: int) -> None:
    """Show recent audit entries."""
    import asyncio
    from pathlib import Path
    from jarvis.policy.audit import AuditLog

    data_dir = Path.home() / ".jarvis" / "data"
    audit = AuditLog(data_dir / "audit.db")

    async def _tail() -> list:
        await audit.initialize()
        return await audit.tail(limit=lines)

    entries = asyncio.run(_tail())
    if not entries:
        console.print("[dim]No audit entries found.[/dim]")
        return

    table = Table(title=f"Audit Log (last {lines})")
    table.add_column("Time", style="dim", max_width=19)
    table.add_column("Trace", style="cyan", max_width=14)
    table.add_column("Server", style="blue")
    table.add_column("Tool", style="magenta")
    table.add_column("Tier", style="yellow")
    table.add_column("Decision")

    for entry in entries:
        ts = str(entry.get("timestamp", ""))[:19]
        decision = entry.get("decision", "")
        dec_style = {"allow": "green", "confirm": "yellow", "deny": "red"}.get(decision, "")
        table.add_row(
            ts,
            str(entry.get("trace_id", ""))[:14],
            str(entry.get("server", "")),
            str(entry.get("tool", "")),
            str(entry.get("tier", "")),
            f"[{dec_style}]{decision}[/{dec_style}]" if dec_style else decision,
        )

    console.print(table)


@audit.command("search")
@click.argument("query")
@click.pass_context
def audit_search(ctx: click.Context, query: str) -> None:
    """Search audit log."""
    import asyncio
    from pathlib import Path
    from jarvis.policy.audit import AuditLog

    data_dir = Path.home() / ".jarvis" / "data"
    audit = AuditLog(data_dir / "audit.db")

    async def _search() -> list:
        await audit.initialize()
        return await audit.search(query)

    entries = asyncio.run(_search())
    if not entries:
        console.print(f"[dim]No audit entries matching '{query}'.[/dim]")
        return

    for entry in entries:
        ts = str(entry.get("timestamp", ""))[:19]
        console.print(
            f"[dim]{ts}[/dim] "
            f"[cyan]{entry.get('trace_id', '')[:14]}[/cyan] "
            f"[blue]{entry.get('server', '')}[/blue]."
            f"[magenta]{entry.get('tool', '')}[/magenta] "
            f"tier={entry.get('tier', '')} "
            f"decision={entry.get('decision', '')} "
            f"{entry.get('notes', '')}"
        )


# ---------------------------------------------------------------------------
# jarvis memory
# ---------------------------------------------------------------------------


def _get_memory_store():
    """Create a MemoryStore instance."""
    from pathlib import Path
    from jarvis.memory.store import MemoryStore
    data_dir = Path.home() / ".jarvis" / "data"
    return MemoryStore(data_dir / "memory.db")


@main.group()
@click.pass_context
def memory(ctx: click.Context) -> None:
    """Manage memory."""
    pass


@memory.command("list")
@click.option("--tag", default=None, help="Filter by tag")
@click.option("-n", "--limit", default=50, help="Max results")
@click.pass_context
def memory_list(ctx: click.Context, tag: str | None, limit: int) -> None:
    """List stored facts."""
    import asyncio
    store = _get_memory_store()

    async def _list():
        await store.initialize()
        return await store.list_facts(limit=limit, tag=tag)

    facts = asyncio.run(_list())
    if not facts:
        console.print("[dim]No facts stored yet.[/dim]")
        return

    table = Table(title=f"Memory Facts ({len(facts)})")
    table.add_column("ID", style="dim", width=5)
    table.add_column("Key", style="cyan")
    table.add_column("Value", max_width=60)
    table.add_column("Source", style="dim")
    table.add_column("Tags", style="yellow")

    for fact in facts:
        table.add_row(
            str(fact.id),
            fact.key,
            fact.value[:60] + ("..." if len(fact.value) > 60 else ""),
            fact.source,
            ", ".join(fact.tags),
        )
    console.print(table)


@memory.command("search")
@click.argument("query")
@click.pass_context
def memory_search(ctx: click.Context, query: str) -> None:
    """Search memory by content."""
    import asyncio
    store = _get_memory_store()

    async def _search():
        await store.initialize()
        return await store.search(query)

    results = asyncio.run(_search())
    if not results:
        console.print(f"[dim]No facts matching '{query}'.[/dim]")
        return

    for r in results:
        console.print(
            f"  [cyan]{r.fact.key}[/cyan]: {r.fact.value} "
            f"[dim](score: {r.score:.2f}, source: {r.fact.source})[/dim]"
        )


@memory.command("delete")
@click.argument("fact_id", type=int)
@click.pass_context
def memory_delete(ctx: click.Context, fact_id: int) -> None:
    """Delete a fact by ID."""
    import asyncio
    store = _get_memory_store()

    async def _delete():
        await store.initialize()
        return await store.delete_fact(fact_id)

    deleted = asyncio.run(_delete())
    if deleted:
        console.print(f"[green]Deleted fact {fact_id}.[/green]")
    else:
        console.print(f"[red]Fact {fact_id} not found.[/red]")


@memory.command("export")
@click.option("-o", "--output", default="memory_export.json", help="Output file")
@click.pass_context
def memory_export(ctx: click.Context, output: str) -> None:
    """Export all memory to JSON."""
    import asyncio
    import json
    store = _get_memory_store()

    async def _export():
        await store.initialize()
        return await store.export_all()

    data = asyncio.run(_export())
    with open(output, "w") as f:
        json.dump(data, f, indent=2, default=str)
    console.print(f"[green]Exported {len(data['facts'])} facts and {len(data['summaries'])} summaries to {output}[/green]")


# ---------------------------------------------------------------------------
# jarvis panic (stub — Phase 3)
# ---------------------------------------------------------------------------


@main.command()
@click.confirmation_option(prompt="⚠️  PANIC: This will deny all L2+ operations. Continue?")
@click.pass_context
def panic(ctx: click.Context) -> None:
    """KILL SWITCH: Cancel all in-flight requests and deny all L2+ operations."""
    from pathlib import Path

    # Write a panic flag file that the running instance watches
    panic_file = Path.home() / ".jarvis" / "data" / "PANIC"
    panic_file.parent.mkdir(parents=True, exist_ok=True)
    panic_file.write_text("PANIC ACTIVATED")
    console.print("[bold red]🚨 PANIC MODE ACTIVATED[/bold red]")
    console.print("[red]All L2+ operations are now DENIED.[/red]")
    console.print("[dim]To deactivate: delete ~/.jarvis/data/PANIC[/dim]")


# ---------------------------------------------------------------------------
# jarvis serve
# ---------------------------------------------------------------------------


@main.command()
@click.option("--host", default=None, help="Override API host")
@click.option("--port", default=None, type=int, help="Override API port")
@click.pass_context
def serve(ctx: click.Context, host: str | None, port: int | None) -> None:
    """Start the JARVIS API server."""
    import uvicorn
    from jarvis.config import AppConfig
    from jarvis.api.app import create_app

    config_dir = ctx.obj["config_dir"]
    try:
        config = AppConfig.load(config_dir=config_dir)
    except Exception as e:
        err_console.print(f"[red]Config error: {e}[/red]")
        sys.exit(1)

    api_host = host or config.jarvis.api.host
    api_port = port or config.jarvis.api.port

    console.print(f"[bold cyan]JARVIS API[/bold cyan] starting on {api_host}:{api_port}")

    app = create_app(config)
    uvicorn.run(app, host=api_host, port=api_port, log_level="info")


# ---------------------------------------------------------------------------
# jarvis trace
# ---------------------------------------------------------------------------


@main.group()
@click.pass_context
def trace(ctx: click.Context) -> None:
    """View request traces."""
    pass


@trace.command("show")
@click.argument("trace_id")
@click.pass_context
def trace_show(ctx: click.Context, trace_id: str) -> None:
    """Show details for a specific trace."""
    import asyncio
    from pathlib import Path
    from jarvis.policy.audit import AuditLog

    data_dir = Path.home() / ".jarvis" / "data"
    audit = AuditLog(data_dir / "audit.db")

    async def _show():
        await audit.initialize()
        return await audit.get_by_trace(trace_id)

    entries = asyncio.run(_show())
    if not entries:
        console.print(f"[dim]No entries for trace '{trace_id}'.[/dim]")
        return

    console.print(f"[bold]Trace: [cyan]{trace_id}[/cyan][/bold]")
    console.print()
    for entry in entries:
        ts = str(entry.get("timestamp", ""))[:19]
        decision = entry.get("decision", "")
        dec_style = {"allow": "green", "confirm": "yellow", "deny": "red"}.get(decision, "")
        console.print(
            f"  [dim]{ts}[/dim] "
            f"[blue]{entry.get('server', '')}[/blue]."
            f"[magenta]{entry.get('tool', '')}[/magenta] "
            f"tier=[yellow]{entry.get('tier', '')}[/yellow] "
            f"[{dec_style}]{decision}[/{dec_style}]"
        )
        if entry.get("notes"):
            console.print(f"    [dim]{entry['notes']}[/dim]")


if __name__ == "__main__":
    main()
