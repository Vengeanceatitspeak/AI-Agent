"""JARVIS Doctor — Environment and configuration diagnostics.

Checks:
    1. Python version (>= 3.11)
    2. Core dependencies installed
    3. Config files exist and validate
    4. .env file and required API keys
    5. Data directory is writable
    6. Each enabled server is launchable (can find its command)
    7. Optional: voice dependencies
    8. Optional: audio devices
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


console = Console()


def _check(label: str, ok: bool, detail: str = "") -> bool:
    """Print a check result and return whether it passed."""
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    msg = f"  {icon} {label}"
    if detail:
        msg += f"  [dim]({detail})[/dim]"
    console.print(msg)
    return ok


def _check_python() -> bool:
    """Check Python version."""
    v = sys.version_info
    ok = v >= (3, 11)
    return _check(
        "Python version",
        ok,
        f"{v.major}.{v.minor}.{v.micro}" + ("" if ok else " — need >= 3.11"),
    )


def _check_dependency(name: str, import_name: str | None = None) -> bool:
    """Check if a Python package is importable."""
    mod = import_name or name
    try:
        importlib.import_module(mod)
        return _check(f"Package: {name}", True)
    except ImportError:
        return _check(f"Package: {name}", False, "not installed")


def _check_config(config_dir: Path) -> bool:
    """Validate all config files."""
    all_ok = True
    files = ["jarvis.yaml", "servers.yaml", "policy.yaml", "persona.yaml"]
    for fname in files:
        path = config_dir / fname
        if not path.exists():
            _check(f"Config: {fname}", False, f"not found at {path}")
            all_ok = False
        else:
            _check(f"Config: {fname}", True, "exists")

    if all_ok:
        # Try full validation
        try:
            from jarvis.config import AppConfig

            AppConfig.load(config_dir=config_dir)
            _check("Config validation", True, "all files pass pydantic validation")
        except Exception as e:
            _check("Config validation", False, str(e))
            all_ok = False

    return all_ok


def _check_env_file(project_root: Path) -> bool:
    """Check .env file and API keys."""
    env_file = project_root / ".env"
    if not env_file.exists():
        return _check(
            ".env file",
            False,
            f"not found — copy .env.example to .env and fill in values",
        )

    _check(".env file", True, "exists")

    # Check for at least one LLM API key
    has_key = False
    key_names = ["ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"]
    for key in key_names:
        val = os.environ.get(key, "")
        if val and val.strip():
            has_key = True
            _check(f"  {key}", True, "set")
        else:
            _check(f"  {key}", False, "not set")

    # Also check Ollama (doesn't need a key but check if reachable)
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    _check("  OLLAMA_BASE_URL", True, ollama_url)

    if not has_key:
        _check(
            "LLM API key",
            False,
            "at least one API key required (or configure Ollama)",
        )

    return True


def _check_data_dir(config_dir: Path) -> bool:
    """Check data directory is writable."""
    try:
        from jarvis.config import AppConfig

        config = AppConfig.load(config_dir=config_dir)
        data_dir = config.get_data_dir()
        # Try creating a test file
        test_file = data_dir / ".doctor_test"
        test_file.write_text("ok")
        test_file.unlink()
        return _check("Data directory", True, str(data_dir))
    except Exception as e:
        return _check("Data directory", False, str(e))


def _check_servers(config_dir: Path) -> bool:
    """Check if enabled servers can be found."""
    try:
        from jarvis.config import AppConfig

        config = AppConfig.load(config_dir=config_dir)
        all_ok = True
        for server in config.get_enabled_servers():
            if server.transport.value == "stdio":
                cmd = server.command
                if cmd and shutil.which(cmd):
                    _check(f"Server: {server.name}", True, f"command '{cmd}' found")
                else:
                    _check(
                        f"Server: {server.name}",
                        False,
                        f"command '{cmd}' not found in PATH",
                    )
                    all_ok = False
            else:
                _check(
                    f"Server: {server.name}",
                    True,
                    f"HTTP transport → {server.url}",
                )
        return all_ok
    except Exception as e:
        return _check("Servers", False, str(e))


def _check_voice() -> bool:
    """Check optional voice dependencies."""
    voice_deps = {
        "faster_whisper": "faster-whisper",
        "silero_vad": "silero-vad",
        "openwakeword": "openwakeword",
        "piper": "piper-tts",
        "sounddevice": "sounddevice",
    }
    console.print("\n  [dim]Voice dependencies (optional):[/dim]")
    all_ok = True
    for import_name, pip_name in voice_deps.items():
        try:
            importlib.import_module(import_name)
            _check(f"  Voice: {pip_name}", True)
        except ImportError:
            _check(f"  Voice: {pip_name}", False, "not installed (optional)")
            all_ok = False
    return all_ok


def run_doctor(config_dir: Path) -> None:
    """Run all diagnostic checks."""
    console.print(Panel.fit("[bold cyan]JARVIS Doctor[/bold cyan]", border_style="cyan"))
    console.print()

    project_root = config_dir.parent
    results: list[bool] = []

    console.print("[bold]System:[/bold]")
    results.append(_check_python())

    console.print("\n[bold]Core Dependencies:[/bold]")
    core_deps = [
        ("pydantic", None),
        ("yaml", "yaml"),
        ("structlog", None),
        ("click", None),
        ("rich", None),
        ("fastapi", None),
        ("uvicorn", None),
        ("aiosqlite", None),
        ("httpx", None),
        ("mcp", None),
        ("dotenv", "dotenv"),
        ("watchfiles", None),
    ]
    for name, imp in core_deps:
        results.append(_check_dependency(name, imp))

    console.print("\n[bold]Configuration:[/bold]")
    results.append(_check_config(config_dir))

    console.print("\n[bold]Environment:[/bold]")
    results.append(_check_env_file(project_root))

    console.print("\n[bold]Data Directory:[/bold]")
    results.append(_check_data_dir(config_dir))

    console.print("\n[bold]Servers:[/bold]")
    results.append(_check_servers(config_dir))

    _check_voice()

    console.print()
    passed = sum(results)
    total = len(results)
    if all(results):
        console.print(
            Panel.fit(
                f"[bold green]All {total} checks passed![/bold green]",
                border_style="green",
            )
        )
    else:
        failed = total - passed
        console.print(
            Panel.fit(
                f"[bold yellow]{passed}/{total} checks passed, {failed} failed[/bold yellow]",
                border_style="yellow",
            )
        )
