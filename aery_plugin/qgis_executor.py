"""Thread-safe QGIS Python code execution via local TCP socket + main-thread queue."""

import base64
import json
import os
import queue
import secrets
import socket
import threading
import time
import traceback
import uuid
from collections import deque
from typing import Any, Optional
# Import sandbox proxies for export and use in exec globals
from aery_plugin.sandbox import (
    _make_os_proxy,
    _make_subprocess_proxy,
    _make_shutil_proxy,
    _make_urllib_proxy,
    _make_builtins_proxy,
    _make_sandbox_exec_globals,
)
from PyQt6.QtCore import QObject, QTimer
# Cached globals — built once on first execution, reused for all subsequent calls

class _ProgressStdout:
    """Tee stdout during code execution to capture print() output as progress events.

    Adapted from GeoAI worker pattern (_ProgressStdout in geoai_task_worker.py).
    Only captures output from the designated execution thread to avoid cross-talk.
    """

    def __init__(self, real_stdout, emit_fn, owner_thread):
        self._real = real_stdout
        self._emit = emit_fn
        self._owner = owner_thread
        self._buf = ""

    def write(self, text: str) -> int:
        try:
            self._real.write(text)
        except Exception:
            pass
        if threading.current_thread() is self._owner:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line_str = line.strip()
                if line_str:
                    try:
                        self._emit(line_str)
                    except Exception:
                        pass
        return len(text)

    def flush(self) -> None:
        try:
            self._real.flush()
        except Exception:
            pass
        if threading.current_thread() is self._owner and self._buf.strip():
            line_str = self._buf.strip()
            self._buf = ""
            try:
                self._emit(line_str)
            except Exception:
                pass

    def __getattr__(self, name):
        return getattr(self._real, name)
_GLOBALS_CACHE: Optional[dict[str, Any]] = None
# ── Sanitization: Block dangerous canvas background manipulation ──
def _sanitize_code(code: str) -> str:
    """Remove/block code that manipulates canvas background brush/pixmap.
    Prevents LLM from setting static images as canvas background from captured screenshots.
    """
    dangerous_patterns = [
        "setBackgroundBrush",
        "backgroundBrush",
        "backgroundPixmap",
        "setBackgroundPixmap",
        "setStyleSheet",
        "setBackgroundRole",
        "backgroundRole",
        "QPalette",
        "setAutoFillBackground",
        "autoFillBackground",
        "QBrush",
        "QPixmap",
        "loadFromData",
        "fromData",
        "QImage",
        "base64.b64decode",
        "base64.decodebytes",
        "data:image",
    ]
    for pattern in dangerous_patterns:
        if pattern in code:
            raise ValueError(f"Canvas background manipulation blocked: '{pattern}' not allowed. Use proper layers instead.")
    return code
