# DemoServer — JARVIS MCP Server

## Tools

| Tool | Description | Risk Tier |
|---|---|---|
| `example_tool` | An example tool | L1 |

## Running Standalone

```bash
cd servers/demo_server
python -m demo_server
```

## Testing

```bash
pytest servers/demo_server/tests/
```

## Configuration

Add to `config/servers.yaml`:

```yaml
  - name: demo
    enabled: true
    transport: stdio
    command: python
    args: ["-m", "demo_server"]
    cwd: servers/demo_server
    timeout_seconds: 30
    startup_timeout_seconds: 15
    restart: on_failure
    toolsets: []
    risk_default: L2
```
