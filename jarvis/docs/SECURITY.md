# Security Model

## 1. Policy Engine is Mandatory

There is **no code path** from the model to a server that skips the policy engine. All tool calls are evaluated before execution.

## 2. All Servers and Tool Output are Untrusted

- MCP servers are treated as untrusted parties
- Server-provided safety annotations (readOnly hints) are logged but **never trusted for tier decisions**
- All tool output from untrusted sources (web, email, external files) is wrapped in `UNTRUSTED CONTENT` delimiters
- Taint tracking escalates L2+ actions after untrusted content enters context

## 3. Least Privilege

- Per-server environment scoping: each server only receives explicitly listed env vars
- Filesystem sandboxing: servers operate within configured root directories
- Network egress rules: web server blocks private/internal IP ranges
- No arbitrary shell execution in v1

## 4. Local-Only API

- FastAPI bound to `127.0.0.1` by default
- Binding to `0.0.0.0` is rejected by config validation
- Token authentication required for all endpoints/WebSockets
- No CORS wildcard

## 5. Secrets Management

- Secrets loaded from `.env` file only (gitignored)
- Never logged, even at DEBUG level
- Redacted in audit entries (argument values that look like secrets)
- `.env.example` provided without values

## 6. Dependency Pinning

- All dependencies pinned with version ranges in `pyproject.toml`
- Lock file committed for reproducible builds

## 7. Audit Trail

- Append-only SQLite audit log
- Records: timestamp, trace_id, user input hash, server, tool, arguments (redacted), tier, decision, result status, duration
- CLI access: `jarvis audit tail|search`

## 8. Kill Switch

- `jarvis panic` immediately cancels all in-flight requests
- Puts policy engine in deny-all-L2+ mode until reset
- Available via CLI and API endpoint

## 9. No Model-Generated Code Execution

- The model cannot execute arbitrary code or shell commands
- Any shell tool (if added later) requires: L3+ tier, allow-list patterns, mandatory confirmation

## Reviewing Third-Party MCP Servers

Before enabling a third-party MCP server:

1. Review its code for what it accesses (filesystem, network, system)
2. Check what environment variables it requests
3. Assign appropriate risk tiers to its tools in `servers.yaml`
4. Start with `enabled: false` and test in isolation
5. Use `expose` allow-list to limit which tools are available
6. Monitor audit log after enabling