def _build_globals() -> dict[str, Any]:
    """Import every useful QGIS/PyQt6/geo class once and cache."""
    global _GLOBALS_CACHE
    if _GLOBALS_CACHE is not None:
        return _GLOBALS_CACHE

    g: dict[str, Any] = {}

    # ── stdlib always available ──
    import base64 as _b64, json as _json, os as _os, math as _math
    import re as _re, csv as _csv, pathlib as _pathlib, datetime as _dt
    import urllib.request as _urlreq, urllib.parse as _urlparse
    import subprocess as _sub, shutil as _shutil, tempfile as _tmp
    import statistics as _stats, collections as _coll, itertools as _it
    g.update({
        "base64": _b64, "json": _json, "os": _os, "math": _math,
        "re": _re, "csv": _csv, "pathlib": _pathlib, "datetime": _dt,
        "urllib": __import__("urllib"), "subprocess": _sub, "shutil": _shutil,
        "tempfile": _tmp, "statistics": _stats, "collections": _coll,
        "itertools": _it,
    })

    # ── QGIS Core — dynamically import every class to avoid version conflicts ──
    try:
        import qgis.core as qc
        core_classes = [
            "Qgis", "QgsApplication", "QgsCoordinateReferenceSystem", "QgsCoordinateTransform",
            "QgsCoordinateTransformContext", "QgsDataSourceUri", "QgsDistanceArea", "QgsExpression",
            "QgsExpressionContext", "QgsExpressionContextUtils", "QgsFeature", "QgsFeatureRequest",
            "QgsField", "QgsFields", "QgsGeometry", "QgsLayerTreeGroup", "QgsLayerTreeLayer",
            "QgsMapLayer", "QgsMapLayerType", "QgsMapSettings", "QgsMapThemeCollection",
            "QgsMarkerSymbol", "QgsMessageLog", "QgsPalLayerSettings", "QgsPoint", "QgsPointCloudLayer",
            "QgsPointXY", "QgsProcessingFeedback", "QgsProject", "QgsRasterBandStats", "QgsRasterLayer",
            "QgsRectangle", "QgsRendererRange", "QgsSingleSymbolRenderer", "QgsSpatialIndex",
            "QgsSymbol", "QgsSymbolLayer", "QgsTextFormat", "QgsVectorDataProvider", "QgsVectorFileWriter",
            "QgsVectorLayer", "QgsVectorLayerUtils", "QgsWkbTypes",
            # Layout classes
            # Layout and Cartography classes
            "QgsLayout", "QgsLayoutItemLabel", "QgsLayoutItemLegend", "QgsLayoutItemMap",
            "QgsLayoutItemMapGrid", "QgsLayoutItemMapOverview", "QgsLayoutItemNorthArrow",
            "QgsLayoutItemPage", "QgsLayoutItemPicture", "QgsLayoutItemScaleBar",
            "QgsLayoutItemPolygon", "QgsLayoutItemPolyline", "QgsLayoutItemHtml",
            "QgsLayoutItemAttributeTable", "QgsLayoutMeasurement", "QgsLayoutObject",
            "QgsLayoutPoint", "QgsLayoutSize", "QgsLayoutUnit", "QgsLayoutItem",
            "QgsLayoutUnits", "QgsPageLayout", "QgsPrintLayout", "QgsLayoutExporter",
            # Pseudocolor/renderer & Symbology
            "QgsFillSymbol", "QgsLineSymbol", "QgsSimpleFillSymbolLayer", "QgsSimpleLineSymbolLayer",
            "QgsSimpleMarkerSymbolLayer", "QgsCategorizedSymbolRenderer", "QgsRendererCategory",
            "QgsColorRampShader", "QgsRasterShader", "QgsSingleBandPseudoColorRenderer",
            "QgsSingleBandGrayRenderer", "QgsGraduatedSymbolRenderer", "QgsClassificationQuantile",
            "QgsVectorLayerSimpleLabeling"
        ]
        for name in core_classes:
            if hasattr(qc, name):
                g[name] = getattr(qc, name)
    except ImportError:
        pass

    # ── QGIS GUI ──
    try:
        import qgis.gui as qg
        gui_classes = ["QgsMapCanvas", "QgsMapToolEmitPoint", "QgsRubberBand", "QgsVertexMarker"]
        for name in gui_classes:
            if hasattr(qg, name):
                g[name] = getattr(qg, name)
    except ImportError:
        pass
    # ── PyQt6 ──
    try:
        from PyQt6.QtCore import Qt, QVariant, QDate, QDateTime
        from PyQt6.QtGui import QColor, QFont, QImage, QPainter
        from PyQt6.QtWidgets import QApplication, QMessageBox
        g.update({
            "Qt": Qt, "QVariant": QVariant, "QDate": QDate, "QDateTime": QDateTime,
            "QColor": QColor, "QFont": QFont, "QImage": QImage, "QPainter": QPainter,
            "QApplication": QApplication, "QMessageBox": QMessageBox,
        })
    except ImportError:
        pass

    # ── Processing ──
    try:
        import processing
        g["processing"] = processing
    except ImportError:
        g["processing"] = None

    # ── Optional scientific stack ──
    for mod_name, alias in [
        ("numpy", "np"), ("pandas", "pd"), ("matplotlib.pyplot", "plt"),
        ("scipy", "scipy"), ("sklearn", "sklearn"), ("shapely.geometry", "shapely_geom"),
        ("geopandas", "gpd"), ("rasterio", "rasterio"), ("fiona", "fiona"),
        ("pyproj", "pyproj"), ("networkx", "nx"),
    ]:
        try:
            g[alias] = __import__(mod_name, fromlist=[""])
        except ImportError:
            pass

    # Import _build_leaflet_html from geospatial_tools (single source of truth)
    try:
        from aery_plugin.geospatial_tools import _build_leaflet_html
        g["_build_leaflet_html"] = _build_leaflet_html
    except ImportError:
        pass

    # Inject geospatial helper functions into globals so they're available
    # inside run_qgis_code blocks (Approach 2)
    try:
        from aery_plugin.geospatial_tools import (
            export_webmap as _export_webmap,
            publish_geoserver as _publish_geoserver,
            set_layer_style as _set_layer_style,
            multi_map_layout as _multi_map_layout,
            save_map_theme as _save_map_theme,
            load_map_theme as _load_map_theme,
            list_map_themes as _list_map_themes,
            refresh_canvas as _refresh_canvas,
            safe_to_file as _safe_to_file,
            clean_proj_env as _clean_proj_env,
            smooth_geometry as _smooth_geometry,
            regularize_polygon as _regularize_polygon,
            get_city_bbox as _get_city_bbox,
            search_stac as _search_stac,
            load_cog_layer as _load_cog_layer,
            get_gee_tile_url as _get_gee_tile_url,
            load_gee_tile_layer as _load_gee_tile_layer,
            resolve_layer as _resolve_layer,
            safe_create_geodataframe as _safe_create_geodataframe,
            query_overpass as _query_overpass,
            run_quickosm_query as _run_quickosm_query,
            georeference_image as _georeference_image,
        )
        g.update({
            "export_webmap": _export_webmap,
            "publish_geoserver": _publish_geoserver,
            "set_layer_style": _set_layer_style,
            "multi_map_layout": _multi_map_layout,
            "save_map_theme": _save_map_theme,
            "load_map_theme": _load_map_theme,
            "list_map_themes": _list_map_themes,
            "refresh_canvas": _refresh_canvas,
            "safe_to_file": _safe_to_file,
            "clean_proj_env": _clean_proj_env,
            "smooth_geometry": _smooth_geometry,
            "regularize_polygon": _regularize_polygon,
            "get_city_bbox": _get_city_bbox,
            "search_stac": _search_stac,
            "load_cog_layer": _load_cog_layer,
            "get_gee_tile_url": _get_gee_tile_url,
            "load_gee_tile_layer": _load_gee_tile_layer,
            "resolve_layer": _resolve_layer,
            "safe_create_geodataframe": _safe_create_geodataframe,
            "query_overpass": _query_overpass,
            "run_quickosm_query": _run_quickosm_query,
            "georeference_image": _georeference_image,
        })
    except ImportError:
        pass

    try:
        g["_resolve_question"] = _resolve_question  # type: ignore[name-defined]
    except NameError:
        def _resolve_question_stub(*a, **kw):  # type: ignore[misc]
            pass
        g["_resolve_question"] = _resolve_question_stub

    _GLOBALS_CACHE = g
    return g


