"""Thread-safe QGIS Python code execution via local TCP socket + main-thread queue."""

import base64
import json
import os
import queue
import socket
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from PyQt6.QtCore import QObject, QTimer

# Cached globals — built once on first execution, reused for all subsequent calls
_GLOBALS_CACHE: Optional[dict[str, Any]] = None


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

    # ── QGIS Core — every useful class ──
    try:
        from qgis.core import (
            Qgis,
            QgsApplication,
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsCoordinateTransformContext,
            QgsDataSourceUri,
            QgsDistanceArea,
            QgsExpression,
            QgsExpressionContext,
            QgsExpressionContextUtils,
            QgsFeature,
            QgsFeatureRequest,
            QgsField,
            QgsFields,
            QgsGeometry,
            QgsLayerTreeGroup,
            QgsLayerTreeLayer,
            QgsMapLayer,
            QgsMapLayerType,
            QgsMapSettings,
            QgsMapThemeCollection,
            QgsMarkerSymbol,
            QgsMessageLog,
            QgsPalLayerSettings,
            QgsPoint,
            QgsPointCloudLayer,
            QgsPointXY,
            QgsProcessingFeedback,
            QgsProject,
            QgsRasterBandStats,
            QgsRasterLayer,
            QgsRectangle,
            QgsRendererRange,
            QgsSingleSymbolRenderer,
            QgsSpatialIndex,
            QgsSymbol,
            QgsSymbolLayer,
            QgsTextFormat,
            QgsVectorDataProvider,
            QgsVectorFileWriter,
            QgsVectorLayer,
            QgsVectorLayerUtils,
            QgsWkbTypes,
            # Layout / print classes
            QgsLayout,
            QgsLayoutItemLabel,
            QgsLayoutItemLegend,
            QgsLayoutItemMap,
            QgsLayoutItemNorthArrow,
            QgsLayoutItemPage,
            QgsLayoutItemPicture,
            QgsLayoutItemScaleBar,
            QgsLayoutMeasurement,
            QgsLayoutObject,
            QgsLayoutPoint,
            QgsLayoutSize,
            QgsLayoutUnit,
            QgsLayoutItem,
            QgsLayoutUnits,
            QgsPageLayout,
            QgsPrintLayout,
            QgsLayoutExporter,
        )
        # Pseudocolor renderer (needed for NDVI/SAR display)
        try:
            from qgis.core import (
                QgsColorRampShader,
                QgsRasterShader,
                QgsSingleBandPseudoColorRenderer,
                QgsSingleBandGrayRenderer,
                QgsGraduatedSymbolRenderer,
                QgsClassificationQuantile,
                QgsVectorLayerSimpleLabeling,
            )
            g.update({
                "QgsColorRampShader": QgsColorRampShader,
                "QgsRasterShader": QgsRasterShader,
                "QgsSingleBandPseudoColorRenderer": QgsSingleBandPseudoColorRenderer,
                "QgsSingleBandGrayRenderer": QgsSingleBandGrayRenderer,
                "QgsGraduatedSymbolRenderer": QgsGraduatedSymbolRenderer,
                "QgsClassificationQuantile": QgsClassificationQuantile,
                "QgsVectorLayerSimpleLabeling": QgsVectorLayerSimpleLabeling,
            })
        except ImportError:
            pass
        g.update({
            "Qgis": Qgis,
            "QgsApplication": QgsApplication,
            "QgsCoordinateReferenceSystem": QgsCoordinateReferenceSystem,
            "QgsCoordinateTransform": QgsCoordinateTransform,
            "QgsCoordinateTransformContext": QgsCoordinateTransformContext,
            "QgsDataSourceUri": QgsDataSourceUri,
            "QgsDistanceArea": QgsDistanceArea,
            "QgsExpression": QgsExpression,
            "QgsExpressionContext": QgsExpressionContext,
            "QgsExpressionContextUtils": QgsExpressionContextUtils,
            "QgsFeature": QgsFeature,
            "QgsFeatureRequest": QgsFeatureRequest,
            "QgsField": QgsField,
            "QgsFields": QgsFields,
            "QgsGeometry": QgsGeometry,
            "QgsLayerTreeGroup": QgsLayerTreeGroup,
            "QgsLayerTreeLayer": QgsLayerTreeLayer,
            "QgsMapLayer": QgsMapLayer,
            "QgsMapLayerType": QgsMapLayerType,
            "QgsMapSettings": QgsMapSettings,
            "QgsMapThemeCollection": QgsMapThemeCollection,
            "QgsMarkerSymbol": QgsMarkerSymbol,
            "QgsMessageLog": QgsMessageLog,
            "QgsPalLayerSettings": QgsPalLayerSettings,
            "QgsPoint": QgsPoint,
            "QgsPointCloudLayer": QgsPointCloudLayer,
            "QgsPointXY": QgsPointXY,
            "QgsProcessingFeedback": QgsProcessingFeedback,
            "QgsProject": QgsProject,
            "QgsRasterBandStats": QgsRasterBandStats,
            "QgsRasterLayer": QgsRasterLayer,
            "QgsRectangle": QgsRectangle,
            "QgsRendererRange": QgsRendererRange,
            "QgsSingleSymbolRenderer": QgsSingleSymbolRenderer,
            "QgsSpatialIndex": QgsSpatialIndex,
            "QgsSymbol": QgsSymbol,
            "QgsTextFormat": QgsTextFormat,
            "QgsVectorDataProvider": QgsVectorDataProvider,
            "QgsVectorFileWriter": QgsVectorFileWriter,
            "QgsVectorLayer": QgsVectorLayer,
            "QgsVectorLayerUtils": QgsVectorLayerUtils,
            "QgsWkbTypes": QgsWkbTypes,
            # Layout / print — available in QGIS 3.28+ / QGIS 4
            "QgsLayout": QgsLayout,
            "QgsLayoutExporter": QgsLayoutExporter,
            "QgsLayoutItemLabel": QgsLayoutItemLabel,
            "QgsLayoutItemLegend": QgsLayoutItemLegend,
            "QgsLayoutItemMap": QgsLayoutItemMap,
            "QgsLayoutItemNorthArrow": QgsLayoutItemNorthArrow,
            "QgsLayoutItemPage": QgsLayoutItemPage,
            "QgsLayoutItemPicture": QgsLayoutItemPicture,
            "QgsLayoutItemScaleBar": QgsLayoutItemScaleBar,
            "QgsLayoutMeasurement": QgsLayoutMeasurement,
            "QgsLayoutObject": QgsLayoutObject,
            "QgsLayoutPoint": QgsLayoutPoint,
            "QgsLayoutSize": QgsLayoutSize,
            "QgsLayoutUnit": QgsLayoutUnit,
            "QgsPrintLayout": QgsPrintLayout,
            "QgsPageLayout": QgsPageLayout,
        })
    except ImportError:
        pass

    # ── QGIS GUI ──
    try:
        from qgis.gui import (
            QgsMapCanvas,
            QgsMapToolEmitPoint,
            QgsRubberBand,
            QgsVertexMarker,
        )
        g.update({
            "QgsMapCanvas": QgsMapCanvas,
            "QgsMapToolEmitPoint": QgsMapToolEmitPoint,
            "QgsRubberBand": QgsRubberBand,
            "QgsVertexMarker": QgsVertexMarker,
        })
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

    # Deferred lookup — function may be defined after this call during import;
    # the real function is resolved when _build_globals() is called at runtime.
    try:
        g["_build_leaflet_html"] = _build_leaflet_html  # type: ignore[name-defined]
    except NameError:
        def _build_leaflet_html_stub(*a, **kw):  # type: ignore[misc]
            raise RuntimeError("_build_leaflet_html not yet available — call after full import")
        g["_build_leaflet_html"] = _build_leaflet_html_stub

    _GLOBALS_CACHE = g
    return g


