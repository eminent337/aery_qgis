"""GeoLibre-inspired typed tool surface for the Aery QGIS agent.

Unlike the legacy `tools.py` (which routes everything through
`run_qgis_code`), these tools are pure typed handlers that execute directly
on the QGIS main thread via the executor's QTimer queue, or through the
QGIS processing thread pool for long-running algorithms. No raw code
generation — each tool maps a structured JSON schema to a direct PyQGIS
call, mirroring GeoLibre's `createAssistantTools` pattern.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from aery_plugin.logger import logger


# ---------------------------------------------------------------------------
# Tool model
# ---------------------------------------------------------------------------


@dataclass
class Tool:
    """A single typed tool: JSON schema + a handler.

    The handler runs ON the QGIS main thread (via the executor queue) so it
    may touch QgisProject.instance(), mapCanvas(), etc. safely. Long-running
    processing is delegated to QgsProcessingAlgRunnerTask internally; the
    handler returns a JSON-safe dict.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], Any], Any]  # (params, on_progress) -> json-safe
    required: list[str] = field(default_factory=list)
    destructive: bool = False
    examples: list[str] = field(default_factory=list)

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }


# ---------------------------------------------------------------------------
# Generic layer/context helpers (main-thread safe)
# ---------------------------------------------------------------------------


def _resolve_layer(ref: str):
    """Resolve a layer by id, then case-insensitive name. Runs on main thread."""
    from qgis.core import QgsProject

    project = QgsProject.instance()
    if not ref:
        return None
    # By id first
    layer = project.mapLayer(ref)
    if layer:
        return layer
    # By name (case-insensitive)
    ref_lower = ref.lower()
    for l in project.mapLayers().values():
        if l.name().lower() == ref_lower:
            return l
    return None


def _layer_summary(layer) -> dict[str, Any]:
    """Compact JSON-safe description of a layer (no row data)."""
    from qgis.core import QgsVectorLayer

    summary: dict[str, Any] = {
        "id": layer.id(),
        "name": layer.name(),
        "type": layer.type().name,  # e.g. VectorLayer / RasterLayer
        "visible": bool(layer.isVisible()),
        "crs": layer.crs().authid(),
    }
    if isinstance(layer, QgsVectorLayer):
        summary["geometry"] = (
            layer.geometryType().name if hasattr(layer, "geometryType") else "unknown"
        )
        summary["featureCount"] = layer.featureCount()
        try:
            summary["extent"] = [
                layer.extent().xMinimum(),
                layer.extent().yMinimum(),
                layer.extent().xMaximum(),
                layer.extent().yMaximum(),
            ]
        except Exception:
            pass
    return summary


def _project_context() -> dict[str, Any]:
    """Description of the current project: layers, CRS, names only."""
    from qgis.core import QgsProject

    project = QgsProject.instance()
    layers = [_layer_summary(l) for l in project.mapLayers().values()]
    # Keep the schema light for the model: id, name, type, crs, feature count
    return {
        "projectName": project.title() or "",
        "crs": project.crs().authid(),
        "layerCount": len(layers),
        "layers": layers,
    }


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _h_list_layers(params: dict, on_progress=None) -> dict[str, Any]:
    return _project_context()


def _h_get_layer_schema(params: dict, on_progress=None) -> dict[str, Any]:
    from qgis.core import QgsVectorLayer

    layer = _resolve_layer(params["layer"])
    if layer is None:
        return {"error": f"Layer '{params['layer']}' not found in project."}
    if not isinstance(layer, QgsVectorLayer):
        return {"error": f"Layer '{params['layer']}' is not a vector layer."}
    fields = []
    for f in layer.fields():
        fields.append({
            "name": f.name(),
            "type": f.typeName(),
            "alias": f.alias() or "",
        })
    return {
        "id": layer.id(),
        "name": layer.name(),
        "fields": fields,
        "featureCount": layer.featureCount(),
        "extent": [
            layer.extent().xMinimum(),
            layer.extent().yMinimum(),
            layer.extent().xMaximum(),
            layer.extent().yMaximum(),
        ],
        "crs": layer.crs().authid(),
    }


