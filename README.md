# Aery QGIS Plugin

Run the Aery AI geospatial agent inside QGIS. Perform spatial analysis through a native PyQt Command Center with project validation, safe main-thread execution, visual proof cards, Claude-style activity status, and an operation audit trail.

![Aery](aery_plugin/resources/icons/aery.svg)

## Requirements

- **QGIS 4.0+** — QGIS 4.0.2-Norrköping or later

## Installation

```bash
cd aery-qgis-plugin
chmod +x install.sh
./install.sh
```

Then restart QGIS and enable the plugin in **Plugins → Manage and Install Plugins → Aery**.

## Usage

1. Open the Aery Command Center (**View → Panels → Aery**)
2. Type a geospatial task in natural language
3. The agent inspects your project, validates common GIS risks, writes QGIS code, executes it, and records an audit trail

### Examples

- "Buffer all roads by 50 meters"
- "Select parcels within 500m of schools"
- "Create a heatmap of earthquake epicenters"
- "Reproject all layers to EPSG:3857"
- "Export the selected features as GeoJSON"
- "What layers are in my current project?"
- "Validate this project before analysis"
- "Show me what operations Aery has run in this project"

## How It Works

```
┌──────────────────────────────┐       ┌──────────────────────┐
│  QGIS Python Plugin          │       │  Aery RPC (Node.js)  │
│  - Chat Panel (PyQt6)        │◄──────►  - LLM Agent        │
│  - QGIS Code Executor        │ TCP   │  - Embedded GIS Tools│
│  - Thread-safe QTimer Queue  │       │  - Audit/Validation  │
└──────────────────────────────┘       └──────────────────────┘
```

- The plugin spawns the bundled **Aery QGIS runner** as a Node.js subprocess
- The runner registers embedded QGIS tools (`run_qgis_code`, `get_project_context`, `validate_project`, `run_processing`, `capture_canvas`, `get_audit_trail`, etc.)
- Tools communicate **via local TCP socket** back to the plugin
- QGIS code executes **on the main thread** via a queue-based bridge (QTimer)
- Every executed code request is appended to `.aery/operations.jsonl` for review and reproducibility

## Trust Features

- **Project validation** flags missing CRS, geographic CRS distance risks, empty layers, and large-layer operations before analysis.
- **Risk classification** tags generated code that may delete project data, delete files, overwrite files, or run shell commands.
- **Audit trail** records request ID, timestamp, success state, executed code, and risk tags in the active project directory.
- **Visual confirmation** uses `capture_canvas` after map-altering operations so results can be checked in context.
- **Command Center UI** replaces raw text output with structured cards, a left rail for tool windows, and a right proof/context pane.
- **Activity strip** replaces the old raw streaming bar with a blinking `✻` status such as `thinking`, `running processing`, or `capturing snapshot`.
- **Crash hardening** disconnects UI signals before runner shutdown and suppresses normal disconnect events during plugin unload/restart.

## Architecture

| Component | File | Purpose |
|-----------|------|---------|
| Plugin entry | `aery_plugin/__init__.py` | QGIS `classFactory` |
| Plugin lifecycle | `aery_plugin/plugin.py` | init/unload, temp files, menu |
| Chat UI | `aery_plugin/chat_panel.py` | Native PyQt Command Center: rail menus, structured transcript, context/proof pane, activity strip |
| RPC bridge | `aery_plugin/rpc_bridge.py` | Aery subprocess stdin/stdout |
| Code executor | `aery_plugin/qgis_executor.py` | TCP socket + thread-safe QTimer queue |
| QGIS runner | `runner/entry.ts` | Embedded geospatial tools, prompt, provider loading |

## Development

```bash
# Install locally for development
./install.sh

# Run tests
cd aery-qgis-plugin
python3 -m pytest tests/ -v

# View plugin structure
find . -type f | sort
```

## Test Status

Core components:
- **QGIS Code Executor** — 7 tests (socket server, request/response, errors, concurrency, shutdown)
- **Runner Integration** — binary contract and embedded tool tests
- **RPC Bridge** — 7 tests (spawn, errors, commands, events, shutdown)
- **Chat Panel** — 17 tests (UI creation, messaging, abort, error states, rendering)
- **Plugin** — 7 tests (initialization, lifecycle, cleanup)

## License

MIT © Aryee