def _build_leaflet_html(layer_files, basemap="osm", include_search=False, title=None, bbox=None):
    """Build a self-contained Leaflet.js HTML string from layer file references.

    Args:
        layer_files: list of {name, file, count} dicts (relative paths to data files)
        basemap: 'osm', 'satellite', 'topo', 'stamen_toner', or 'none'
        include_search: add a geocoding search box at top-left
        title: page <title> (default: "QGIS Web Map")
        bbox: QgsRectangle or None (falls back to [0, 0] center)

    Returns:
        Complete HTML string with embedded Leaflet map.
    """
    # Duck-type bbox check — avoids requiring QGIS imports outside exec()
    _is_rect = hasattr(bbox, "center") and hasattr(bbox, "yMinimum") and hasattr(bbox, "xMinimum")
    if _is_rect:
        center = [bbox.center().y(), bbox.center().x()]
        bounds = [[bbox.yMinimum(), bbox.xMinimum()], [bbox.yMaximum(), bbox.xMaximum()]]
    else:
        center, bounds = [0, 0], None

    basemap_urls = {
        "osm": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "topo": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "stamen_toner": "https://stamen-tiles-{s}.a.ssl.fastly.net/toner/{z}/{x}/{y}.png",
        "none": None,
    }
    bm_url = basemap_urls.get(basemap)
    bm_attr = (
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        if basemap == "osm"
        else '© Esri'
        if basemap == "satellite"
        else ""
    )

    layer_js = []
    for lf in layer_files:
        f = lf.get("file", "")
        if f.endswith(".geojson"):
            layer_js.append(f'fetch("{f}").then(r=>r.json()).then(data=>L.geoJSON(data,{{}}).addTo(map))')
        elif f.endswith(".tif") or f.endswith(".tiff"):
            layer_js.append(f'L.imageOverlay("{f}", bounds).addTo(map)')

    search_block = ""
    if include_search:
        search_block = (
            '<div id="search" style="position:absolute;top:10px;left:60px;z-index:1000;">'
            '<input id="q" placeholder="Search location…" style="padding:4px 8px;width:200px;">'
            '<button onclick="doSearch()">Go</button></div>\n'
            '<script>\nfunction doSearch(){'
            'var q=document.getElementById("q").value;'
            'fetch("https://nominatim.openstreetmap.org/search?format=json&q="+encodeURIComponent(q))'
            '.then(r=>r.json()).then(d=>{if(d[0]){'
            'map.setView([d[0].lat,d[0].lon],12);'
            'L.marker([d[0].lat,d[0].lon]).addTo(map);}})}\n</script>'
        )

    bounds_js = f"var bounds={json.dumps(bounds)};" if bounds else ""
    center_js = f"var center={json.dumps(center)};"

    tile_js = f'L.tileLayer("{bm_url}", {{attribution: "{bm_attr}"}}).addTo(map);' if bm_url else ""
    layer_js_str = "\n    ".join(layer_js)

    return (
        f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title or "QGIS Web Map"}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>#map{{height:600px;}}</style>