def _h_set_layer_visibility(params: dict, on_progress=None) -> dict[str, Any]:
    from qgis.core import QgsProject

    layer = _resolve_layer(params["layer"])
    if layer is None:
        return {"error": f"Layer '{params['layer']}' not found."}
    tree = QgsProject.instance().layerTreeRoot()
    node = tree.findLayer(layer.id())
    if node is not None:
        node.setItemVisibilityChecked(bool(params["visible"]))
    else:
        layer.setVisible(bool(params["visible"]))
    return {
        "layer": layer.name(),
        "visible": bool(params["visible"]),
    }


def _h_capture_canvas(params: dict, on_progress=None) -> dict[str, Any]:
    """Capture the canvas to a PNG data URL (main thread)."""
    from PyQt6.QtCore import QMetaObject, Qt, QByteArray, QBuffer, QIODevice
    from PyQt6.QtGui import QImage, QColor

    iface = _get_iface()
    if iface is None:
        return {"error": "No QGIS interface (headless run)."}
    canvas = iface.mapCanvas()
    if canvas is None:
        return {"error": "No map canvas available."}
    img = canvas.grab().toImage()
    # Downscale if absurdly large
    max_dim = params.get("max_dim", 1200)
    if max(img.width(), img.height()) > max_dim:
        img = img.scaled(
            max_dim,
            max_dim,
            _qt_keep_aspect(),
            _qt_smooth(),
        )
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    b64 = bytes(data.toBase64()).decode("ascii")
    return {"image": f"data:image/png;base64,{b64}", "width": img.width(), "height": img.height()}


def _qt_keep_aspect():
    from PyQt6.QtCore import Qt
    return Qt.AspectRatioMode.KeepAspectRatio


def _qt_smooth():
    from PyQt6.QtCore import Qt
    return Qt.TransformationMode.SmoothTransformation


def _get_iface():
    import aery_plugin.plugin as plugin_mod
    try:
        return plugin_mod.iface
    except Exception:
        return None


def _h_zoom_to_layer(params: dict, on_progress=None) -> dict[str, Any]:
    iface = _get_iface()
    layer = _resolve_layer(params["layer"])
    if layer is None:
        return {"error": f"Layer '{params['layer']}' not found."}
    if iface is None:
        return {"error": "No QGIS interface."}
    canvas = iface.mapCanvas()
    canvas.setExtent(layer.extent())
    canvas.refresh()
    return {"zoomedTo": layer.name()}


def _h_zoom_to_place(params: dict, on_progress=None) -> dict[str, Any]:
    """Geocode a place name and zoom the canvas to it (Nominatim)."""
    import urllib.parse
    import urllib.request

    iface = _get_iface()
    if iface is None:
        return {"error": "No QGIS interface."}
    place = params.get("place", "").strip()
    if not place:
        return {"error": "'place' is required."}
    url = "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + urllib.parse.quote(place)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Aery-QGIS-Assistant/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        results = json.loads(resp.read().decode())
    if not results:
        return {"error": f"No place found for '{place}'."}
    r = results[0]
    bounds = r.get("boundingbox")
    if not bounds:
        return {"error": f"No bounding box for '{place}'."}
    from qgis.core import QgsRectangle, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
    rect = QgsRectangle(float(bounds[2]), float(bounds[0]), float(bounds[3]), float(bounds[1]))
    src_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    dst_crs = QgsProject.instance().crs()
    if src_crs != dst_crs:
        xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
        rect = xform.transformBoundingBox(rect)
    canvas = iface.mapCanvas()
    canvas.setExtent(rect)
    canvas.refresh()
    return {
        "place": place,
        "displayName": r.get("display_name", place),
        "extent": [rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()],
    }


