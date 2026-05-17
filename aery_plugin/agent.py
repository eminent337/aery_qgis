"""Agent core for the Aery QGIS plugin.

Manages the conversation loop, tool calling, and context building.
Calls LLM APIs directly via llm_client.py.
"""

import json
import os
from typing import Any, Callable, Optional

from aery_plugin.llm_client import create_client, APIError
from aery_plugin.tools import ToolRegistry


class Agent:
    """The geospatial AI agent."""

    def __init__(self, executor, iface=None):
        self.executor = executor
        self.iface = iface
        self.tools = ToolRegistry(executor, iface)
        self._messages: list[dict] = []
        self._client = None
        self._model = ""
        self._system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Build the geospatial system prompt from the rules JSON."""
        rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "geospatial_rules.json")
        with open(rules_path) as f:
            rules = json.load(f)

        lines = [
            f"You are {rules['identity']['role']}.",
            f"You can do anything: {rules['identity']['capabilities']}.",
            f"Workflow: {rules['identity']['workflow']}",
            "",
            "=== QGIS WORKFLOW ===",
        ]
        lines += list(rules.get("workflow_steps", []))
        lines += ["", "=== PROCESSING SEARCH FILTER ===", rules.get("processing_search_filter", "")]
        lines += ["", "=== GLOBALS ALWAYS AVAILABLE IN run_qgis_code ==="]
        lines += list(rules.get("globals_available", []))
        lines += [rules.get("globals_note", ""), "", "=== CRS RULES ==="]
        lines += list(rules.get("crs_rules", []))
        lines += ["", "=== SAFETY RULES ==="]
        lines += list(rules.get("safety_rules", []))
        lines += ["", "=== PROCESSING PATTERNS ==="]
        for k, v in rules.get("processing_patterns", {}).items():
            lines.append(f"# {k}:\n{v}")
        lines += ["", "=== STYLING IN CODE ==="]
        for k, v in rules.get("styling_code", {}).items():
            lines.append(f"# {k}:\n{v}")
        lines += ["", "=== ERROR RECOVERY ==="]
        lines += list(rules.get("error_recovery", []))

        # Advanced sections (Python/QGIS API specific)
        advanced = """
=== RASTER ANALYSIS ===
stats = layer.dataProvider().bandStatistics(1, QgsRasterBandStats.All)
import rasterio; from rasterio.warp import reproject, Resampling
ndvi = (nir.astype(float) - red.astype(float)) / (nir + red + 1e-10)

=== VECTOR DATA MANIPULATION ===
layer.startEditing()
layer.changeAttributeValue(feat.id(), layer.fields().indexOf('category'), 'urban')
layer.commitChanges()
idx = QgsSpatialIndex(layer.getFeatures()); nearby = idx.nearestNeighbor(QgsPointXY(x, y), 5)
da = QgsDistanceArea(); da.setEllipsoid('WGS84')

=== WEB DATA FETCHING ===
import urllib.request, json
query = '[out:json];node[amenity=hospital](bbox);out;'
url = f'https://overpass-api.de/api/interpreter?data={urllib.parse.quote(query)}'

=== MACHINE LEARNING IN QGIS ===
import numpy as np; from sklearn.ensemble import RandomForestClassifier
from sklearn.cluster import KMeans

=== NETWORK ANALYSIS ===
import networkx as nx; G = nx.Graph()
path = nx.shortest_path(G, source, target, weight='weight')

=== 3D AND TERRAIN ===
processing.run('qgis:hillshade', {'INPUT': dem, 'Z_FACTOR': 1.5, 'AZIMUTH': 315, 'V_ANGLE': 45, 'OUTPUT': f'{project_dir}/hillshade.tif'})
processing.run('native:slope', {'INPUT': dem, 'Z_FACTOR': 1.0, 'OUTPUT': f'{project_dir}/slope.tif'})
processing.run('gdal:contour', {'INPUT': dem, 'INTERVAL': 50, 'OUTPUT': f'{project_dir}/contours.gpkg'})

=== DISPLAY ON CANVAS ===
layer = QgsRasterLayer(output_path, 'result_name')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().setExtent(layer.extent()); iface.mapCanvas().refresh()

=== WEB MAP EXPORT ===
export_webmap(output_dir='./webmap', basemap='osm', include_search=False, title='My Map')

=== GEOSERVER PUBLISHING ===
publish_geoserver(layer='roads', geoserver_url='http://localhost:8080/geoserver',
                  username='admin', password='geoserver', workspace='my_workspace')

