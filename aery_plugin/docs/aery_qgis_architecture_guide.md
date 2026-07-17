# Aery QGIS Plugin Architecture & Developer Guide
*A comprehensive technical reference for the internal design and execution flow of the Aery QGIS plugin.*

---

## 1. Executive Summary
Aery is a professional geospatial AI agent running directly inside **QGIS (desktop application)**. Its core design goal is to allow users to manipulate, style, query, and analyze layers using natural language. 

Unlike web-based assistants that run in a restricted browser environment, Aery operates on the **QGIS main thread**, giving it access to QGIS's full C++ processing registry and the complete PyQGIS API. To do this safely and responsively, Aery uses a **background QThread/asyncio event loop** for network/LLM calls, and synchronizes PyQGIS execution with the main GUI thread via a **thread-safe queue and a 50ms QTimer**.

---

## 2. Directory Structure & Module Roles
The codebase is structured into five functional areas:

```
aery_plugin/
├── __init__.py               # Package entry, exposes classFactory() for QGIS
├── plugin.py                 # QGIS plugin lifecycle manager (menu actions, dock widgets)
├── chat_panel.py             # PyQt6 Chat Dock Panel UI (dock widget, layout, scroll areas)
├── input_area.py             # Chat input text box widget
├── transcript_view.py        # Message bubbles, tool cards, markdown rendering
├── activity_strip.py         # Thinking status bar widget
│
├── agent.py                  # Public facade, owns worker QThread and state machine
├── agent_permissions.py      # Gating registry and session approval flags
├── agent_dispatcher.py       # Parses tool calls and routes execution
│
├── qgis_executor.py          # Sandboxed main-thread Python runner
├── executor_socket.py        # Local TCP server for Node.js IPC with token auth
├── executor_canvas.py        # Renders map canvas to base64 PNG images
├── executor_audit.py         # Log writer for executed code blocks
├── sandbox.py                # Consolidated AST checker rules
│
├── llm_client.py             # Direct httpx-based Google/Anthropic/OpenAI clients
├── oauth_helper.py           # Canonical credentials helper module shim
│
├── tools.py                  # Base tool definitions
├── tool_defs/                # Tool schema libraries (raster, vector, layouts, GEE)
└── knowledge_base.py         # Local QGIS API documentation search indexes
```

---

## 3. Class Hierarchies & Core Dependencies
The primary classes and their ownership/dependency tree:

1. **`AeryPlugin` (`plugin.py`)**: Root QGIS plugin class loaded by QGIS.
   - Instantiates and owns `QGISCodeExecutor`.
   - Instantiates and owns `AeryChatPanel` dock widget.
2. **`AeryChatPanel` (`chat_panel.py`)**: PyQt6 `QDockWidget` subclass.
   - Instantiates and owns the public `Agent` facade.
   - Manages the transcript views and input area, forwarding user prompts to the agent.
3. **`Agent` (`agent.py`)**: Main agent interface inheriting from `QObject`.
   - Owns the background `QThread` and the run target `_AgentWorker`.
   - Owns `PermissionManager` (gating), `AgentDispatcher` (routing), `ToolRegistry`, and `KnowledgeBase`.
4. **`_AgentWorker` (`agent.py`)**: Runs in the background thread.
   - Establishes a new `asyncio` event loop.
   - Executes `Agent.run()` asynchronously, emitting PyQt signals (`chunk`, `finished`, `error`) back to the main thread.
5. **`AgentDispatcher` (`agent_dispatcher.py`)**: Evaluates tool calls.
   - Routes python code to the `QGISCodeExecutor`'s task queue.
6. **`QGISCodeExecutor` (`qgis_executor.py`)**: Main-thread code runner inheriting from `QObject`.
   - Owns `SocketServer` (IPC), `CanvasCapture` (canvas rendering), and `AuditLogger`.
   - Executes Python scripts in the QGIS main namespace.

---

## 4. End-to-End Request Execution Flow

