"""Agent core for the Aery QGIS plugin.

Manages the conversation loop, tool calling, and context building.
Calls LLM APIs directly via llm_client.py.
"""

import asyncio
import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Optional

from aery_plugin.llm_client import create_client, APIError
from aery_plugin.tools import ToolRegistry

try:
    from PyQt6.QtCore import QObject, pyqtSignal, QThread
    _HAS_PYQT6 = True
except ImportError:
    _HAS_PYQT6 = False
    QObject = object
    pyqtSignal = None
    QThread = None


class _AgentWorker(QObject):
    """Runs Agent.run() in a QThread so the Qt event loop stays responsive.

    Uses the opengeos/GeoAgent pattern: a brand-new asyncio event loop is
    created inside the worker thread — never touches the Qt main-loop.
    """
    finished = pyqtSignal(str)   # final assistant text
    error   = pyqtSignal(str)   # error string
    chunk   = pyqtSignal(dict)  # streaming event dict

    def __init__(self, agent):
        super().__init__()
        self._agent = agent
        self._user_message = ""

    def start_task(self, user_message: str) -> None:
        self._user_message = user_message

    def run(self) -> None:  # QThread.run() override
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            reply = loop.run_until_complete(
                self._agent.run(self._user_message, on_event=self.chunk.emit)
            )
            self.finished.emit(reply)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            loop.close()


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
        self._session_id: Optional[str] = None
        self._project_dir: Optional[str] = None
        # Permission suspend/resume state
        self._permission_needed: bool = False
        self._permission_tool_use_id: str = ""
        self._permission_tool_name: str = ""
        self._permission_request_id: str = ""
        self._permission_resolved: threading.Event = threading.Event()
        self._permission_approved: bool = False
        self._permission_always: bool = False

        # QThread worker for offloading async agent work off the Qt main thread
        if _HAS_PYQT6:
            self._worker = _AgentWorker(self)
            self._thread = QThread()
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.finished.connect(self._on_worker_finished)
            self._worker.error.connect(self._on_worker_error)
        else:
            self._worker = None
            self._thread = None

    def cancel(self) -> None:
        """Cancel the current agent turn if running."""
        self.cancel_permission()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(500)

    def start(self, user_message: str) -> None:
        """Post a user message and start processing in the QThread worker.

        If the worker is already running the message is queued locally by the
        caller (ChatPanel manages the queue).
        """
        if self._worker:
            self._worker.start_task(user_message)
            self._thread.start()
        else:
            import warnings
            warnings.warn("PyQt6 not available; agent.run() must be called directly")

    def is_busy(self) -> bool:
        """Return True if the agent thread is currently active."""
        return self._thread.isRunning() if self._thread else False

    # ── QThread worker callbacks (overridable by ChatPanel) ──────────────────
    def _on_worker_finished(self, reply: str) -> None:
        """Called in the Qt main thread when the worker completes successfully."""

    def _on_worker_error(self, error: str) -> None:
        """Called in the Qt main thread when the worker raises an exception."""

    def resolve_permission(self, approved: bool, always: bool = False) -> None:
        """Called from ChatPanel when the user approves or denies a pending
        permission request.  Wakes the agent's run() thread by setting the
        internal Event.

        Args:
            approved: True  → execute the tool after resume.
            always:   True  → switch to bypassPermissions mode.
        """
        self._permission_approved = approved
        self._permission_always   = always
        self._permission_resolved.set()

    def cancel_permission(self) -> None:
        """Called from ChatPanel when the user explicitly denies."""
        self.resolve_permission(approved=False)

    def reset_permission_state(self) -> None:
        """Clear stale permission state — call at the start of each turn."""
        self._permission_needed    = False
        self._permission_request_id = ""
        self._permission_approved  = False
        self._permission_always    = False
        self._permission_resolved.clear()

    def _try_failover(self):
        """Try to create a failover client using alternative providers.

        Returns a new client if failover is available, None otherwise.
        """
        try:
            from aery_plugin import oauth_helper
            from aery_plugin.llm_client import create_client

            # Get list of available providers with credentials
            active = oauth_helper.get_active_provider()
            if not active:
                return None

            current_provider = active.get("id", "")
            auth = oauth_helper._load_auth()

            # Try common failover providers
            failover_providers = ["openai", "anthropic", "google", "deepseek", "groq"]

            for provider_id in failover_providers:
                if provider_id == current_provider:
                    continue

                # Check if provider has credentials
                auth_entry = auth.get(provider_id, {})
                if not auth_entry:
                    continue

                # Try to create client
                try:
                    client, model = create_client(provider_id, auth_entry, active.get("model", ""))
                    if client:
                        return client
                except Exception:
                    continue

        except Exception:
            pass

        return None

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

        return provider_id, auth_entry, model

    def initialize(self):
        """Set up the API client from current provider config."""
        provider_id, auth_entry, model = self._load_credentials()
        self._client, self._model = create_client(provider_id, auth_entry, model)

    def reinitialize(self):
        """Force re-create the API client (call after provider/model change)."""
        self._client = None
        self._model = ""
        self.initialize()

    def _build_context_message(self) -> str:
        """Build a QGIS environment context message with graph context."""
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

            # Add graph context if available
            if self._project_dir:
                from aery_plugin.graph_engine import get_context_for_prompt, build_tool_capability_graph, auto_detect_spatial_relationships, prune_graph
                build_tool_capability_graph(self._project_dir)
                auto_detect_spatial_relationships(self._project_dir)
                prune_graph(self._project_dir)
                graph_ctx = get_context_for_prompt(self._project_dir)
                if graph_ctx:
                    lines.append(graph_ctx)

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

        # Add context on first message, refresh graph context on subsequent messages
        if not self._messages:
            ctx = self._build_context_message()
            if ctx:
                self._messages.append({"role": "user", "content": f"[QGIS Context]\n{ctx}"})
        elif self._project_dir:
            # Refresh graph context on each turn (layers may have changed)
            try:
                from aery_plugin.graph_engine import get_context_for_prompt, auto_detect_spatial_relationships
                auto_detect_spatial_relationships(self._project_dir)
                graph_ctx = get_context_for_prompt(self._project_dir, user_message)
                if graph_ctx:
                    self._messages.append({"role": "user", "content": f"[Graph Context]\n{graph_ctx}"})
            except Exception:
                pass

        self._messages.append({"role": "user", "content": user_message})
        self._persist_message({"role": "user", "content": user_message})

        # Record prompt in graph
        if self._project_dir:
            try:
                from aery_plugin.graph_engine import record_prompt
                record_prompt(self._project_dir, user_message, [], [])
            except Exception:
                pass

        max_turns = 10
        for turn in range(max_turns):
            self.reset_permission_state()
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
                async for chunk in self._client.chat_stream(
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
                    response = await self._client.chat(
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
                # Try failover on retryable errors (429, 500, 502, 503, 504)
                if e.retryable or e.status_code in (429, 500, 502, 503, 504):
                    try:
                        failover_client = self._try_failover()
                        if failover_client:
                            self._client = failover_client
                            if on_event:
                                on_event({"type": "text_chunk", "text": f"[Failover] Switching to alternative provider due to: {e}\n"})
                            continue  # Retry with new provider
                    except Exception:
                        pass
                return f"API error: {e}"

            if tool_calls:
                # Execute tools
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    had_error = False
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}

                    if on_event:
                        on_event({"type": "tool_start", "tool": name, "params": args})

                    try:
                        # Check permission before execution
                        code = args.get("code", "") if name == "run_qgis_code" else None
                        perm = self.tools.check_permission(name, args, code)
                        if perm["behavior"] == "ask":
                            if on_event:
                                on_event({
                                    "type": "permission_request",
                                    "request_id": str(uuid.uuid4()),
                                    "tool_name": name,
                                    "tool_use_id": tc.get("id", ""),
                                    "input": args,
                                    "description": perm.get("description", ""),
                                    "risk_level": perm.get("risk_level", "medium"),
                                    "uuid": str(uuid.uuid4()),
                                    "session_id": self._session_id,
                                })
                            self._permission_needed = True
                            self._permission_request_id = str(uuid.uuid4())
                            self._permission_resolved.clear()
                            had_error = False  # will be set by own branches below
                            try:
                                self._permission_resolved.wait(timeout=120)
                            except Exception:
                                pass
                            if not self._permission_approved:
                                tool_result = f"Permission denied — tool '{name}' not executed."
                                had_error = True
                            if self._permission_always:
                                self.tools.set_permission_mode("bypassPermissions")
                            elif self._permission_approved:
                                # Approved — take the allow-path snapshot + execute
                                snapshot = self._snapshot_layer_state(name, code or "")
                                result2 = await self.tools.execute(name, args)
                                tool_result = str(result2)
                                if on_event:
                                    on_event({
                                        "type": "tool_done", "tool": name,
                                        "result": tool_result[:500],
                                    })
                                result = result2
                                if self._project_dir:
                                    try:
                                        from aery_plugin.graph_engine import record_code_execution
                                        input_layers = args.get("layers", args.get("layer", ""))
                                        if isinstance(input_layers, str):
                                            input_layers = [input_layers] if input_layers else []
                                        output_files = []
                                        if isinstance(result2, dict):
                                            output_files = result2.get("files", result2.get("output_files", []))
                                            if isinstance(output_files, str):
                                                output_files = [output_files]
                                        record_code_execution(
                                            self._project_dir, name, args.get("code", ""),
                                            tool_result[:200], input_layers, output_files, True,
                                        )
                                    except Exception:
                                        pass
                                if snapshot:
                                    self._undo_stack.append({
                                        "tool": name, "snapshot": snapshot,
                                        "timestamp": self._get_timestamp(),
                                    })
                        elif perm["behavior"] == "deny":
                            tool_result = f"Permission denied: {perm.get('message', 'blocked by policy')}"
                            if on_event:
                                on_event({"type": "tool_error", "tool": name, "error": tool_result})
                            had_error = True
                        else:
                            snapshot = self._snapshot_layer_state(name, code or "")
                            result = await self.tools.execute(name, args)
                            tool_result = str(result)
                            if on_event:
                                on_event({
                                    "type": "tool_done", "tool": name,
                                    "result": tool_result[:500],
                                })
                            if self._project_dir:
                                try:
                                    from aery_plugin.graph_engine import record_code_execution
                                    input_layers = args.get("layers", args.get("layer", ""))
                                    if isinstance(input_layers, str):
                                        input_layers = [input_layers] if input_layers else []
                                    output_files = []
                                    if isinstance(result, dict):
                                        output_files = result.get("files", result.get("output_files", []))
                                        if isinstance(output_files, str):
                                            output_files = [output_files]
                                    record_code_execution(
                                        self._project_dir, name, args.get("code", ""),
                                        tool_result[:200], input_layers, output_files, True,
                                    )
                                except Exception:
                                    pass
                            if snapshot:
                                self._undo_stack.append({
                                    "tool": name, "snapshot": snapshot,
                                    "timestamp": self._get_timestamp(),
                                })
                    except Exception as e:
                        tool_result = f"Error: {e}"
                        if on_event:
                            on_event({"type": "tool_error", "tool": name, "error": str(e)})
                        had_error = True

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
                    self._persist_message({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "tool_name": name,
                        "content": tool_result[:2000],  # truncate for persistence
                    })
            else:
                # Final response
                if full_content:
                    self._messages.append({"role": "assistant", "content": full_content})
                    self._persist_message({"role": "assistant", "content": full_content})
                return full_content

        return "Agent reached maximum turns."

    def reset(self):
        """Clear conversation history and start fresh session."""
        # Stop worker thread if running
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(500)
        self._messages = []
        self._undo_stack = []
        self._self_correction_count = 0
        self.reset_permission_state()
        if self._project_dir:
            from aery_plugin.session import create_session
            self._session_id = create_session(self._project_dir)

    def get_history(self) -> list[dict]:
        """Return conversation history."""
        return list(self._messages)

    def list_sessions(self, project_dir: str) -> list[dict]:
        """List all sessions for a project."""
        from aery_plugin.session import list_sessions
        return list_sessions(project_dir)

    def start_session(self, project_dir: str) -> str:
        """Start a new persisted session. Returns session ID."""
        from aery_plugin.session import create_session
        self._project_dir = project_dir
        self._session_id = create_session(project_dir)
        return self._session_id

    def resume_session(self, project_dir: str, session_id: str) -> list[dict]:
        """Resume a previous session. Returns loaded messages."""
        from aery_plugin.session import load_session
        self._project_dir = project_dir
        self._session_id = session_id
        messages = load_session(project_dir, session_id)
        # Filter out session_start headers and context messages
        self._messages = [
            m for m in messages
            if m.get("role") in ("user", "assistant", "tool")
        ]
        return self._messages

    def _persist_message(self, msg: dict):
        """Persist a message to the session file."""
        if not self._session_id or not self._project_dir:
            return
        from aery_plugin.session import append_message
        append_message(self._project_dir, self._session_id, msg)

    def _snapshot_layer_state(self, tool_name: str, code: str) -> dict:
        """Capture a lightweight layer snapshot for undo support."""
        snapshot = {"tool": tool_name, "code": code, "timestamp": self._get_timestamp(), "layers": {}}
        try:
            from qgis.core import QgsProject
            for lyr in QgsProject.instance().mapLayers().values():
                snapshot["layers"][lyr.id()] = {
                    "name": lyr.name(),
                    "type": lyr.type().name,
                }
        except Exception:
            pass
        return snapshot

    def _get_timestamp(self) -> str:
        """Return an ISO-format timestamp."""
        import datetime
        return datetime.datetime.utcnow().isoformat() + "Z"