=== STYLE & VISUAL STATE ===
set_layer_style(layer='ndvi', style='singleband', colormap='RdYlGn', band=1, min=-1, max=1)
save_map_theme(theme_name='ndvi_view')
multi_map_layout(layout_name='comparison', output_path='/path/multi.pdf', paper_format='A3')
"""
        return "\n".join(lines) + advanced

    def _load_credentials(self) -> tuple[str, dict, str]:
        """Load provider credentials from oauth_helper.

        Returns (provider_id, auth_entry, model).
        """
        from aery_plugin import oauth_helper

        active = oauth_helper.get_active_provider()
        if not active:
            raise RuntimeError("No LLM provider configured. Open Settings to configure a provider.")

        provider_id = active["id"]
        model = active.get("model", "")

        auth = oauth_helper._load_auth()
        auth_entry = auth.get(provider_id, {})
        if not auth_entry.get("key") and not auth_entry.get("access"):
            raise RuntimeError(f"No credentials for provider '{provider_id}'. Please configure it in Settings.")

        return provider_id, auth_entry, model

    def initialize(self):
        """Set up the API client from current provider config."""
        provider_id, auth_entry, model = self._load_credentials()
        self._client, self._model = create_client(provider_id, auth_entry, model)

    def _build_context_message(self) -> str:
        """Build a QGIS environment context message."""
        try:
            from qgis.core import QgsProject
            proj = QgsProject.instance()
            layers = []
            for lyr in proj.mapLayers().values():
                info = f"  - {lyr.name()} [{lyr.type().name}, {lyr.crs().authid() if lyr.crs() else 'no CRS'}]"
                if hasattr(lyr, "featureCount"):
                    info += f" {lyr.featureCount()} features"
                if hasattr(lyr, "bandCount"):
                    info += f" {lyr.bandCount()} bands"
                layers.append(info)

            lines = [
                "=== QGIS ENVIRONMENT ===",
                f"Project: {proj.fileName() or '(unsaved)'}",
                f"Layers ({len(layers)}):",
            ] + (layers if layers else ["  (none)"])
            lines.append("=== END ENVIRONMENT ===")
            return "\n".join(lines)
        except Exception:
            return ""

    async def run(self, user_message: str, on_event: Optional[Callable] = None) -> str:
        """Run the agent with a user message.

        on_event: callback for streaming events (tool calls, text chunks).
        Returns the final assistant response text.
        """
        if not self._client:
            self.initialize()

        # Add context on first message
        if not self._messages:
            ctx = self._build_context_message()
            if ctx:
                self._messages.append({"role": "user", "content": f"[QGIS Context]\n{ctx}"})

        self._messages.append({"role": "user", "content": user_message})

        max_turns = 10
        for turn in range(max_turns):
            if on_event:
                on_event({"type": "thinking"})

            # Build messages with system prompt
            api_messages = [{"role": "system", "content": self._system_prompt}] + self._messages

            # Call LLM with streaming
            try:
                tools = self.tools.list_tools()
                full_content = ""
                tool_calls = []

                # Stream the response
                for chunk in self._client.chat_stream(
                    messages=api_messages,
                    model=self._model,
                    max_tokens=8192,
                    tools=tools if tools else None,
                ):
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    # Extract text content
                    content = delta.get("content", "")
                    if content:
                        full_content += content
                        if on_event:
                            on_event({"type": "text_chunk", "text": content})
                    # Extract tool calls (may arrive in separate chunks)
                    if delta.get("tool_calls"):
                        for tc in delta["tool_calls"]:
                            # Merge with existing tool calls by index
                            idx = tc.get("index", 0)
                            while len(tool_calls) <= idx:
                                tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
                            existing = tool_calls[idx]
                            if tc.get("id"):
                                existing["id"] = tc["id"]
                            if tc.get("function"):
                                if tc["function"].get("name"):
                                    existing["function"]["name"] += tc["function"]["name"]
                                if tc["function"].get("arguments"):
                                    existing["function"]["arguments"] += tc["function"]["arguments"]

                if not full_content and not tool_calls:
                    # Fallback: non-streaming response (some providers don't stream tools well)
                    response = self._client.chat(
                        messages=api_messages,
                        model=self._model,
                        max_tokens=8192,
                        tools=tools if tools else None,
                    )
                    choice = response.get("choices", [{}])[0]
                    message = choice.get("message", {})
                    full_content = message.get("content", "")
                    tool_calls = message.get("tool_calls", [])

            except APIError as e:
                return f"API error: {e}"

            if tool_calls:
                # Execute tools
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}

                    if on_event:
                        on_event({"type": "tool_start", "tool": name, "params": args})

                    try:
                        result = await self.tools.execute(name, args)
                        tool_result = str(result)
                        if on_event:
                            on_event({"type": "tool_done", "tool": name, "result": tool_result[:500]})
                    except Exception as e:
                        tool_result = f"Error: {e}"
                        if on_event:
                            on_event({"type": "tool_error", "tool": name, "error": str(e)})

                    self._messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tc],
                    })
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": tool_result,
                    })
            else:
                # Final response
                if full_content:
                    self._messages.append({"role": "assistant", "content": full_content})
                return full_content

        return "Agent reached maximum turns."

    def reset(self):
        """Clear conversation history."""
        self._messages = []

    def get_history(self) -> list[dict]:
        """Return conversation history."""
        return list(self._messages)
