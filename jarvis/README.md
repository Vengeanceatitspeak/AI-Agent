# JARVIS — Modular AI Assistant

A persistent, modular personal AI assistant built on the Model Context Protocol (MCP).

## Architecture

- **Core** (the brain): Agent loop, LLM provider abstraction, MCP client manager, policy/safety engine, memory, event bus, scheduler, voice pipeline, and local API.
- **MCP Servers** (the capabilities): Independent processes exposing tools via MCP. Add/remove capabilities through `config/servers.yaml` — zero Core code changes.

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Setup

```bash
# Clone and enter the project
cd jarvis

# Install with dev dependencies
uv pip install -e ".[dev,llm-all]"

# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys

# Run diagnostics
jarvis doctor

# Start chatting
jarvis chat
```

### Configuration

All configuration lives in `config/`:

| File | Purpose |
|---|---|
| `jarvis.yaml` | Core settings (models, memory, API, voice) |
| `servers.yaml` | MCP server registry (the pluggable part) |
| `policy.yaml` | Risk tiers, safety rules, rate limits |
| `persona.yaml` | Personality and system prompt |

### Adding a Capability

1. Write an MCP server (see `docs/WRITING_A_SERVER.md`)
2. Add an entry to `config/servers.yaml`
3. Run `jarvis servers reload` (or restart)
4. JARVIS can now use the new tools

### CLI Commands

```bash
jarvis chat                    # Interactive chat
jarvis servers list            # List configured servers
jarvis servers status          # Show running server status
jarvis servers reload          # Hot-reload server config
jarvis audit tail              # Recent audit entries
jarvis memory search <query>   # Search memory
jarvis doctor                  # Environment diagnostics
jarvis panic                   # Kill switch
```

## Development

```bash
# Run all checks (lint + types + tests)
make check

# Individual commands
make lint        # ruff linter
make format      # ruff formatter
make typecheck   # mypy strict
make test        # pytest
make doctor      # diagnostics
```

## Project Structure

```
jarvis/
├── config/          # YAML configuration files
├── docs/            # Architecture, security, and guide docs
├── src/jarvis/      # Core source code
│   ├── core/        # Agent loop, session, router, events
│   ├── llm/         # LLM provider abstraction
│   ├── mcp_client/  # MCP client manager
│   ├── policy/      # Safety and policy engine
│   ├── memory/      # Memory system
│   ├── voice/       # Voice pipeline (optional)
│   └── api/         # FastAPI + WebSocket
├── servers/         # Bundled MCP servers
├── tests/           # Unit, integration, and eval tests
└── scripts/         # Scaffolding and diagnostic tools
```

## Documentation

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — System design and boundaries
- [SECURITY.md](docs/SECURITY.md) — Security model and requirements
- [WRITING_A_SERVER.md](docs/WRITING_A_SERVER.md) — How to create a new MCP server
- [DECISIONS.md](docs/DECISIONS.md) — Architecture decision records

## License

MIT
