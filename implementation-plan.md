# aery_qgis: Native Geospatial Integration Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform Aery into a specialized, built-in QGIS agent (`aery_qgis`) that is geomatics-aware and stripped of non-GIS developer noise.

**Architecture:**
- **Forked Core**: Use a specialized copy of the Aery engine (`aery-core`) with the Geospatial Suite baked in.
- **Built-in Tools**: GIS tools and `web_search` are native core tools, not extensions.
- **Auto-Context**: Native context injector automatically pulls QGIS state (layers, CRS) before every turn.
- **Simplified UI**: Clean Qt Chat Panel in QGIS that spawns the specialized binary.

**Tech Stack:**
- **Engine**: TypeScript (Aery Core fork), Bun (compiler).
- **Plugin**: Python (PyQt6), QGIS 4 API.

---

### Phase 1: Engine Specialization (Core Fork)

**Files:**
- Create: `aery-core/packages/coding-agent/src/core/tools/geospatial-suite.ts`
- Create: `aery-core/packages/coding-agent/src/core/qgis-context.ts`
- Modify: `aery-core/packages/coding-agent/src/core/tools/index.ts`
- Modify: `aery-core/packages/coding-agent/src/core/sdk.ts`
- Modify: `aery-core/packages/coding-agent/src/core/system-prompt.ts`

- [ ] **Task 1.1: Re-implement Geospatial Suite**
  Restore the 16 GIS tools and `web_search` directly into the core.
- [ ] **Task 1.2: Hard-Strip Developer Tools**
  Remove `edit`, `grep`, and `find` from default activation.
- [ ] **Task 1.3: Enable Auto-Context Injection**
  Add logic to `sdk.ts` to automatically fetch QGIS project state before every LLM turn.
- [ ] **Task 1.4: Specialized GIS Prompt**
  Hardcode the "Geospatial Rulebook" into the engine's system prompt.

### Phase 2: Perfect Build & Binary

- [ ] **Task 2.1: Clean Monorepo Build**
  `cd aery-core && bun install && npm run build`
- [ ] **Task 2.2: Specialized Binary Compilation**
  Compile the `aery-qgis-runner` and sync all assets.

### Phase 3: Plugin Synchronization

- [ ] **Task 3.1: Clean Python Bridge**
  Simplify `rpc_bridge.py` to use the native features of our new binary.
- [ ] **Task 3.2: UI Polish**
  Update chat panel header and task suggestions.
