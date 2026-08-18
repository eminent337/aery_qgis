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
    @property
    def input_schema(self) -> dict[str, Any]:
        """MCP-compatible input schema."""
        return {
            "type": "object",
            "properties": self.parameters,
            "required": self.required,
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
# New typed tool handlers (GeoLibre parity)
# ---------------------------------------------------------------------------
def _h_add_vector_layer(params: dict, on_progress=None) -> dict[str, Any]:
    """Load a vector layer (GeoJSON, Shapefile, GPKG, etc.) from a file path or URL."""
    from qgis.core import QgsProject, QgsVectorLayer
    path = params.get("path", "").strip()
    name = params.get("name", "").strip() or "Vector Layer"
    if not path:
        return {"error": "'path' is required."}
    layer = QgsVectorLayer(path, name, "ogr")
    if not layer.isValid():
        return {"error": f"Could not load vector layer from '{path}'."}
    QgsProject.instance().addMapLayer(layer)
    return {
        "addedLayer": layer.id(),
        "name": layer.name(),
        "path": path,
        "featureCount": layer.featureCount(),
        "geometryType": layer.geometryType().name if hasattr(layer, "geometryType") else "unknown",
        "crs": layer.crs().authid(),
    }
def _h_add_raster_layer(params: dict, on_progress=None) -> dict[str, Any]:
    """Load a raster layer (GeoTIFF, COG, etc.) from a file path or URL."""
    from qgis.core import QgsProject, QgsRasterLayer
    path = params.get("path", "").strip()
    name = params.get("name", "").strip() or "Raster Layer"
    if not path:
        return {"error": "'path' is required."}
    layer = QgsRasterLayer(path, name)
    if not layer.isValid():
        return {"error": f"Could not load raster layer from '{path}'."}
    QgsProject.instance().addMapLayer(layer)
    return {
        "addedLayer": layer.id(),
        "name": layer.name(),
        "path": path,
        "width": layer.width(),
        "height": layer.height(),
        "bandCount": layer.bandCount(),
        "crs": layer.crs().authid(),
    }
def _h_create_layer(params: dict, on_progress=None) -> dict[str, Any]:
    """Create a new memory layer (point, line, polygon) with optional fields."""
    from qgis.core import QgsProject, QgsVectorLayer, QgsFields, QgsField
    from PyQt6.QtCore import QVariant
    geometry_type = params.get("geometry_type", "Point").capitalize()
    name = params.get("name", "New Layer").strip()
    crs = params.get("crs", "EPSG:4326")
    fields_def = params.get("fields", [])
    type_map = {
        "Point": "Point",
        "Linestring": "LineString",
        "Line": "LineString",
        "Polygon": "Polygon",
        "Multipoint": "MultiPoint",
        "Multilinestring": "MultiLineString",
        "Multipolygon": "MultiPolygon",
    }
    wkt_type = type_map.get(geometry_type, "Point")
    uri = f"{wkt_type}?crs={crs}"
    layer = QgsVectorLayer(uri, name, "memory")
    if not layer.isValid():
        return {"error": f"Could not create {geometry_type} layer."}
    if fields_def:
        fields = QgsFields()
        for f in fields_def:
            fname = f.get("name")
            ftype = f.get("type", "string").lower()
            type_map_qt = {
                "string": QVariant.Type.String,
                "integer": QVariant.Type.Int,
                "int": QVariant.Type.Int,
                "double": QVariant.Type.Double,
                "float": QVariant.Type.Double,
                "boolean": QVariant.Type.Bool,
                "bool": QVariant.Type.Bool,
                "date": QVariant.Type.Date,
                "datetime": QVariant.Type.DateTime,
            }
            qt_type = type_map_qt.get(ftype, QVariant.Type.String)
            fields.append(QgsField(fname, qt_type))
        layer.dataProvider().addAttributes(fields)
        layer.updateFields()
    QgsProject.instance().addMapLayer(layer)
    return {
        "addedLayer": layer.id(),
        "name": layer.name(),
        "geometryType": geometry_type,
        "crs": crs,
        "fields": [{"name": f.name(), "type": f.typeName()} for f in layer.fields()],
    }
def _h_add_feature(params: dict, on_progress=None) -> dict[str, Any]:
    """Add a feature (geometry + attributes) to an existing editable layer."""
    from qgis.core import QgsFeature, QgsGeometry
    from qgis.core import QgsProject
    layer_ref = params.get("layer", "").strip()
    geometry = params.get("geometry")
    attributes = params.get("attributes", {})
    if not layer_ref:
        return {"error": "'layer' is required."}
    if geometry is None:
        return {"error": "'geometry' is required (GeoJSON-like dict)."}
    layer = _resolve_layer(layer_ref)
    if layer is None:
        return {"error": f"Layer '{layer_ref}' not found."}
    if not layer.isEditable():
        if not layer.startEditing():
            return {"error": f"Could not start editing on layer '{layer.name()}'."}
    feat = QgsFeature(layer.fields())
    geom = QgsGeometry.fromGeoJson(json.dumps(geometry))
    if geom.isNull():
        return {"error": "Invalid geometry."}
    feat.setGeometry(geom)
    for key, value in attributes.items():
        idx = layer.fields().indexFromName(key)
        if idx >= 0:
            feat.setAttribute(idx, value)
    if not layer.addFeature(feat):
        layer.rollBack()
        return {"error": "Failed to add feature."}
    if not layer.commitChanges():
        return {"error": "Failed to commit changes."}
    return {
        "layer": layer.name(),
        "featureId": feat.id(),
        "geometryType": layer.geometryType().name,
    }
def _h_run_expression(params: dict, on_progress=None) -> dict[str, Any]:
    """Evaluate a QGIS expression on features of a layer."""
    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsFeatureRequest
    layer_ref = params.get("layer", "").strip()
    expression_text = params.get("expression", "").strip()
    limit = params.get("limit", 100)
    if not layer_ref:
        return {"error": "'layer' is required."}
    if not expression_text:
        return {"error": "'expression' is required."}
    layer = _resolve_layer(layer_ref)
    if layer is None:
        return {"error": f"Layer '{layer_ref}' not found."}
    exp = QgsExpression(expression_text)
    if exp.hasParserError():
        return {"error": f"Expression parse error: {exp.parserErrorString()}"}
    context = QgsExpressionContext()
    context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    results = []
    for feat in layer.getFeatures(QgsFeatureRequest().setLimit(limit)):
        context.setFeature(feat)
        value = exp.evaluate(context)
        if exp.hasEvalError():
            return {"error": f"Expression eval error: {exp.evalErrorString()}"}
        results.append({
            "featureId": feat.id(),
            "value": value,
        })
    return {
        "layer": layer.name(),
        "expression": expression_text,
        "results": results,
        "count": len(results),
    }
def _h_calculate_field(params: dict, on_progress=None) -> dict[str, Any]:
    """Run the native field calculator algorithm on a layer."""
    algorithm = "native:fieldcalculator"
    raw_params = {
        "INPUT": params["layer"],
        "FIELD_NAME": params.get("field_name", "calculated"),
        "FIELD_TYPE": params.get("field_type", 0),  # 0=Float, 1=Integer, 2=String, 3=Date
        "FIELD_LENGTH": params.get("field_length", 10),
        "FIELD_PRECISION": params.get("field_precision", 3),
        "NEW_FIELD": params.get("new_field", True),
        "FORMULA": params.get("formula", ""),
        "OUTPUT": "TEMPORARY_OUTPUT",
    }
    return _h_run_processing({"algorithm": algorithm, "parameters": raw_params}, on_progress)
def _h_join_attributes(params: dict, on_progress=None) -> dict[str, Any]:
    """Join attributes by location (spatial) or field (attribute)."""
    join_type = params.get("join_type", "location")  # "location" or "field"
    if join_type == "location":
        algorithm = "native:joinattributesbylocation"
        raw_params = {
            "INPUT": params["input_layer"],
            "JOIN": params["join_layer"],
            "PREDICATE": params.get("predicate", [0]),  # 0=intersects
            "JOIN_FIELDS": params.get("join_fields", []),
            "METHOD": params.get("method", 0),  # 0=take first, 1=summarize
            "DISCARD_NONMATCHING": params.get("discard_nonmatching", False),
            "OUTPUT": "TEMPORARY_OUTPUT",
        }
    else:
        algorithm = "native:joinattributestable"
        raw_params = {
            "INPUT": params["input_layer"],
            "FIELD": params["input_field"],
            "INPUT_2": params["join_layer"],
            "FIELD_2": params["join_field"],
            "FIELDS_TO_COPY": params.get("fields_to_copy", []),
            "METHOD": params.get("method", 0),
            "DISCARD_NONMATCHING": params.get("discard_nonmatching", False),
            "OUTPUT": "TEMPORARY_OUTPUT",
        }
    return _h_run_processing({"algorithm": algorithm, "parameters": raw_params}, on_progress)
def _h_extract_by_attribute(params: dict, on_progress=None) -> dict[str, Any]:
    """Filter features by attribute value (native:extractbyattribute)."""
    algorithm = "native:extractbyattribute"
    raw_params = {
        "INPUT": params["layer"],
        "FIELD": params["field"],
        "OPERATOR": params.get("operator", 0),  # 0=equals, 1=not equals, 2=greater, 3=less, etc.
        "VALUE": params["value"],
        "OUTPUT": "TEMPORARY_OUTPUT",
    }
    return _h_run_processing({"algorithm": algorithm, "parameters": raw_params}, on_progress)
def _h_dissolve(params: dict, on_progress=None) -> dict[str, Any]:
    """Aggregate/dissolve geometries (native:dissolve)."""
    algorithm = "native:dissolve"
    raw_params = {
        "INPUT": params["layer"],
        "FIELD": params.get("field", []),
        "SEPARATE_DISJOINT": params.get("separate_disjoint", False),
        "OUTPUT": "TEMPORARY_OUTPUT",
    }
    return _h_run_processing({"algorithm": algorithm, "parameters": raw_params}, on_progress)
def _h_buffer(params: dict, on_progress=None) -> dict[str, Any]:
    """Buffer geometries (native:buffer)."""
    algorithm = "native:buffer"
    raw_params = {
        "INPUT": params["layer"],
        "DISTANCE": params.get("distance", 100.0),
        "SEGMENTS": params.get("segments", 5),
        "END_CAP_STYLE": params.get("end_cap_style", 0),  # 0=round, 1=flat, 2=square
        "JOIN_STYLE": params.get("join_style", 0),  # 0=round, 1=miter, 2=bevel
        "MITER_LIMIT": params.get("miter_limit", 2.0),
        "DISSOLVE": params.get("dissolve", False),
        "OUTPUT": "TEMPORARY_OUTPUT",
    }
    return _h_run_processing({"algorithm": algorithm, "parameters": raw_params}, on_progress)
def _h_intersection(params: dict, on_progress=None) -> dict[str, Any]:
    """Spatial intersection of two layers (native:intersection)."""
    algorithm = "native:intersection"
    raw_params = {
        "INPUT": params["input_layer"],
        "OVERLAY": params["overlay_layer"],
        "INPUT_FIELDS": params.get("input_fields", []),
        "OVERLAY_FIELDS": params.get("overlay_fields", []),
        "OVERLAY_FIELDS_PREFIX": params.get("overlay_fields_prefix", ""),
        "OUTPUT": "TEMPORARY_OUTPUT",
    }
    return _h_run_processing({"algorithm": algorithm, "parameters": raw_params}, on_progress)
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
            handler=_h_apply_symbology,
        ),
        Tool(
            name="add_vector_layer",
            description=(
                "Load a vector layer (GeoJSON, Shapefile, GPKG, etc.) from a file path or URL. "
                "Returns layer ID, name, feature count, geometry type, and CRS."
            ),
            parameters={
                "path": {"type": "string", "description": "File path or URL to the vector data."},
                "name": {"type": "string", "description": "Layer name (optional).", "default": ""},
            },
            required=["path"],
            handler=_h_add_vector_layer,
            destructive=False,
        ),
        Tool(
            name="add_raster_layer",
            description=(
                "Load a raster layer (GeoTIFF, COG, etc.) from a file path or URL. "
                "Returns layer ID, name, dimensions, band count, and CRS."
            ),
            parameters={
                "path": {"type": "string", "description": "File path or URL to the raster data."},
                "name": {"type": "string", "description": "Layer name (optional).", "default": ""},
            },
            required=["path"],
            handler=_h_add_raster_layer,
            destructive=False,
        ),
        Tool(
            name="create_layer",
            description=(
                "Create a new memory layer (Point, LineString, Polygon, MultiPoint, MultiLineString, MultiPolygon) "
                "with optional attribute fields. Returns layer ID and schema."
            ),
            parameters={
                "geometry_type": {
                    "type": "string",
                    "description": "Geometry type: Point, LineString, Polygon, MultiPoint, MultiLineString, MultiPolygon.",
                    "default": "Point",
                },
                "name": {"type": "string", "description": "Layer name.", "default": "New Layer"},
                "crs": {"type": "string", "description": "CRS (e.g. 'EPSG:4326').", "default": "EPSG:4326"},
                "fields": {
                    "type": "array",
                    "description": "List of field definitions: [{name: 'field_name', type: 'string|integer|double|boolean|date|datetime'}]",
                    "items": {"type": "object", "properties": {"name": {"type": "string"}, "type": {"type": "string"}}},
                    "default": [],
                },
            },
            required=[],
            handler=_h_create_layer,
            destructive=False,
        ),
        Tool(
            name="add_feature",
            description=(
                "Add a feature (geometry + attributes) to an existing editable layer. "
                "Geometry must be a GeoJSON-like dict. Attributes are key-value pairs matching layer fields."
            ),
            parameters={
                "layer": {"type": "string", "description": "Target layer name or id."},
                "geometry": {"type": "object", "description": "GeoJSON geometry object (Point, LineString, Polygon, etc.)."},
                "attributes": {
                    "type": "object",
                    "description": "Attribute key-value pairs matching layer fields.",
                    "default": {},
                },
            },
            required=["layer", "geometry"],
            handler=_h_add_feature,
            destructive=True,
        ),
        Tool(
            name="run_expression",
            description=(
                "Evaluate a QGIS expression on features of a layer. Returns value per feature (up to limit). "
                "Use for computed fields, filtering logic, or spatial predicates without modifying data."
            ),
            parameters={
                "layer": {"type": "string", "description": "Layer name or id."},
                "expression": {"type": "string", "description": "QGIS expression string (e.g. \"$area > 1000\", \"name LIKE 'A%'\")."},
                "limit": {"type": "integer", "description": "Maximum features to evaluate (default 100).", "default": 100},
            },
            required=["layer", "expression"],
            handler=_h_run_expression,
            destructive=False,
        ),
        Tool(
            name="calculate_field",
            description=(
                "Run the native field calculator algorithm on a layer. Creates or updates a field with an expression."
            ),
            parameters={
                "layer": {"type": "string", "description": "Layer name or id."},
                "field_name": {"type": "string", "description": "Output field name.", "default": "calculated"},
                "field_type": {"type": "integer", "description": "0=Float, 1=Integer, 2=String, 3=Date.", "default": 0},
                "field_length": {"type": "integer", "description": "Field length (default 10).", "default": 10},
                "field_precision": {"type": "integer", "description": "Decimal precision (default 3).", "default": 3},
                "new_field": {"type": "boolean", "description": "Create new field (true) or update existing (false).", "default": True},
                "formula": {"type": "string", "description": "QGIS expression for field values."},
            },
            required=["layer", "formula"],
            handler=_h_calculate_field,
            destructive=True,
        ),
        Tool(
            name="join_attributes",
            description=(
                "Join attributes by location (spatial) or field (attribute). "
                "join_type='location' uses native:joinattributesbylocation; 'field' uses native:joinattributestable."
            ),
            parameters={
                "join_type": {
                    "type": "string",
                    "description": "Join type: 'location' (spatial) or 'field' (attribute).",
                    "enum": ["location", "field"],
                    "default": "location",
                },
                "input_layer": {"type": "string", "description": "Input layer name or id."},
                "join_layer": {"type": "string", "description": "Join layer name or id."},
                "predicate": {
                    "type": "array",
                    "description": "Spatial predicates (location join): 0=intersects, 1=contains, 2=within, etc.",
                    "items": {"type": "integer"},
                    "default": [0],
                },
                "join_fields": {
                    "type": "array",
                    "description": "Fields to copy from join layer (location join).",
                    "items": {"type": "string"},
                    "default": [],
                },
                "input_field": {"type": "string", "description": "Input layer field (field join)."},
                "join_field": {"type": "string", "description": "Join layer field (field join)."},
                "fields_to_copy": {
                    "type": "array",
                    "description": "Fields to copy from join layer (field join).",
                    "items": {"type": "string"},
                    "default": [],
                },
                "method": {"type": "integer", "description": "Join method: 0=take first, 1=summarize.", "default": 0},
                "discard_nonmatching": {"type": "boolean", "description": "Discard non-matching features.", "default": False},
            },
            required=["join_type", "input_layer", "join_layer"],
            handler=_h_join_attributes,
            destructive=False,
        ),
        Tool(
            name="extract_by_attribute",
            description=(
                "Filter features by attribute value using native:extractbyattribute. "
                "Operators: 0=equals, 1=not equals, 2=greater, 3=less, 4=greater or equal, 5=less or equal, 6=contains, 7=not contains, 8=starts with, 9=ends with."
            ),
            parameters={
                "layer": {"type": "string", "description": "Layer name or id."},
                "field": {"type": "string", "description": "Field to filter on."},
                "operator": {"type": "integer", "description": "Comparison operator (0-9).", "default": 0},
                "value": {"type": "string", "description": "Value to compare against."},
            },
            required=["layer", "field", "value"],
            handler=_h_extract_by_attribute,
            destructive=False,
        ),
        Tool(
            name="dissolve",
            description=(
                "Aggregate/dissolve geometries using native:dissolve. Optionally dissolve by field values."
            ),
            parameters={
                "layer": {"type": "string", "description": "Layer name or id."},
                "field": {
                    "type": "array",
                    "description": "Field(s) to dissolve by (empty = dissolve all).",
                    "items": {"type": "string"},
                    "default": [],
                },
                "separate_disjoint": {"type": "boolean", "description": "Output separate parts for disjoint geometries.", "default": False},
            },
            required=["layer"],
            handler=_h_dissolve,
            destructive=False,
        ),
        Tool(
            name="buffer",
            description=(
                "Buffer geometries using native:buffer. Supports distance, segments, end cap/join styles, and optional dissolve."
            ),
            parameters={
                "layer": {"type": "string", "description": "Layer name or id."},
                "distance": {"type": "number", "description": "Buffer distance (default 100.0).", "default": 100.0},
                "segments": {"type": "integer", "description": "Segments per quadrant (default 5).", "default": 5},
                "end_cap_style": {"type": "integer", "description": "0=round, 1=flat, 2=square.", "default": 0},
                "join_style": {"type": "integer", "description": "0=round, 1=miter, 2=bevel.", "default": 0},
                "miter_limit": {"type": "number", "description": "Miter limit (default 2.0).", "default": 2.0},
                "dissolve": {"type": "boolean", "description": "Dissolve result (default false).", "default": False},
            },
            required=["layer"],
            handler=_h_buffer,
            destructive=False,
        ),
        Tool(
            name="intersection",
            description=(
                "Spatial intersection of two layers using native:intersection. "
                "Returns features from input layer that intersect overlay layer."
            ),
            parameters={
                "input_layer": {"type": "string", "description": "Input layer name or id."},
                "overlay_layer": {"type": "string", "description": "Overlay layer name or id."},
                "input_fields": {
                    "type": "array",
                    "description": "Fields to keep from input layer (empty = all).",
                    "items": {"type": "string"},
                    "default": [],
                },
                "overlay_fields": {
                    "type": "array",
                    "description": "Fields to keep from overlay layer (empty = all).",
                    "items": {"type": "string"},
                    "default": [],
                },
                "overlay_fields_prefix": {"type": "string", "description": "Prefix for overlay fields (default '').", "default": ""},
            },
            required=["input_layer", "overlay_layer"],
            handler=_h_intersection,
            destructive=False,
        ),
    ]

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
        return json.dumps(result, indent=2) if isinstance(result, dict) else str(result)
def tool_schemas() -> list[dict[str, Any]]:
    return [t.to_openai_schema() for t in create_tools()]
def _marshal_tool_call(name: str, params: dict) -> str:
    """Build a main-thread task string for mutating typed tools.
    The QGISCodeExecutor accepts special `__tool__:<name>` task strings with
    the params JSON-encoded; qgis_executor._process_queue dispatches them to
    the corresponding handler on the main thread. This keeps typed tools on
    the GUI thread without generating executable code.
    """
    return f"__tool__:{name}:{json.dumps(params)}"