```
[User prompt] ──> [AeryChatPanel] ──> [Agent.start()] ──> [_AgentWorker QThread]
                                                                  │
                                                        (runs asyncio loop)
                                                                  │
[ChatPanel] <── [Qt Signals] <── [LLM Client stream] <─── [Agent.run()]
                                                                  │ (if tool_call)
                                                                  ▼
[ChatPanel] <── [Gating Dialog] <── [PermissionManager] <── [AgentDispatcher]
      │                                                           │
 (approved) ──────────────────────────────────────────────────────┘
      │
      ▼
[QGIS Main Thread] ──> [QGISCodeExecutor QTimer] ──> [exec(code, sandbox_g)] ──> [PyQGIS / Canvas]
                                                                                         │
[QThread Agent] <── [Result Queue] <── [AuditLogger] <── [processEvents() poll] <────────┘
      │
      ▼
[LLM Turn 2] ──> [Finished Signal] ──> [ChatPanel displays final response]
```

---

## 5. Background/Main Thread Synchronization
Since QGIS GUI and C++ model APIs are not thread-safe, all PyQGIS execution must happen on QGIS's main thread. Aery accomplishes this safely using a queue-based synchronization bridge:

1. **Background QThread (`_AgentWorker`)**: Runs LLM chat completions and client stream processing in a dedicated thread. This keeps network latency entirely off QGIS's GUI, preventing "Not Responding" windows.
2. **Thread-Safe Queues**: When the agent dispatches `run_qgis_code`, it pushes the task payload (code string, run ID) into a thread-safe `queue.Queue`.
3. **Main-Thread QTimer**: The `QGISCodeExecutor` maintains a `QTimer` firing every 50ms on the main Qt thread. It checks the queue, pulls the task, runs AST/sandbox validations, and executes the Python code via `exec(code, sandbox_g)` in the main namespace.
4. **Main-Thread Event Pumping**: If the sandboxed code runs a QGIS processing algorithm, Aery patches `processing.run` dynamically. It runs the algorithm inside a `QgsProcessingAlgRunnerTask` using QGIS's background task manager, but polls for completion on the main thread using:
   ```python
   while not result_holder:
       QApplication.processEvents()
       time.sleep(0.05)
   ```
   This lets QGIS paint and respond to user clicks while the heavy algorithm runs in the background.

---

## 6. QGIS Integration Points & Safety

### A. The AST Sandbox (`sandbox.py`)
Before executing Python code, the executor passes it through an AST checker that:
* Blocks forbidden imports (e.g. `subprocess`, `requests`, `urllib`).
* Blocks star imports (`from x import *`) and relative imports.
* Blocks magic attributes (`__dict__`, `__globals__`).
* Blocks string-construction bypasses (`chr`, `ord`, `format`) commonly used in sandbox escapes.

### B. Canvas Capture (`executor_canvas.py`)
To allow the agent to visually verify its output:
1. Grabs `iface.mapCanvas()`.
2. Instantiates a `QImage` matching the canvas viewport size.
3. Renders the map canvas layers onto the `QImage` via a `QPainter`.
4. Saves the image into a `QBuffer` as a PNG.
5. Returns a base64 PNG data URI back to the agent.

### C. Socket IPC Auth (`executor_socket.py`)
To support external executions from Node.js (via the companion runner):
* Opens a TCP socket on `127.0.0.1`.
* Generates a random `secrets.token_urlsafe(32)` token at startup.
* Requires the token in the incoming JSON payload. Missing or invalid tokens are rejected with `Unauthorized` and the socket is immediately closed.

---

## 7. State Persistence & Session Lifecycle
* **Message Serialization**: The conversation is stored as a list of role/content dictionaries (OpenAI format).
* **State Persistence**: On message append or turn completion, the agent calls `_persist_state()` which writes to `settings.json` inside the QGIS project directory.
* **Metadata stored**: The `undo_stack` (canvas rollback history), `tool_loop_count` (loop counters), `tool_history` (oscillation checks), and `bypass_permissions` (always-allow flags).
* **Lifecycle**: `start_session(project_dir)` loads or initializes the JSON project state. `resume_session()` restores message history. `list_sessions()` scans the project folder for active session JSON files.

---

## 8. Dedicated Geospatial Tools & Caching