# Pending question callbacks: quest_id → (result_queue, req_id)
_pending_questions: dict[str, tuple[queue.Queue, str]] = {}


def _resolve_question(quest_id: str, answer: dict) -> None:
    """Called from chat_panel _on_event when the user submits a question card."""
    pending = _pending_questions.pop(quest_id, None)
    if pending:
        result_queue, _ = pending
        try:
            result_queue.put(answer)
        except Exception:
            pass


def _find_chat_panel() -> Optional[Any]:
    """Walk top-level widgets and return the ChatPanel instance if present."""
    try:
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance()
        if _app is None:
            return None
        for w in _app.topLevelWidgets():
            if hasattr(w, "_handle_question") and hasattr(w, "_feed_layout"):
                return w
    except Exception:
        pass
    return None


def _process_question(req_id: str, result_queue: queue.Queue, params: dict) -> dict:
    """Render a question card in the chat panel OR post a QEvent as fallback.

    Locates ChatPanel via _find_chat_panel() and calls _handle_question directly
    so the card appears in the feed at once. When no panel is found (headless test
    environments) a synthetic QEvent is posted via QApplication so that a
    pre-seeded _resolve_question() call still wakes the poll loop.

    Returns the answer dict or an error/timeout dict.
    """
    quest_id   = params.get("questId") or str(uuid.uuid4())
    reply_q    = queue.Queue()
    _pending_questions[quest_id] = (reply_q, req_id)

    # Build event payload
    event_payload = {"type": "question", "questId": quest_id, **params}

    # ── Fast path: ChatPanel is live → call _handle_question directly ──────────
    _QApp = None
    delivered = False
    try:
        from PyQt6.QtWidgets import QApplication as _QApp
        _app = _QApp.instance()
        if _app is not None:
            panel = _find_chat_panel()
            if panel is not None:
                panel._handle_question(event_payload)
                delivered = True
    except Exception:
        pass

    # ── Fallback: post a QEvent so a pre-seeded _resolve_question can deliver ──
    if not delivered:
        try:
            from PyQt6.QtCore import QEvent as _QEvent
            class _AskUserQEvent(_QEvent):
                def __init__(self, payload):
                    super().__init__(_QEvent.Type.User)
                    self._payload = payload
            _QApp.instance().postEvent(_QApp.instance(), _AskUserQEvent(event_payload))
        except Exception:
            pass   # best-effort; the poll loop handles absence of a panel

    # ── Poll reply_q and deliver answer into the caller's result_queue ─────────
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        QApplication = None  # type: ignore[assignment]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            answer = reply_q.get(timeout=0.1)
            _pending_questions.pop(quest_id, None)
            response = {"answer": answer}
            result_queue.put(response)
            return response
        except queue.Empty:
            if QApplication is not None:
                try:
                    QApplication.processEvents()
                except Exception:
                    pass

    _pending_questions.pop(quest_id, None)
    response = {"error": "Question timed out after 120 s"}
    result_queue.put(response)
    return response


