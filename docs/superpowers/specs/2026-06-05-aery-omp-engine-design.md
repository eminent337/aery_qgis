# Aery QGIS Plugin: OMP Engine Architecture Design

## 1. Overview
The goal of this project is to completely rewrite the core agent engine of the `aery-qgis-plugin` by porting the advanced architectural concepts of the `oh-my-pi` (`omp`) engine into pure Python. This will transform the plugin into a highly capable, "batteries-included" agent that supports multimodal vision, real-time stream interception, precise code editing, and subagent parallelization.

All code will be authored natively within the Aery project and strictly branded as Aery.

## 2. Core Loop & Time-Traveling Stream Rules (TTSR)
- **Token-Level Interception:** The engine will manage LLM generation via a streaming state machine rather than waiting for bulk responses.
- **Rule Injection:** If the model hallucinates (e.g., generating dangerous code or forbidden QGIS APIs), the engine will immediately abort the stream mid-token, slice the output, inject a system-level correction rule, and retry. This minimizes token waste and enforces safety constraints interactively.

## 3. Tool Architecture & Perfect Editing
- **Hashline Editor:** A new `edit` tool will be implemented using content-hash anchors rather than fragile regex or full-file rewrites. This guarantees perfect atomic edits across Python scripts and project files.
- **Persistent QGIS Sandbox (`eval`):** The current AST executor will be upgraded to maintain a persistent state context (variables, layers, and references) across multiple agent turns, identically matching the `omp` Python cell behavior.
- **Unified Path Routing:** Implementation of `omp`'s protocol schemes (e.g., `layer://`, `feature://`) so the agent uses a single unified `read` tool to extract data whether it's from a file, a database, or a live QGIS layer.

## 4. Multimodal Vision
- **Canvas Capture:** The engine will implement native `capture_canvas` and `inspect_ui` tools. The agent will physically "see" the QGIS Map Canvas by feeding high-resolution snapshots to its Vision model.
- **Visual Validation:** This allows the agent to iteratively tweak map styling, symbology, and geometry rendering by verifying the pixel output directly.

## 5. Memory & Subagents
- **Hindsight Bank:** Implementation of `retain`, `recall`, and `reflect` tools. The agent will build a persistent, project-scoped memory bank that survives across QGIS sessions.
- **Parallel Subagents (`task`):** For heavy computational or research workloads, the core engine will dispatch isolated subagent workers. These workers will execute in separate threads and return strictly validated JSON results to the main agent, preventing context pollution.

## 6. Implementation Scope & Order
1. **Engine Core:** Scaffold the new `aery_engine.py` with the streaming loop and TTSR support.
2. **Tools & Vision:** Migrate the AST sandbox into the new tool registry and build the `capture_canvas` and `edit` tools.
3. **Memory & Subagents:** Build the Hindsight local database and the worker thread dispatcher.
4. **UI Integration:** Disconnect the QGIS UI from the legacy agent and wire it seamlessly to the new OMP-powered backend.
