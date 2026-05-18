# Aery QGIS Plugin

Run the Aery AI geospatial agent inside QGIS. Perform spatial analysis through a native PyQt Command Center with project validation, safe main-thread execution, visual proof cards, and an operation audit trail.

![Aery](aery_plugin/resources/icons/aery.svg)

## Requirements

- **QGIS 4.0+** — QGIS 4.0.2-Norrköping or later
- Python 3.10+

## Installation

```bash
cd aery-qgis-plugin
chmod +x install.sh
./install.sh
```

Then restart QGIS and enable the plugin in **Plugins → Manage and Install Plugins → Aery**.

## Usage

1. Open the Aery Command Center (**View → Panels → Aery**)
2. Configure a provider in **Settings** (OAuth subscription or API key)
3. Type a geospatial task in natural language
4. The agent inspects your project, validates common GIS risks, writes QGIS code, executes it, and records an audit trail

### Examples

- "Buffer all roads by 50 meters"
- "Select parcels within 500m of schools"
- "Create a heatmap of earthquake epicenters"
- "Reproject all layers to EPSG:3857"
- "Export the selected features as GeoJSON"
- "Export this project as a web map"
- "Apply a viridis colormap to the DEM"
- "Create a 2x2 comparison layout PDF"
- "Publish this layer to GeoServer"

## How It Works

```
┌──────────────────────────────┐
│  QGIS Python Plugin          │
│  - Chat Panel (PyQt6)        │
│  - Pure Python Agent         │
│  - Direct LLM API calls      │
│  - Thread-safe QTimer Queue  │
│  - 13 built-in tools         │
└──────────────────────────────┘
```

- The plugin runs a **pure Python agent** — no Node.js, no external dependencies
- The agent makes **direct LLM API calls** (OpenAI, Anthropic, Google Gemini)
- QGIS code executes **on the main thread** via a queue-based bridge (QTimer)
- Every executed code request is appended to `.aery/operations.jsonl` for review and reproducibility

## Provider System

### OAuth Subscriptions (free tier available)

- **Google Antigravity** — Gemini 3, Claude, GPT-OSS via Google Cloud
- **Google Gemini CLI** — Gemini 2.5/3, Claude via Cloud Code Assist
- **OpenAI Codex** — GPT-5 series via OpenAI
- **Anthropic** — Claude via Claude.ai
- **GitHub Copilot** — Claude, GPT, Gemini via GitHub

### API Key Providers

Anthropic, OpenAI, Google Gemini, Groq, Mistral, OpenRouter, Fireworks, DeepSeek, xAI, Cloudflare Workers AI, Cerebras, Hugging Face, Together AI, and more.

### Aery Gateway

One key from `aery-web.pages.dev` gives access to all providers through `aery-gateway.eminent337.workers.dev`.

## Built-in Tools

| Tool | Description |
|------|-------------|
| `run_qgis_code` | Execute any Python in the QGIS main thread |
| `get_project_context` | Snapshot all layers, CRS, extents, field schemas |
| `capture_canvas` | Export the live map canvas as a high-DPI PNG |
| `web_search` | Search the web via DuckDuckGo |
| `web_fetch` | Fetch and parse content from a URL |
| `export_webmap` | Export project as interactive Leaflet.js web map |
| `publish_geoserver` | Publish layers to GeoServer via REST API |
| `set_layer_style` | Apply visual styles (colormaps, RGB, graduated, categorized) |
| `multi_map_layout` | Create multi-panel print layout PDFs |
| `save_map_theme` | Save current layer visibility state |
| `load_map_theme` | Restore a saved map theme |
| `list_map_themes` | List all saved map themes |
| `refresh_canvas` | Refresh the QGIS map canvas |

All geospatial tools are also available inside `run_qgis_code` blocks as global functions.

## Architecture

| Component | File | Purpose |
|-----------|------|---------|
| Plugin entry | `aery_plugin/__init__.py` | QGIS `classFactory` |
| Plugin lifecycle | `aery_plugin/plugin.py` | init/unload, temp files, menu |
| Chat UI | `aery_plugin/chat_panel.py` | PyQt Command Center with structured transcript |
| Agent | `aery_plugin/agent.py` | Pure Python agent — conversation loop, tool calling, streaming |
| LLM clients | `aery_plugin/llm_client.py` | OpenAI, Anthropic, Gemini API clients with retry |
| Tools | `aery_plugin/tools.py` | Tool registry with 13 tools |
| Geospatial tools | `aery_plugin/geospatial_tools.py` | 8 geospatial tool implementations |
| Provider auth | `aery_plugin/oauth_helper.py` | OAuth, API key, and gateway credential management |
| Provider UI | `aery_plugin/provider_settings.py` | Provider config wizard, model switcher |
| Code executor | `aery_plugin/qgis_executor.py` | Thread-safe QTimer queue with full QGIS globals |
| Knowledge graph | `aery_plugin/graph_engine.py` | Provenance, session, spatial, tool, and algorithm graphs |

## Trust Features

- **Project validation** flags missing CRS, geographic CRS distance risks, empty layers, and large-layer operations before analysis.
- **Risk classification** tags generated code that may delete project data, delete files, overwrite files, or run shell commands.
- **Audit trail** records request ID, timestamp, success state, executed code, and risk tags in the active project directory.
- **Visual confirmation** uses `capture_canvas` after map-altering operations so results can be checked in context.
- **Activity strip** shows real-time status: `thinking`, `running tool`, `capturing snapshot`.

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

177 tests covering:
- Chat panel UI (54 tests)
- Provider settings (54 tests)
- QGIS executor (26 tests)
- Plugin lifecycle (6 tests)
- Integration (10 tests)
- Geospatial tools (5 tests)
- Agent (1 test)
- LLM client (1 test)
- Webmap export, map themes, multi-map layout, GeoServer publish, layer styling (20 tests)

## License

MIT © Aryee