To enhance speed and reliability, Aery incorporates a set of dedicated, single-purpose tools and caching mechanisms:

### A. Dedicated Narrow Tools
Instead of relying solely on raw code execution, Aery registers specific geospatial tools in the registry:
* **Canvas View Tools**: `zoom_to_layer`, `set_map_extent`, `pan_to`, and `refresh_canvas`.
* **Layer Manipulation Tools**: `toggle_layer_visibility`, `set_layer_style`, `export_layer`, `remove_layer`, and `run_processing_algorithm`.
These tools construct well-defined Python snippets and run them via the QGIS main thread, failing fast with clear errors if the target layer does not exist.

### B. Background Parameter Validation
Before dispatching code to the QGIS main thread, Aery's background thread performs runtime validation of parameters against JSON schemas:
* **Data Constraint Checks**: Range (`minimum`/`maximum`), enum validation, and regex `pattern` matching.
* **Format Prechecks**: Enforces CRS syntax compliance (e.g. `EPSG:\d+`) and validates layer names/IDs against currently loaded project layers.
* **Type Coercion**: Automatically coerces valid JSON strings into dictionaries or lists when expected by the tool schema.

### C. Project Context Caching
To avoid expensive calls to retrieve the QGIS project state:
* **Cached Snapshot**: Serialized project metadata (layers, CRS, fields) is cached inside the `ToolRegistry`.
* **Signals & Mutation Invalidation**: The cache is marked dirty upon project mutations, mutating tool executions (e.g. layer styling, removal, visibility toggle), or QGIS project signals (`readProject`, `projectSaved`, `layersAdded`, `layersRemoved`).

### D. Prompt Escalation Policy
* The agent's system prompt enforces a strict escalation policy: prefer dedicated, narrow tools for basic map and layer operations, falling back to `run_qgis_code` only when custom scripts, complex analyses, or advanced PyQt6 styling are required.


---

## 9. Advanced Performance & Latency Upgrades

To achieve sub-millisecond execution times and minimal LLM overhead, Aery includes the following speed and robustness features:

### A. Instantaneous Signal-Based Dispatch
Instead of polling the thread-safe task queue with a 50ms `QTimer` (which adds up to 150ms of delay in multi-turn tool chains), Aery uses a custom PyQt6 `pyqtSignal` (`request_received`).
* **Cross-Thread Invocation**: The background socket thread emits the signal after queuing a task.
* **Immediate Main-Thread Slot**: The slot is processed instantly by the QGIS main event loop (typically in `<1ms`), eliminating polling latency.

### B. Lazy Context & Tiered Schemas
To optimize prompt token consumption and speed up context retrieval, project state is split:
* **Tier 1 (Lightweight Summary)**: `get_project_context` returns only basic layer properties (ID, name, type, CRS, visibility). This reduces context size by up to 80% on large projects.
* **Tier 2 (On-Demand Schema)**: The dedicated `get_layer_schema` tool retrieves fields, feature counts, extents, and raster details only when the agent explicitly requests detailed attributes of a specific layer.

### C. Speculative Stream Validation
* **Partial-JSON Parsing**: As tool argument strings stream from the LLM, Aery uses a custom parser (`_try_parse_partial_json`) to close open brackets and parse incomplete JSON objects on-the-fly.
* **Early Semantic Checks**: Arguments are pre-validated against schemas and layer references are resolved *before* the completion stream finishes, allowing early warnings or fail-fast recovery.

### D. Dynamic Semantic Code Snippets
* **Snippet Registry**: A database of QGIS 4.0 and PyQt6 API patterns (styling vector/raster layers, creating memory layers, running algorithms).
* **Dynamic Prompt Injection**: Matches keywords in the user request to automatically inject relevant PyQGIS reference snippets into the prompt, reducing model code-generation syntax errors.

### E. Out-of-Process Geoprocessing
* **Headless execution**: When `out_of_process` is set, Aery uses the `qgis_process` CLI to execute the algorithm in a separate background OS process.
* **Responsive GUI**: Keeps QGIS completely responsive during intensive processing tasks, automatically falling back to in-process execution if memory layers are detected as inputs.

