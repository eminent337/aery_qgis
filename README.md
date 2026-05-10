# Aery QGIS Plugin

Run the Aery AI coding agent inside QGIS. Perform geospatial operations through natural language.

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

1. Open the Aery panel (**View → Panels → Aery**)
2. Type a geospatial task in natural language
3. The agent will inspect your project, write QGIS code, and execute it

### Examples

- "Buffer all roads by 50 meters"
- "Select parcels within 500m of schools"
- "Create a heatmap of earthquake epicenters"
- "Reproject all layers to EPSG:3857"
- "Export the selected features as GeoJSON"
- "What layers are in my current project?"

## How It Works

```
┌──────────────────────────────┐       ┌──────────────────────┐
│  QGIS Python Plugin          │       │  Aery RPC (Node.js)  │
│  - Chat Panel (PyQt6)        │◄──────►  - LLM Agent        │
│  - QGIS Code Executor        │ TCP   │  - Tool Execution    │
│  - Thread-safe QTimer Queue  │       │  - Extensions        │
└──────────────────────────────┘       └──────────────────────┘
```

- The plugin spawns **Aery in RPC mode** as a Node.js subprocess
- A **generated extension** (`.mjs`) registers QGIS tools (run_qgis_code, get_project_context, run_processing, add_layer, etc.)
- Tools communicate **via local TCP socket** back to the plugin
- QGIS code executes **on the main thread** via a queue-based bridge (QTimer)

## Architecture

| Component | File | Purpose |
|-----------|------|---------|
| Plugin entry | `aery_plugin/__init__.py` | QGIS `classFactory` |
| Plugin lifecycle | `aery_plugin/plugin.py` | init/unload, temp files, menu |
| Chat UI | `aery_plugin/chat_panel.py` | QDockWidget panel with message log |
| RPC bridge | `aery_plugin/rpc_bridge.py` | Aery subprocess stdin/stdout |
| Code executor | `aery_plugin/qgis_executor.py` | TCP socket + thread-safe QTimer queue |
| Extension builder | `aery_plugin/extension_builder.py` | Generates the Aery .mjs extension |

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

All tests pass. Core components:
- **QGIS Code Executor** — 7 tests (socket server, request/response, errors, concurrency, shutdown)
- **Extension Builder** — 1 test (file generation with port)
- **RPC Bridge** — 7 tests (spawn, errors, commands, events, shutdown)
- **Chat Panel** — 17 tests (UI creation, messaging, abort, error states, rendering)
- **Plugin** — 7 tests (initialization, lifecycle, cleanup)

## License

MIT © Aryee