def _h_run_processing(params: dict, on_progress=None) -> dict[str, Any]:
    """Run a QGIS processing algorithm via QgsProcessingAlgRunnerTask.

    The handler runs on the main thread (executor queue); the heavy work is
    delegated to the QGIS thread pool through the executor's
    `_run_processing_async`, so the UI stays responsive. `on_progress` is
    forwarded as structured progress events.
    """
    algorithm = params["algorithm"]
    raw_params = params.get("parameters", {})
    # Resolve string layer references to actual layer objects
    resolved = {}
    for k, v in raw_params.items():
        if isinstance(v, str):
            layer = _resolve_layer(v)
            resolved[k] = layer if layer is not None else v
        else:
            resolved[k] = v
    # Run through the executor's main-thread async runner
    import aery_plugin.qgis_executor as executor_mod
    from qgis.core import QgsProcessingContext

    ex = executor_mod._current_executor() if hasattr(executor_mod, "_current_executor") else None
    if ex is not None:
        ctx = QgsProcessingContext()
        results = ex._run_processing_async(
            algorithm,
            resolved,
            context=ctx,
            req_id=None,  # no progress callback from here; caller streams events
            result_queue=None,
        )
    else:
        import processing
        results = processing.run(algorithm, resolved)
    # Summarize outputs: layer outputs -> ids, others -> string
    summary = {}
    for k, v in (results or {}).items():
        if hasattr(v, "id"):
            summary[k] = v.id()
        elif hasattr(v, "dataProvider") and hasattr(v, "source"):
            summary[k] = v.source()
        else:
            try:
                summary[k] = str(v)
            except Exception:
                summary[k] = repr(v)
    return {"algorithm": algorithm, "outputs": summary}


def _h_load_basemap(params: dict, on_progress=None) -> dict[str, Any]:
    from qgis.core import QgsProject, QgsRasterLayer

    reference = params.get("basemap", "osm")
    # Forward to the existing resolve_basemap in legacy tools
    from aery_plugin.tools import resolve_basemap

    entry = resolve_basemap(reference)
    if entry is None:
        return {"error": f"Unknown basemap '{reference}'."}
    url = entry["url"]
    name = params.get("name") or entry.get("label") or "Basemap"
    layer = QgsRasterLayer(url, name, "wms" if "wmts" in url else "xyz")
    if not layer.isValid():
        return {"error": f"Could not load basemap from {url}."}
    QgsProject.instance().addMapLayer(layer)
    return {
        "addedLayer": layer.id(),
        "name": layer.name(),
        "url": url,
    }


def _h_remove_layer(params: dict, on_progress=None) -> dict[str, Any]:
    from qgis.core import QgsProject

    layer = _resolve_layer(params["layer"])
    if layer is None:
        return {"error": f"Layer '{params['layer']}' not found."}
    QgsProject.instance().removeMapLayer(layer.id())
    return {"removedLayer": params["layer"]}


def _h_apply_symbology(params: dict, on_progress=None) -> dict[str, Any]:
    """Apply a categorized/graduated symbology to a vector layer."""
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsCategorizedSymbolRenderer,
        QgsGraduatedSymbolRenderer, QgsSymbol, QgsRendererCategory,
        QgsRendererRange, QgsColorRampShader, QgsFillSymbol,
        QgsPalettedRasterRenderer,
    )

    layer = _resolve_layer(params["layer"])
    if layer is None:
        return {"error": f"Layer '{params['layer']}' not found."}
    mode = params.get("mode", "categorized")
    field_name = params.get("field")
    color = params.get("color", "#ff0000")
    class_count = int(params.get("class_count", 5))

    if not isinstance(layer, QgsVectorLayer):
        return {"error": f"Layer '{params['layer']}' is not vector."}

    if field_name is None:
        # Simple single-symbol style
        symbol = QgsFillSymbol.createSimple({"color": color, "outline_color": "black"})
        from qgis.core import QgsSingleSymbolRenderer
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()
        return {"layer": layer.name(), "mode": "single", "symbol": color}

    idx = layer.fields().indexOf(field_name)
    if idx < 0:
        return {"error": f"Field '{field_name}' not found in layer '{layer.name()}'."}

    if mode == "graduated":
        values = [f.attribute(idx) for f in layer.getFeatures()]
        numeric = sorted([v for v in values if isinstance(v, (int, float))])
        if not numeric:
            return {"error": f"Field '{field_name}' has no numeric values for graduated renderer."}
        lo, hi = numeric[0], numeric[-1]
        step = (hi - lo) / class_count if class_count > 0 else 1
        ranges = []
        from qgis.core import QgsSymbol
        for i in range(class_count):
            r_from = lo + i * step
            r_to = lo + (i + 1) * step if i < class_count - 1 else hi + 0.001
            sym = QgsFillSymbol.createSimple({"color": color})
            ranges.append(QgsRendererRange(r_from, r_to, sym, f"{r_from:.2f}–{r_to:.2f}"))
        renderer = QgsGraduatedSymbolRenderer(field_name, ranges)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
        return {"layer": layer.name(), "mode": "graduated", "field": field_name, "classes": class_count}

    # categorized
    unique_values = set()
    for f in layer.getFeatures():
        v = f.attribute(idx)
        if v is not None:
            unique_values.add(str(v))
        if len(unique_values) >= 25:
            break
    categories = []
    palette = params.get("palette", "Spectral")
    for i, val in enumerate(sorted(unique_values)):
        sym = QgsFillSymbol.createSimple({"color": _ramp_color(i, len(unique_values))})
        categories.append(QgsRendererCategory(val, sym, str(val)))
    renderer = QgsCategorizedSymbolRenderer(field_name, categories)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return {
        "layer": layer.name(),
        "mode": "categorized",
        "field": field_name,
        "categories": len(categories),
        "uniqueValues": list(unique_values)[:10],
    }