</head><body>
<h1>{title or "QGIS Web Map"}</h1>
{search_block}
<div id="map"></div>
<script>
{center_js}
{bounds_js}
var map = L.map('map');
{"map.fitBounds(bounds);" if bounds else "map.setView(center, 8);"}
{tile_js}
{layer_js_str}
</script></body></html>"""
    )


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
        self._request_queue: queue.Queue = queue.Queue()
        self._result_queues: dict[str, queue.Queue] = {}
        self._running = False
        self.server: Optional[socket.socket] = None
        self.port: Optional[int] = None
        self._server_thread: Optional[threading.Thread] = None
        self._timer: Optional[QTimer] = None
        self._child_pids: set[int] = set()  # track subprocess children for abort

    def start_socket_server(self):
        """Start TCP socket server in background thread + main-thread QTimer."""
        self._running = True
        self._write_run_start_marker()

        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.server.listen(5)
        self.server.settimeout(1.0)

        self._server_thread = threading.Thread(target=self._serve, daemon=True)
        self._server_thread.start()

        self._timer = QTimer()
        self._timer.timeout.connect(self._process_queue)
        self._timer.start(100)  # 100ms — less CPU waste when idle

    def _serve(self):
        while self._running:
            try:
                conn, _ = self.server.accept()
                threading.Thread(target=self._handle_connection, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_connection(self, conn: socket.socket):
        try:
            # Read until newline; hard cap at 1 MB to prevent memory exhaustion
            MAX_BODY = 1_048_576
            data = b""
            conn.settimeout(30.0)
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) >= MAX_BODY:
                    break
                if b"\n" in data:
                    break

            request = json.loads(data.decode().strip())
            req_id = request.get("id")
            method = request.get("method", "run_code")
            code = request.get("code", "")

            result_queue: queue.Queue = queue.Queue()
            self._result_queues[req_id] = result_queue
            metadata = {
                "method": method,
                "tool_name": request.get("tool_name") or method,
                "source": request.get("source", "plugin"),
                "started_at": time.perf_counter(),
                "run_id": request.get("run_id") or self.run_id,
            }

            if method == "get_project_context":
                ctx = self._get_project_context()
                result_queue.put({"id": req_id, "success": True, "result": ctx})
                # Record all layers in graph
                try:
                    from aery_plugin.graph_engine import record_layer, build_tool_capability_graph
                    project_path = ctx.get("project_path", "")
                    pdir = ctx.get("project_dir", os.path.expanduser("~"))
                    build_tool_capability_graph(pdir)
                    for lyr in ctx.get("layers", []):
                        record_layer(pdir, lyr["name"], lyr.get("type",""), lyr.get("crs",""))
                except Exception:
                    pass
            elif method == "capture_canvas":
                self._request_queue.put((req_id, "__capture_canvas__", result_queue, metadata))
            else:
                self._request_queue.put((req_id, code, result_queue, metadata))

            result = result_queue.get(timeout=300)
            conn.sendall((json.dumps(result) + "\n").encode())
        except queue.Empty:
            conn.sendall((json.dumps({"success": False, "error": "Execution timed out after 300s"}) + "\n").encode())
        except Exception as e:
            conn.sendall((json.dumps({"success": False, "error": str(e)}) + "\n").encode())
        finally:
            conn.close()

    def _process_queue(self):
        from qgis.core import QgsProject
        processed = 0
        try:
            while processed < 10:  # max 10 per tick to stay responsive
                req_id, code, result_queue, metadata = self._request_queue.get_nowait()
                processed += 1
                response: dict[str, Any]
                project_dir = os.path.expanduser("~")
                try:
                    project_path = QgsProject.instance().fileName()
                    project_dir = os.path.dirname(project_path) if project_path else os.path.expanduser("~")

                    if code == "__capture_canvas__":
                        response = {"id": req_id, "success": True, "result": self._capture_canvas()}
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
                        try:
                            local_vars: dict[str, Any] = {
                                "iface": self.iface,
                                "project_dir": project_dir,
                                "result": None,
                            }
                            exec(code, g, local_vars)
                        finally:
                            _sub_mod.Popen = _orig_popen
                            _sys_mod.modules["subprocess"] = _sub_mod
                        # Auto-refresh canvas after any code execution
                        try:
                            if self.iface:
                                self.iface.mapCanvas().refresh()
                        except Exception:
                            pass
                        response = {
                            "id": req_id,
                            "success": True,
                            "result": local_vars.get("result"),
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

    def _capture_canvas(self) -> str:
        """Capture the QGIS map canvas as a base64 PNG string."""
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtCore import QSize
        import io
        canvas = self.iface.mapCanvas()
        size = canvas.size()
        img = QImage(QSize(size.width(), size.height()), QImage.Format.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        canvas.render(painter)
        painter.end()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    @staticmethod
    def classify_code_risk(code: str) -> list[dict[str, str]]:
        """Return risk categories — only flag genuinely dangerous operations."""
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
                threading.Thread(
                    target=auto_detect_spatial_relationships,
                    args=(project_dir,),
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

    def execute(self, code: str, timeout: int = 300) -> dict[str, Any]:
        result_queue: queue.Queue = queue.Queue()
        self._request_queue.put(("direct", code, result_queue, {
            "method": "run_code",
            "tool_name": "run_qgis_code",
            "source": "plugin",
            "started_at": time.perf_counter(),
        }))
        self._process_queue()
        return result_queue.get(timeout=timeout)

    def shutdown(self):
        self._running = False
        if self._timer:
            self._timer.stop()
        if self.server:
            try:
                self.server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.server.close()
            except OSError:
                pass
            self.server = None
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=1.0)