class QGISCodeExecutor(QObject):
    """Executes Python code in QGIS's main thread safely.

    Starts a TCP socket server in a background thread. Requests arriving on
    the socket are queued and processed on the QGIS main thread via a QTimer.
    """

    def __init__(self, iface: Optional[Any] = None, audit_dir: Optional[str] = None):
        super().__init__()
        self.iface = iface
        self.audit_dir = audit_dir
        self.run_id = str(uuid.uuid4())
        # Dual-queue: priority deque for interactive / blocking requests;
        # normal queue for fire-and-forget run_code calls.
        self._priority_queue: deque = deque()
        self._normal_queue: queue.Queue = queue.Queue()
        self._result_queues: dict[str, queue.Queue] = {}
        self._running = False
        self.server: Optional[socket.socket] = None
        self.port: Optional[int] = None
        self._server_thread: Optional[threading.Thread] = None
        self._timer: Optional[QTimer] = None
        self._child_pids: set[int] = set()  # track subprocess children for abort
        self._cancel_event = threading.Event()  # cooperative cancellation (user abort)
        # Lazily generated per-instance secret for socket auth (see auth_token).
        self._auth_token: Optional[str] = None

    @property
    def auth_token(self) -> str:
        """Per-instance random secret for socket auth (lazily generated).

        Exposed on the executor so the legitimate runner can retrieve it via
        the same out-of-band channel used for the port number.
        """
        if self._auth_token is None:
            self._auth_token = secrets.token_urlsafe(32)
        return self._auth_token

    @auth_token.setter
    def auth_token(self, value: str) -> None:
        self._auth_token = value

    def start_socket_server(self):
        """Start TCP socket server via SocketServer (canonical handler).

        The SocketServer shares this executor's auth_token and drives the
        same queue priority system (_priority_queue / _normal_queue).
        """
        from aery_plugin.executor_socket import SocketServer

        self._write_run_start_marker()

        # Reuse this executor's auth_token so client and server share the
        # same secret without additional coordination.
        self._socket_server = SocketServer(self, auth_token=self.auth_token)
        self._socket_server.start()

        # Keep local references for shutdown / port access
        self.port = self._socket_server.port
        self.server = self._socket_server.server
        self._server_thread = self._socket_server._server_thread

        self._timer = QTimer()
        self._timer.timeout.connect(self._process_queue)
        self._timer.start(100)  # 100ms — less CPU waste when idle

    def _process_queue(self):
        from qgis.core import QgsProject
        processed = 0

        # Let Qt repaint/process signals before we execute — keeps the UI
        # responsive between queued runs (hybrid responsiveness).
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
        except Exception:
            pass

        # Pull a queued item from either source, preferring priority (O(1) deque.popleft)
        def _dequeue():
            if self._priority_queue:
                return self._priority_queue.popleft()
            return self._normal_queue.get_nowait()

        try:
            # Drain priority first (interactive / blocking requests), then normal queue up to 10
            while processed < 10:
                req_id, code, result_queue, metadata = _dequeue()
                processed += 1
                response: dict[str, Any]
                project_dir = os.path.expanduser("~")
                try:
                    project_path = QgsProject.instance().fileName()
                    project_dir = os.path.dirname(project_path) if project_path else os.path.expanduser("~")

                    if code == "__get_project_context__":
                        ctx = self._get_project_context()
                        response = {"id": req_id, "success": True, "result": ctx}
                        try:
                            from aery_plugin.graph_engine import record_layer, build_tool_capability_graph
                            pdir = ctx.get("project_dir", os.path.expanduser("~"))
                            build_tool_capability_graph(pdir)
                            for lyr in ctx.get("layers", []):
                                record_layer(pdir, lyr["name"], lyr.get("type",""), lyr.get("crs",""))
                        except Exception:
                            pass
                    elif code == "__capture_canvas__":
                        err_msg = ""
                        try:
                            b64 = self._capture_canvas()
                        except Exception as e:
                            b64 = ""
                            err_msg = f" (Exception: {str(e)})"
                        PNG_PREFIX = "iVBORw0KGgo"
                        if not b64 or len(b64.strip()) < 16:
                            response = {
                                "id": req_id, "success": False,
                                "error": f"Canvas capture returned empty image data. Canvas may be uninitialised.{err_msg}",
                            }
                        elif not b64.strip().startswith(PNG_PREFIX):
                            response = {
                                "id": req_id, "success": True,
                                "result": f"[non-image base64, {len(b64)} chars]",
                            }
                        else:
                            # Return as data URL so LLM can see it as image (multimodal) not confusing raw base64 text
                            response = {"id": req_id, "success": True, "result": f"data:image/png;base64,{b64}"}
                    elif code.startswith("__tool__:"):
                        # Typed tool dispatch (GeoLibre-style main-thread tools).
                        # Format: __tool__:<name>:<json params>
                        try:
                            _rest = code[len("__tool__:"):]
                            _name, _params_json = _rest.split(":", 1)
                            _params = json.loads(_params_json)
                            from aery_plugin.tools_new import create_tools
                            _tool_map = {t.name: t for t in create_tools()}
                            _tool = _tool_map.get(_name)
                            if _tool is None:
                                response = {"id": req_id, "success": False, "error": f"Unknown typed tool: {_name}"}
                            else:
                                _result = _tool.handler(_params, None)
                                response = {"id": req_id, "success": True, "result": _result}
                        except Exception as _te:
                            import traceback as _tb2
                            response = {
                                "id": req_id, "success": False,
                                "error": f"Typed tool failed: {_te}\n{_tb2.format_exc()}",
                            }
                    elif code == "__ask_user__":
                        # Forward run_id so the answer can be tied back to the triggering turn
                        qp = {**metadata.get("params", {}), "run_id": metadata.get("run_id")}
                        response = _process_question(req_id, result_queue, qp)
                    else:
                        risks = self.classify_code_risk(code)
                        # Warn about output file conflicts
                        conflicts = self._detect_output_conflicts(code, project_dir)
                        if conflicts:
                            risks.append({"category": "output_conflict", "message": f"Will overwrite: {', '.join(conflicts)}"})
                        g = _build_globals()
                        # Patch subprocess.Popen to track child PIDs in sys.modules
                        # so that exec()-invoked code that does `import subprocess` also gets
                        # the patched Popen.
                        import sys as _sys_mod
                        import subprocess as _sub_mod
                        executor_self = self
                        _orig_popen = _sub_mod.Popen
                        class _TrackedPopen(_orig_popen):
                            def __init__(self, *a, **kw):
                                super().__init__(*a, **kw)
                                executor_self._child_pids.add(self.pid)
                            def wait(self, *a, **kw):
                                r = super().wait(*a, **kw)
                                executor_self._child_pids.discard(self.pid)
                                return r
                        _sub_mod.Popen = _TrackedPopen
                        _sys_mod.modules["subprocess"] = _sub_mod
                        g["subprocess"] = _sub_mod
                        # Patch processing.run → QGIS native async (QgsProcessingAlgRunnerTask
                        # in the thread pool + event pumping) so heavy algorithms don't
                        # freeze the UI or block the event loop. Restored in the finally.
                        _orig_processing_run = None
                        if g.get("processing") is not None:
                            _orig_processing_run = g["processing"].run
                            g["processing"].run = lambda alg, params, fb=None, ctx=None: self._run_processing_async(
                                alg, params, fb, ctx, req_id=req_id, result_queue=result_queue
                            )
                        try:
                            # DEFENSIVE: Clear any stale canvas background on GUI thread before executing tool code
                            # This prevents "magic static image" from previous runs persisting
                            # Must run on GUI thread like _capture_canvas does
                            try:
                                canvas = self.iface.mapCanvas()
                                def _clear_bg():
                                    from PyQt6.QtGui import QBrush, QColor
                                    canvas.setBackgroundBrush(QBrush(QColor(255, 255, 255)))
                                    canvas.setAutoFillBackground(True)
                                _clear_bg()
                            except Exception:
                                pass
                            # Sanitize code to block canvas background manipulation
                            _sanitize_code(code)
                            local_vars: dict[str, Any] = {
                                "iface": self.iface,
                                "project_dir": project_dir,
                                "result": None,
                            }
                            # stdout capture for progress reporting & result fallback
                            _captured_output = []
                            def _on_stdout_line(line):
                                _captured_output.append(line)
                                result_queue.put({"type": "progress", "message": line, "progress": -1})

                            _orig_stdout = _sys_mod.stdout
                            _tee = _ProgressStdout(
                                _orig_stdout,
                                _on_stdout_line,
                                threading.current_thread(),
                            )
                            _sys_mod.stdout = _tee
                            try:
                                exec(code, g, local_vars)
                            finally:
                                try:
                                    _tee.flush()
                                except Exception:
                                    pass
                                _sys_mod.stdout = _orig_stdout
                        finally:
                            _sub_mod.Popen = _orig_popen
                            _sys_mod.modules["subprocess"] = _sub_mod
                            if _orig_processing_run is not None:
                                g["processing"].run = _orig_processing_run
                        # Auto-refresh canvas after any code execution
                        try:
                            if self.iface:
                                self.iface.mapCanvas().refresh()
                        except Exception:
                            pass
                        # If script produced no explicit 'result = ...', fallback to captured stdout
                        final_res = local_vars.get("result")
                        if final_res is None and _captured_output:
                            final_res = "\n".join(_captured_output).strip()

                        response = {
                            "id": req_id,
                            "success": True,
                            "result": self._safe_json_result(final_res),
                            "risks": risks,
                        }
                    result_queue.put(response)
                except Exception as e:
                    tb = traceback.format_exc()
                    response = {
                        "id": req_id,
                        "success": False,
                        "error": str(e),
                        "traceback": tb,
                    }
                    result_queue.put(response)
                finally:
                    self._write_audit_entry(project_dir, req_id, code, response, metadata)
                    self._result_queues.pop(req_id, None)
                    self._record_graph_hooks(project_dir, code, response, metadata)
        except queue.Empty:
            pass

    def _run_processing_async(self, algorithm, parameters, feedback=None, context=None, req_id=None, result_queue=None):
        """Run a processing algorithm via QgsProcessingAlgRunnerTask in the
        background thread pool while pumping the Qt event loop so the UI
        stays responsive. Falls back to synchronous processing.run() if the
        task infrastructure is unavailable.

        The QTimer is stopped during the wait to prevent re-entering
        _process_queue while we're processing events.
        """
        import time as _time
        from qgis.core import (
            QgsApplication, QgsProcessingAlgRunnerTask,
            QgsProcessingContext, QgsProcessingFeedback,
            QgsProcessingException,
        )
        from PyQt6.QtWidgets import QApplication
        import processing as _processing

        alg = QgsApplication.processingRegistry().algorithmById(algorithm)
        if alg is None:
            return _processing.run(algorithm, parameters, feedback, context)

        ctx = context or QgsProcessingContext()
        fb = feedback or QgsProcessingFeedback()

        result_holder = []
        progress_start = _time.monotonic()
        last_progress = -1
        last_stage = ""

        def _on_finished(successful, results):
            result_holder.append((successful, dict(results) if results else {}))

        def _on_progress(progress):
            nonlocal last_progress, last_stage
            if result_queue and req_id:
                now = _time.monotonic()
                elapsed = now - progress_start
                # Estimate ETA: if we're at progress% after elapsed, 100% takes elapsed*100/progress
                eta = None
                if progress > 0:
                    eta = elapsed * 100.0 / progress - elapsed
                # Get stage name from feedback if available
                stage = ""
                try:
                    stage = fb.progressText() or ""
                except Exception:
                    pass
                if progress != last_progress or stage != last_stage:
                    result_queue.put({
                        "id": req_id,
                        "type": "progress",
                        "progress": progress,
                        "algorithm": algorithm,
                        "stage": stage,
                        "elapsed_sec": round(elapsed, 1),
                        "eta_sec": round(eta, 1) if eta is not None else None,
                    })
                    last_progress = progress
                    last_stage = stage

        task = QgsProcessingAlgRunnerTask(alg, parameters, ctx, fb)
        task.executed.connect(_on_finished)
        task.progressChanged.connect(_on_progress)

        was_running = self._timer is not None and self._timer.isActive()
        if was_running:
            self._timer.stop()
        try:
            QgsApplication.taskManager().addTask(task)
            deadline = _time.monotonic() + 300
            while not result_holder and _time.monotonic() < deadline:
                QApplication.processEvents()
                if self._cancel_requested():
                    fb.cancel()  # Use QgsProcessingFeedback's native cancel
                if task.isCanceled():
                    break
                # Sleep between polls — the algorithm runs in QGIS's thread pool
                # so sleeping here doesn't slow it down, it just prevents us
                # from pegging a CPU core with a busy-spin.
                _time.sleep(0.05)
            if not result_holder:
                raise QgsProcessingException(
                    f"Algorithm '{algorithm}' timed out or was cancelled"
                )
            successful, results = result_holder[0]
            if not successful:
                raise QgsProcessingException(f"Algorithm '{algorithm}' failed")
            return results
        finally:
            if was_running and self._timer is not None:
                self._timer.start(100)


    def _capture_canvas(self) -> str:
        """Capture the QGIS map canvas as a base64 PNG string."""
        from aery_plugin.executor_canvas import CanvasCapture
        cap = CanvasCapture(self.iface)
        return cap.capture()

    @staticmethod
    def classify_code_risk(code: str) -> list[dict[str, str]]:
        """Return risk categories -- only flag genuinely dangerous operations."""
        checks = [
            (
                "destructive_project_change",
                ("removeMapLayer", "removeAllMapLayers", "deleteFeatures", "deleteAttribute"),
                "Code may remove layers, features, or attributes.",
            ),
            (
                "filesystem_delete",
                ("os.remove(", "os.unlink(", "shutil.rmtree(", ".unlink()"),
                "Code may delete files from disk.",
            ),
            (
                "shell_execution",
                ("os.system(", "shell=True"),
                "Code may execute shell commands on the host.",
            ),
        ]
        risks = []
        for category, needles, message in checks:
            if any(needle in code for needle in needles):
                risks.append({"category": category, "message": message})
        return risks

    @staticmethod
    def _summarize_result(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            s = value.strip()
            if s.startswith("iVBORw0KGgo") and len(s) > 256:
                return f"[image/png base64, {len(s)} chars]"
            return s[:400]
        if isinstance(value, (int, float, bool)):
            return str(value)[:400]
        try:
            return json.dumps(value, ensure_ascii=False)[:400]
        except TypeError:
            return str(value)[:400]

    @staticmethod
    def _safe_json_result(value: Any) -> Any:
        """Coerce value to a JSON-serializable form.

        Called immediately before json.dumps to ensure bulky objects
        do not cause memory issues.
        """
        if value is None:
            return None
        # pathlib.Path - convert to string before json.dumps
        try:
            import pathlib as _pl
        except ImportError:
            _pl = None  # type: ignore[assignment]
        if _pl is not None and isinstance(value, _pl.Path):
            return str(value)
        str_rep: Any
        if isinstance(value, str):
            str_rep = value
        else:
            try:
                str_rep = json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                str_rep = str(value)
        if not isinstance(str_rep, str):
            return str(str_rep)
        s = str_rep
        # Guard: empty/invalid base64 -- collapse before it can reach the runner
        # as {type:"image",data:""} which produces "empty base64-encoded bytes"
        # in the Anthropic/insertBlob API call
        if s.startswith("iVBORw0KGgo") and len(s) <= 16:
            return {"_aery_summary": "[empty/invalid base64 image - collapsed]"}
        if s.startswith("iVBORw0KGgo") and len(s) > 600_000:
            return {"_aery_summary": f"[base64 image, {len(s)} chars - send to canvas instead]"}
        if len(s) > 1_000_000:
            return {"_aery_summary": f"[large result, {len(s)} chars - too big to serialise]"}
        return value  # small enough; let json.dumps handle it normally

    def _get_audit_dir(self, project_dir: str) -> str:
        return self.audit_dir or os.path.join(project_dir, ".aery")

    def _append_audit_record(self, audit_dir: str, entry: dict[str, Any]) -> None:
        os.makedirs(audit_dir, exist_ok=True)
        with open(os.path.join(audit_dir, "operations.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _write_run_start_marker(self) -> None:
        try:
            project_dir = os.path.expanduser("~")
            try:
                from qgis.core import QgsProject
                project_path = QgsProject.instance().fileName() or ""
                if project_path:
                    project_dir = os.path.dirname(project_path)
            except Exception:
                project_path = ""
            self._append_audit_record(self._get_audit_dir(project_dir), {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "run_start",
                "run_id": self.run_id,
                "source": "plugin",
                "project_dir": project_dir,
            })
        except Exception:
            pass

    def _write_audit_entry(self, project_dir, req_id, code, response, metadata=None):
        try:
            audit_dir = self._get_audit_dir(project_dir)
            metadata = metadata or {}
            try:
                from qgis.core import QgsProject
                project_path = QgsProject.instance().fileName() or ""
            except Exception:
                project_path = ""
            duration_ms = int((time.perf_counter() - metadata.get("started_at", time.perf_counter())) * 1000)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": req_id,
                "tool_name": metadata.get("tool_name") or "run_code",
                "run_id": metadata.get("run_id", self.run_id),
                "source": metadata.get("source", "plugin"),
                "phase": "end",
                "success": bool(response.get("success")),
                "duration_ms": duration_ms,
                "project_path": project_path,
                "project_dir": project_dir,
                "code": code if code != "__capture_canvas__" else "[canvas capture]",
                "result_summary": self._summarize_result(response.get("result")),
                "risks": response.get("risks", []),
            }
            if not response.get("success"):
                entry["error"] = response.get("error", "")
                entry["traceback"] = response.get("traceback", "")
            self._append_audit_record(audit_dir, entry)
        except Exception:
            pass

    def _get_project_context(self) -> dict[str, Any]:
        import sys
        from qgis.core import QgsProject

        project = QgsProject.instance()
        layers = []

        for layer in project.mapLayers().values():
            info: dict[str, Any] = {
                "id": layer.id(),
                "name": layer.name(),
                "type": layer.type().name,
                "crs": layer.crs().authid() if layer.crs() else None,
                "visible": project.layerTreeRoot().findLayer(layer.id()).isVisible()
                    if project.layerTreeRoot().findLayer(layer.id()) else True,
            }
            if hasattr(layer, "featureCount"):
                info["feature_count"] = layer.featureCount()
            if hasattr(layer, "fields"):
                info["fields"] = [
                    {"name": f.name(), "type": f.typeName()}
                    for f in layer.fields()
                ]
            if hasattr(layer, "geometryType"):
                try:
                    info["geometry_type"] = layer.geometryType().name
                except Exception:
                    pass
            try:
                ext = layer.extent()
                if ext and not ext.isEmpty():
                    info["extent"] = {
                        "xmin": round(ext.xMinimum(), 6),
                        "ymin": round(ext.yMinimum(), 6),
                        "xmax": round(ext.xMaximum(), 6),
                        "ymax": round(ext.yMaximum(), 6),
                    }
            except Exception:
                pass
            # Raster-specific
            if hasattr(layer, "bandCount"):
                info["band_count"] = layer.bandCount()
                try:
                    info["pixel_size"] = {
                        "x": layer.rasterUnitsPerPixelX(),
                        "y": layer.rasterUnitsPerPixelY(),
                    }
                except Exception:
                    pass
            layers.append(info)

        active_layer = self.iface.activeLayer() if self.iface else None
        selection_count = 0
        if active_layer and hasattr(active_layer, "selectedFeatureIds"):
            selection_count = len(active_layer.selectedFeatureIds())

        project_path = project.fileName()
        project_dir = os.path.dirname(project_path) if project_path else os.path.expanduser("~")

        spatial: dict[str, Any] = {}
        if self.iface:
            try:
                canvas = self.iface.mapCanvas()
                ext = canvas.extent()
                dest_crs = canvas.mapSettings().destinationCrs()
                center = canvas.center()
                spatial = {
                    "canvas_crs": dest_crs.authid() if dest_crs else None,
                    "canvas_scale": float(canvas.scale()),
                    "canvas_center": {"x": round(float(center.x()), 6), "y": round(float(center.y()), 6)},
                    "canvas_extent": {
                        "xmin": round(float(ext.xMinimum()), 6),
                        "ymin": round(float(ext.yMinimum()), 6),
                        "xmax": round(float(ext.xMaximum()), 6),
                        "ymax": round(float(ext.yMaximum()), 6),
                    },
                }
                try:
                    from qgis.core import QgsCoordinateTransform, QgsCoordinateReferenceSystem
                    t = QgsCoordinateTransform(
                        dest_crs,
                        QgsCoordinateReferenceSystem("EPSG:4326"),
                        project,
                    )
                    ll = t.transformBoundingBox(ext)
                    spatial["canvas_extent_wgs84"] = {
                        "lat_min": round(float(ll.yMinimum()), 6),
                        "lon_min": round(float(ll.xMinimum()), 6),
                        "lat_max": round(float(ll.yMaximum()), 6),
                        "lon_max": round(float(ll.xMaximum()), 6),
                    }
                except Exception:
                    pass
            except Exception:
                pass

        # Available processing providers
        processing_providers: list[str] = []
        try:
            import processing
            from qgis.core import QgsApplication
            for p in QgsApplication.processingRegistry().providers():
                processing_providers.append(p.id())
        except Exception:
            pass

        return {
            "layers": layers,
            "layer_count": len(layers),
            "active_layer": active_layer.name() if active_layer else None,
            "selection_count": selection_count,
            "project_crs": project.crs().authid() if project.crs() else None,
            "project_dir": project_dir,
            "project_path": project_path or "",
            "home_dir": os.path.expanduser("~"),
            "qgis_python": sys.executable,
            "qgis_prefix_path": os.environ.get("QGIS_PREFIX_PATH", ""),
            "processing_providers": processing_providers,
            "spatial": spatial,
        }

    # kept for backwards compat — now just returns cached globals
    def _get_globals(self) -> dict[str, Any]:
        return _build_globals()

    @staticmethod
    def _detect_output_conflicts(code: str, project_dir: str) -> list[str]:
        """Find output file paths in code that already exist on disk."""
        import re
        conflicts = []
        for m in re.finditer(r'["\']([^"\']+\.(?:tif|tiff|gpkg|shp|geojson|csv|json|pdf|png))["\']', code):
            path = m.group(1).replace("{project_dir}", project_dir)
            if os.path.exists(path):
                conflicts.append(os.path.basename(path))
        return conflicts

    def abort_children(self) -> None:
        """Kill all tracked child subprocesses (e.g. running SNAP/GDAL commands)."""
        import signal
        for pid in list(self._child_pids):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        self._child_pids.clear()

    def _record_graph_hooks(self, project_dir: str, code: str, response: dict, metadata: dict) -> None:
        """Post-execution graph bookkeeping. Failures are logged but never reraise."""
        try:
            from aery_plugin.graph_engine import (
                record_code_execution,
                auto_detect_spatial_relationships,
                collect_layer_data_for_spatial,
                prune_graph,
            )
            import re

            output_files = re.findall(
                r'["\']([^"\']+\.(?:tif|tiff|gpkg|shp|geojson|csv|pdf|png))["\']', code
            )
            record_code_execution(
                project_dir=project_dir,
                tool_name=metadata.get("tool_name", "run_qgis_code"),
                code=code if code != "__capture_canvas__" else "",
                result_summary=self._summarize_result(response.get("result")),
                input_layers=[],
                output_files=output_files,
                success=bool(response.get("success")),
            )
            if response.get("success") and output_files:
                # Collect layer data on main thread (QGIS API is not thread-safe)
                layer_data = collect_layer_data_for_spatial()
                threading.Thread(
                    target=auto_detect_spatial_relationships,
                    args=(project_dir, layer_data),
                    daemon=True,
                ).start()
            prune_graph(project_dir)

        except Exception as exc:
            try:
                from qgis.core import QgsMessageLog, Qgis
                QgsMessageLog.logMessage(
                    f"[Aery] graph hook failed: {exc}",
                    "Aery",
                    Qgis.MessageLevel.Warning,
                )
            except ImportError:
                pass  # not in QGIS context (tests)

    def execute(self, code: str, timeout: int = 300, on_progress=None, priority: Optional[bool] = None) -> dict[str, Any]:
        """Execute code on the main GUI thread via the QTimer queue.
        This ensures Qt widget access (canvas, layers) happens on the correct thread.
        Interactive tools (capture_canvas, zoom_to_place, get_project_context) are
        automatically prioritized in _priority_queue for zero-lag responsiveness.
        """
        if priority is None:
            priority = (
                code in ("__capture_canvas__", "__get_project_context__")
                or code.startswith("__tool__:capture_canvas")
                or code.startswith("__tool__:zoom_to_")
                or code.startswith("__tool__:set_layer_visibility")
            )
        result_queue: queue.Queue = queue.Queue()
        entry = ("direct", code, result_queue, {
            "method": "run_code",
            "tool_name": "run_qgis_code",
            "source": "plugin",
            "priority": priority,
            "started_at": time.perf_counter(),
        })
        if priority:
            self._priority_queue.append(entry)
        else:
            self._normal_queue.put(entry)
        self._cancel_event.clear()
        deadline_exec = time.monotonic() + timeout
        while time.monotonic() < deadline_exec:
            try:
                item = result_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if item.get("type") == "progress":
                if on_progress is not None:
                    try:
                        on_progress(item)
                    except Exception:
                        pass  # progress listeners are best-effort
                continue
            return item
        raise TimeoutError(f"execute() timed out after {timeout}s waiting for main-thread QTimer")

    def cancel(self) -> None:
        """Request cooperative cancellation of the current in-flight execution.

        Thread-safe: callable from any thread (e.g. agent.cancel() from the
        GUI). Sets a flag checked by _run_processing_async, which calls
        QgsProcessingFeedback.cancel() on the main thread. Native QGIS
        algorithms check feedback.isCanceled() cooperatively.
        """
        self._cancel_event.set()

    def _cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def shutdown(self):
        self._running = False
        self._priority_queue.clear()
        import aery_plugin.qgis_executor as _qe_mod
        _qe_mod._pending_questions.clear()
        self._result_queues.clear()
        if self._timer:
            self._timer.stop()
        # Delegate socket shutdown to SocketServer
        if getattr(self, "_socket_server", None):
            self._socket_server.shutdown()
            self._socket_server = None
        # Clear local refs
        self.port = None
        self.server = None
        self._server_thread = None