def _ramp_color(index: int, total: int) -> str:
    """Simple HSL ramp: evenly distribute hues."""
    import colorsys
    hue = index / max(total, 1)
    r, g, b = colorsys.hls_to_rgb(hue, 0.55, 0.75)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


def create_tools() -> list[Tool]:
    """Build the complete GeoLibre-style typed tool list."""
    return [
        Tool(
            name="list_layers",
            description=(
                "List the layers currently in the QGIS project: id, name, type, "
                "CRS, visibility, feature count (vector). Call before referencing "
                "any layer."
            ),
            parameters={},
            handler=_h_list_layers,
        ),
        Tool(
            name="get_layer_schema",
            description=(
                "Get detailed schema for a vector layer: fields (name, QGIS type "
                "name, alias), feature count, extent, CRS."
            ),
            parameters={"layer": {"type": "string", "description": "Layer name or id."}},
            required=["layer"],
            handler=_h_get_layer_schema,
        ),
        Tool(
            name="set_layer_visibility",
            description="Show or hide an existing layer by name or id.",
            parameters={
                "layer": {"type": "string", "description": "Layer name or id."},
                "visible": {"type": "boolean", "description": "True to show, false to hide."},
            },
            required=["layer", "visible"],
            handler=_h_set_layer_visibility,
        ),
        Tool(
            name="capture_canvas",
            description=(
                "Capture the current QGIS map canvas as a PNG data URL. Use to "
                "verify map state visually after operations."
            ),
            parameters={
                "max_dim": {
                    "type": "integer",
                    "description": "Maximum image dimension (default 1200).",
                    "default": 1200,
                }
            },
            handler=_h_capture_canvas,
        ),
        Tool(
            name="zoom_to_layer",
            description="Zoom the map canvas to the extent of an existing layer.",
            parameters={"layer": {"type": "string", "description": "Layer name or id."}},
            required=["layer"],
            handler=_h_zoom_to_layer,
        ),
        Tool(
            name="zoom_to_place",
            description=(
                "Zoom the canvas to a named place (country, city, region) by "
                "geocoding with Nominatim. Use for 'zoom to X' requests."
            ),
            parameters={"place": {"type": "string", "description": "Place name."}},
            required=["place"],
            handler=_h_zoom_to_place,
        ),
        Tool(
            name="run_processing_algorithm",
            description=(
                "Run a QGIS processing algorithm by id (e.g. 'native:buffer'). "
                "Parameters may reference layers by name or id; string values are "
                "auto-resolved to layer objects. Long-running algorithms run in "
                "the QGIS thread pool so the UI stays responsive."
            ),
            parameters={
                "algorithm": {"type": "string", "description": "Algorithm id, e.g. 'native:buffer'."},
                "parameters": {
                    "type": "object",
                    "description": "Algorithm parameters keyed by parameter name.",
                    "default": {},
                },
            },
            required=["algorithm"],
            handler=_h_run_processing,
            examples=[
                '{"algorithm": "native:buffer", "parameters": {"INPUT": "roads", "DISTANCE": 100.0, "OUTPUT": "TEMPORARY_OUTPUT"}}',
            ],
        ),
        Tool(
            name="load_basemap",
            description=(
                "Add an XYZ raster basemap (osm, esri-imagery, esri-topo, "
                "opentopomap, carto-dark) or a raw HTTPS XYZ URL to the project."
            ),
            parameters={
                "basemap": {
                    "type": "string",
                    "description": "Known basemap id or HTTPS XYZ URL template.",
                    "default": "osm",
                },
                "name": {"type": "string", "description": "Layer name.", "default": ""},
            },
            handler=_h_load_basemap,
        ),
        Tool(
            name="remove_layer",
            description="Remove an existing layer from the project.",
            parameters={"layer": {"type": "string", "description": "Layer name or id."}},
            required=["layer"],
            handler=_h_remove_layer,
            destructive=True,
        ),
        Tool(
            name="apply_symbology",
            description=(
                "Apply categorized/graduated/single-symbol symbology to a vector "
                "layer. `field` selects the classification attribute; `mode` is "
                "'categorized' (default) or 'graduated'; `class_count` for "
                "graduated."
            ),
            parameters={
                "layer": {"type": "string", "description": "Layer name or id."},
                "field": {"type": "string", "description": "Attribute field name for classification."},
                "mode": {
                    "type": "string",
                    "enum": ["categorized", "graduated", "single"],
                    "description": "Symbology mode.",
                    "default": "categorized",
                },
                "class_count": {
                    "type": "integer",
                    "description": "Number of classes (graduated).",
                    "default": 5,
                },
                "color": {"type": "string", "description": "Base color hex (single mode).", "default": "#ff0000"},
            },
            required=["layer"],
            handler=_h_apply_symbology,
        ),
    ]


