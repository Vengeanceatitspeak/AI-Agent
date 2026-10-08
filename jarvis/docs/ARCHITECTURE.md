# JARVIS Architecture

## Layers

```
USER (text / voice)
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│ INTERFACES: CLI · WebSocket API · Voice pipeline            │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ CORE                                                        │
│  Session Manager → Agent Loop → LLM Provider (abstraction)  │
│        │               │                                    │
│        │        Tool Router ←→ Policy Engine ←→ Audit Log   │
│        │               │                                    │
│   Memory Manager   MCP Client Manager (registry, hot-reload)│
│        │               │                                    │
│   Event Bus ← Scheduler / Triggers                          │
└──────────────────────────────┬──────────────────────────────┘
                               ▼  (MCP over stdio / streamable HTTP)
┌─────────────────────────────────────────────────────────────┐
│ MCP SERVERS (independent processes, config-driven)          │
│  filesystem · web · notes · system · calendar · market-data │
│  · (your future servers)                                    │
└─────────────────────────────────────────────────────────────┘
```

## Hard Boundaries

1. **Core never imports from a server package.** Servers are launched as subprocesses or connected over HTTP.
2. **Servers never import from Core.** They share nothing except the MCP protocol.
3. **Safety lives in Core only.** The policy engine is the authority. Server-provided annotations are advisory hints from an untrusted party.
4. **Real-time paths are NOT MCP.** Audio capture, VAD, wake word, STT, TTS run as an in-process pipeline.

## Agent Loop

```
receive input → assemble context → call LLM → if tool calls:
    route through policy → execute via MCP → feed results back → repeat
→ final answer
```

- Max iterations per request (default 12)
- Wall-clock timeout (default 300s)
- Parallel tool calls with bounded concurrency
- Cancellation support (important for voice)
- Every request gets a trace_id

## Policy Decision Flow

For every tool call:

1. Resolve tier: explicit tool_override → server risk_default → global default (L3)
2. Apply argument-level rules (path sandboxing, URL filtering, shell patterns)
3. Apply taint rules (untrusted content escalation)
4. Apply rate limits and budgets
5. Return ALLOW, CONFIRM(reason), or DENY(reason)
6. Write audit record regardless of outcome

## MCP Client Manager

- Namespacing: tools exposed as `<server>.<tool>` to avoid collisions
- Lifecycle: parallel startup, capability negotiation, tool/resource discovery
- Hot reload: watch servers.yaml, diff config, start/stop/restart without Core restart
- Health: periodic checks, exponential backoff reconnect, circuit breaker
- Isolation: one server crash never affects another or Core

## Memory Layers

1. **Conversation buffer** — rolling window + summaries per session
2. **Facts store** — durable key facts with source, confidence, timestamps
3. **Semantic recall** — embeddings over summaries and notes for similarity search
