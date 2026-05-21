# aery_qgis: Pure-Python QGIS Agent — Architecture Notes

> **Status:** The plugin is fully implemented and working. This document describes
> the current architecture and records design decisions.

**Goal:** Embed the Aery AI agent inside QGIS as a native Python plugin.
The agent runs pure Python — no Node.js, no external daemon — making it the
simplest possible integration.

**Architecture:**
- **Pure-Python agent** (`agent.py`): conversation loop, tool calling, context
  injection, self-correction, streaming to the UI via Qt signals.
- **Direct LLM API calls** (`llm_client.py`): OpenAI, Anthropic, Gemini with
  retry/backoff. No external proxy required.
- **Main-thread QGIS execution** (`qgis_executor.py`): TCP socket + QTimer queue
  marshals all Python onto the QGIS main thread. Thread-safe by construction.
- **13 built-in tools** (`tools.py` + `geospatial_tools.py`): core, geospatial,
  graph-query, and self-extension tools.
- **Five knowledge graphs** (`graph_engine.py`): provenance, session, spatial,
  tool capability, algorithm. Auto-built and injected as context.

**Tech Stack:**
- **Engine**: Pure Python (no TypeScript fork required)
- **Plugin**: Python, PyQt6, QGIS 4.0+ API

---

## Completed Design Decisions

### Why pure Python
Forking to TypeScript/Node (`aery-core`) adds a build layer, a binary bridge,
and a cross-language IPC boundary for no benefit inside a Python-native host.
Direct LLM API access (`httpx`) is simpler and easier to debug.

### Thread safety
All QGIS API calls serially execute via QTimer on the main thread.
Python `asyncio.to_thread` is the only asyncio boundary and never crosses
into Qt objects.

### Permission model
Destructive patterns (`removeMapLayer`, `deleteFeatures`, `os.remove`,
`shutil.rmtree`) trigger a `threading.Event`-based suspend/resume. The UI
shows a modal dialog; when the user approves or denies, the agent's event loop
unblocks and either retries or skips — no stale message-history pollution.

### Session persistence
JSONL format in `<project_dir>/.aery/sessions/`. Head+tail reads for large
files, 1 MB cap, message truncation at 4 k chars. Compatible with the
previous OpenClaude-inspired format.

### Self-correction
Max 3 retries before surfacing the error to the user. Snapshot-based undo
stack (layer state before destructive `run_qgis_code` calls) is available
as `agent.undo_last_tool()`.

### Graph refresh
Graph context (spatial relationships, tool chains) is injected on the first
user turn and auto-detected again on every subsequent turn in
`agent.run()` — so layers added by earlier tool calls are visible to the LLM
on the next cycle.

---

## Historical Note

An earlier `implementation-plan.md` (pre-2025 refactor) described a TypeScript
`aery-core` fork with a Bun-compiled binary runner. That plan has been
superseded by the pure-Python architecture described above. The TypeScript
approach is no longer necessary for this project.
