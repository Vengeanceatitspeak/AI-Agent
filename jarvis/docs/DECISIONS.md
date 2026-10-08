# Architecture Decision Records

## ADR-001: Use `structlog` for logging

**Status:** Accepted  
**Date:** 2026-10-04

**Context:** Need structured JSON logging with trace IDs and component tagging.

**Decision:** Use `structlog` over stdlib `logging`.

**Rationale:** Better structured JSON output, native async support, processors for context enrichment, cleaner API for binding trace_id/session_id per-request.

---

## ADR-002: Use `click` for CLI

**Status:** Accepted  
**Date:** 2026-10-04

**Context:** Need a CLI framework supporting subcommand groups (servers, audit, memory, trace).

**Decision:** Use `click` over `argparse`.

**Rationale:** Nested subcommand groups, automatic help generation, extensible, well-maintained. Combined with `rich` for styled output.

---

## ADR-003: Use `sqlite-vec` for vector search

**Status:** Accepted  
**Date:** 2026-10-04

**Context:** Need vector similarity search for semantic memory recall.

**Decision:** Use `sqlite-vec` extension rather than a separate vector database.

**Rationale:** Keeps everything in a single SQLite database. No additional process to manage. Good enough for single-user local workloads. Can migrate to a dedicated vector DB later if needed.

---

## ADR-004: Use APScheduler 3.x

**Status:** Accepted  
**Date:** 2026-10-04

**Context:** Need cron-like and interval scheduling for proactive triggers.

**Decision:** Use APScheduler 3.x (not 4.x).

**Rationale:** 4.x is still alpha/early. 3.x is stable, well-documented, and has proven asyncio support via AsyncIOScheduler.

---

## ADR-005: Use Protocol over ABC for interfaces

**Status:** Accepted  
**Date:** 2026-10-04

**Context:** Need to define interfaces for LLM providers, memory stores, STT/TTS.

**Decision:** Use `typing.Protocol` (structural subtyping) instead of `abc.ABC`.

**Rationale:** More Pythonic, supports duck-typing, easier testing with mocks, no forced inheritance. Aligns with modern Python typing best practices.

---

## ADR-006: Voice dependencies in optional extras

**Status:** Accepted  
**Date:** 2026-10-04

**Context:** Voice requires heavy dependencies (PyTorch via silero-vad, faster-whisper, etc.) that many users won't need.

**Decision:** Put all voice dependencies in a `[voice]` extras group.

**Rationale:** Core must run without voice installed (spec requirement). Optional extras keeps the base install fast and light.

---

## ADR-007: Makefile for CI commands

**Status:** Accepted  
**Date:** 2026-10-04

**Context:** Need a single entry point for lint + type check + tests.

**Decision:** Use a Makefile with `make check` as the combined target.

**Rationale:** Simple, universal, no additional dependency. Each target is a single command. Easy to extend.