def tool_schemas() -> list[dict[str, Any]]:
    return [t.to_openai_schema() for t in create_tools()]

# ---------------------------------------------------------------------------
# Bridge: expose typed tools through the legacy ToolRegistry.execute() interface
# ---------------------------------------------------------------------------


class TypedToolBridge:
    """Compatibility layer so agent_dispatcher can call typed tools through
    the same `execute(name, params, on_progress)` contract as the legacy
    ToolRegistry. Tools that mutate the project (processing, remove,
    load_basemap, apply_symbology) run on the QGIS main thread via the
    executor's QTimer queue; pure-read tools run inline (they only read
    QgsProject.instance() state, safe on any thread that pumps no events).
    """

    def __init__(self, executor=None):
        self.executor = executor
        self._tools = {t.name: t for t in create_tools()}
        self._permission_mode = "default"

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def list_tools(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def set_permission_mode(self, mode: str) -> None:
        self._permission_mode = mode

    def check_permission(self, tool_name: str, params: dict, code: str = None) -> dict:
        """Mirror the legacy ToolRegistry.check_permission contract."""
        if self._permission_mode in ("bypassPermissions", "acceptEdits"):
            return {"behavior": "allow"}
        if self._permission_mode == "dontAsk":
            return {"behavior": "deny", "message": "Tool execution blocked in dontAsk mode"}
        tool = self._tools.get(tool_name)
        if tool is not None and tool.destructive:
            return {
                "behavior": "ask",
                "tool_name": tool_name,
                "description": f"Run {tool_name} (destructive operation)",
                "risk_level": "medium",
            }
        return {"behavior": "allow"}

    async def execute(self, name: str, params: dict, on_progress=None) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown typed tool: {name}")
        # Read-only tools may run inline (safe: they only touch QgsProject
        # instance state, not widgets). Mutating tools must run on the QGIS
        # main thread through the executor so Qt widget access is safe.
        read_only = name in {"list_layers", "get_layer_schema", "capture_canvas", "zoom_to_layer", "zoom_to_place"}
        if read_only:
            result = tool.handler(params, on_progress)
        else:
            import aery_plugin.qgis_executor as executor_mod
            ex = self.executor or getattr(executor_mod, "_current_executor", lambda: None)()
            if ex is None:
                # Fallback: run inline (may be unsafe for widgets, but tests
                # without a live QGIS instance rely on this path).
                result = tool.handler(params, on_progress)
            else:
                code = _marshal_tool_call(name, params)
                res = ex.execute(code, 300, on_progress)
                if not res.get("success"):
                    raise RuntimeError(res.get("error", f"Tool '{name}' failed"))
                result = res.get("result")
        return json.dumps(result, indent=2) if isinstance(result, dict) else str(result)


def _marshal_tool_call(name: str, params: dict) -> str:
    """Build a main-thread task string for mutating typed tools.

    The QGISCodeExecutor accepts special `__tool__:<name>` task strings with
    the params JSON-encoded; qgis_executor._process_queue dispatches them to
    the corresponding handler on the main thread. This keeps typed tools on
    the GUI thread without generating executable code.
    """
    return f"__tool__:{name}:{json.dumps(params)}"