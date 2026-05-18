"""Geospatial helper functions for the Aery QGIS agent.

These functions are:
1. Registered as first-class agent tools (Approach 1)
2. Injected into executor globals for use inside run_qgis_code (Approach 2)
"""

import json
import math
import os
import subprocess
import tempfile
import urllib.request
import base64
from typing import Any


def export_webmap(output_dir: str, basemap: str = "osm",
                  extent: str = "", include_search: bool = False,
                  title: str = "", iface=None) -> dict:
    """Export the current QGIS project as an interactive Leaflet.js web map.

    Args:
        output_dir: Full path to output directory (created if missing).
        basemap: 'osm', 'satellite', 'topo', 'stamen_toner', or 'none'.
        extent: Bbox override 'xmin,xmax,ymin,ymax' in project CRS (default: canvas).
        include_search: Add a nominatim geocoding search box.
        title: HTML page title (default: project name).
        iface: QGIS iface object (auto-injected by executor).

    Returns:
        Dict with format, files list, and output_dir.
    """
    from qgis.core import QgsProject, QgsVectorLayer, QgsVectorFileWriter, Qgis

    out_dir = output_dir
    os.makedirs(out_dir, exist_ok=True)
    data_dir = os.path.join(out_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    project = QgsProject.instance()
    all_layers = list(project.mapLayers().values())
    layer_files = []

    for i, lyr in enumerate(all_layers):
        name = lyr.name().replace(" ", "_").replace("/", "_")
        try:
            if lyr.type() == Qgis.LayerType.Vector:
                export_path = os.path.join(data_dir, f"{name}_{i}.geojson")
                options = QgsVectorLayer.LayerOptions()
                exp = QgsVectorFileWriter(export_path, "UTF-8", lyr.fields(),
                                          lyr.wkbType(), lyr.crs(), options)
                exp.addFeatures(lyr.getFeatures())
                exp = None
                layer_files.append({"name": lyr.name(), "file": f"data/{name}_{i}.geojson",
                                    "count": lyr.featureCount()})
            elif lyr.type() == Qgis.LayerType.Raster:
                src = lyr.source()
                if src and os.path.isfile(src):
                    try:
                        from osgeo import gdal
                        ds = gdal.Open(src)
                        if ds:
                            gdal.Translate(os.path.join(data_dir, f"{name}_{i}.tif"), ds)
                            layer_files.append({"name": lyr.name(), "file": f"data/{name}_{i}.tif",
                                                "bandcount": ds.RasterCount})
                    except ImportError:
                        pass
        except Exception as e:
            print(f"  skip {lyr.name()}: {e}")

    # Parse extent
    bbox = None
    if extent:
        try:
            parts = [float(v) for v in extent.split(",")]
            if len(parts) == 4:
                from qgis.core import QgsRectangle
                bbox = QgsRectangle(parts[0], parts[2], parts[1], parts[3])
        except Exception:
            pass

    if not bbox and iface:
        bbox = iface.mapCanvas().extent()

    html = _build_leaflet_html(layer_files, basemap, include_search, title or None, bbox)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)

    layer_files.append({"name": "index.html", "file": "index.html", "size": len(html)})
    print(f"Webmap: {len(layer_files)} files to {out_dir}")
    return {"format": "leaflet", "files": layer_files, "output_dir": out_dir}


def publish_geoserver(layer: str, geoserver_url: str, username: str, password: str,
                      workspace: str = "default", layer_name: str = "",
                      publish_as: str = "auto") -> dict:
    """Publish a vector or raster layer to a GeoServer REST endpoint.

    Args:
        layer: QGIS layer name to publish.
        geoserver_url: GeoServer base URL (e.g. 'http://localhost:8080/geoserver').
        username: GeoServer admin username.
        password: GeoServer admin password.
        workspace: GeoServer workspace name (default: 'default').
        layer_name: GeoServer layer name (default: same as QGIS layer).
        publish_as: 'auto', 'vector', or 'raster'.

    Returns:
        Dict with published status, URLs, and layer info.
    """
    from qgis.core import QgsProject

    layer_name = layer_name or layer
    gs_url = geoserver_url.rstrip("/")

    lyr = next((l for l in QgsProject.instance().mapLayers().values()
                if l.name() == layer), None)
    if lyr is None:
        raise ValueError(f"Layer not found: {layer}")

    is_raster = str(lyr.type()) == "Raster"
    publish_type = "raster" if is_raster or publish_as == "raster" else "vector"
    src_path = lyr.source()
    if not src_path or not os.path.isfile(src_path):
        raise FileNotFoundError(f"Layer source not found: {src_path}")

    tmp = tempfile.mkdtemp(prefix="gs_upload_")
    ext = ".tif" if publish_type == "raster" else ".gpkg"
    upload_path = os.path.join(tmp, layer_name + ext)

    if publish_type == "vector":
        subprocess.run(["ogr2ogr", "-overwrite", "-f", "GPKG", upload_path, src_path],
                       check=True, capture_output=True)
    else:
        import shutil
        shutil.copy2(src_path, upload_path)

    boundary = "----GeoServerBoundary7MA4YWxk"
    with open(upload_path, "rb") as f:
        payload = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(upload_path)}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()

    rest = f"/rest/workspaces/{workspace}/datastores/{layer_name}/file.{ext[1:]}"
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    req = urllib.request.Request(gs_url + rest, data=body, method="PUT")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status = resp.status
        ok, reason = True, ""
        print(f"Published: {status}")
    except Exception as exc:
        ok, reason = False, str(exc)
        print(f"Error: {exc}")

    result = {
        "published": ok, "layer": layer_name, "workspace": workspace,
        "type": publish_type, "geoserver_url": gs_url,
        "wfs_url": f"{gs_url}/{workspace}/wfs", "wms_url": f"{gs_url}/{workspace}/wms",
    }
    if reason:
        result["error"] = reason
    print(json.dumps(result))
    return result


def set_layer_style(layer: str, style: str, band: int = 1,
                    colormap: str = "viridis", min: float = None, max: float = None,
                    red: int = 1, green: int = 2, blue: int = 3,
                    column: str = "", classes: int = 5,
                    method: str = "jenks", color_ramp: str = "Reds",
                    legend_title: str = "", legend_expression: str = "",
                    iface=None) -> dict:
    """Apply visual styles to raster and vector layers.

    Args:
        layer: Layer name or ID.
        style: 'singleband', 'multiband', 'graduated', 'categorized', or 'paletted'.
        band: Band for singleband (default: 1).
        colormap: Colormap name (viridis, gray, rdylgn, spectral, terrain, etc.).
        min: Min pixel value for stretch (auto-detected if None).
        max: Max pixel value for stretch (auto-detected if None).
        red/green/blue: Band indices for multiband RGB.
        column: Attribute column for graduated/categorized style.
        classes: Number of class bins for graduated (default: 5).
        method: Classification method (jenks, equal, quantile, std).
        color_ramp: Named colour ramp (Reds, Blues, Spectral, Viridis, etc.).
        legend_title: Legend header text.
        legend_expression: Rule-based legend entries separated by '|'.
        iface: QGIS iface object (auto-injected by executor).

    Returns:
        Dict with styled layer info.
    """
    from qgis.core import (
        QgsProject, QgsColorRampShader, QgsRasterShader,
        QgsSingleBandPseudoColorRenderer, QgsMultiBandColorRenderer,
        QgsGraduatedSymbolRenderer, QgsCategorizedSymbolRenderer,
        QgsStyle, QgsClassificationJenks, QColor, Qgis,
    )

    proj = QgsProject.instance()
    lyr = next((l for l in proj.mapLayers().values() if l.name() == layer), None)
    if lyr is None:
        raise ValueError(f"Layer not found: {layer}")

    renderer = None

    if style == "singleband":
        prov = lyr.dataProvider()
        stats = None
        try:
            stats = prov.bandStatistics(band, Qgis.BandStatistics.All)
        except Exception:
            pass
        mn = float(min if min is not None else (stats.minimumValue if stats else 0))
        mx = float(max if max is not None else (stats.maximumValue if stats else 255))
        ramp = QgsColorRampShader()
        ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
        ramp.setColorRampItemList([
            QgsColorRampShader.ColorRampItem(mn, QColor("#313695"), str(mn)),
            QgsColorRampShader.ColorRampItem((mn + mx) / 3, QColor("#74add1"), "lo"),
            QgsColorRampShader.ColorRampItem((mn + mx) / 3 * 2, QColor("#ffffbf"), "mid"),
            QgsColorRampShader.ColorRampItem(mx, QColor("#d73027"), str(mx)),
        ])
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(ramp)
        renderer = QgsSingleBandPseudoColorRenderer(prov, band, shader)

    elif style == "multiband":
        renderer = QgsMultiBandColorRenderer(lyr.dataProvider(), red, green, blue)

    elif style == "graduated":
        fields = [f.name() for f in lyr.fields()]
        idx = fields.index(column) if column in fields else -1
        style_hnd = QgsStyle.defaultStyle()
        cr = style_hnd.colorRamp(color_ramp) or QgsStyle.defaultStyle().colorRamp("Reds")
        renderer = QgsGraduatedSymbolRenderer.createRenderer(lyr, idx, classes, cr, None)
        renderer.setClassificationMethod(QgsClassificationJenks())

    elif style == "categorized":
        fields = [f.name() for f in lyr.fields()]
        idx = fields.index(column) if column in fields else -1
        renderer = QgsCategorizedSymbolRenderer.createRenderer(lyr, idx, QgsStyle.defaultStyle())

    elif style == "paletted":
        renderer = lyr.renderer()

    if renderer:
        lyr.setRenderer(renderer)
    lyr.triggerRepaint()

    if legend_title:
        lyr.setName(legend_title)

    result = {
        "styled": layer,
        "style": style,
        "renderer": type(renderer).__name__ if renderer else "none",
    }
    print(json.dumps(result))
    return result


def multi_map_layout(layout_name: str, output_path: str,
                     paper_format: str = "A3", orientation: str = "landscape",
                     grid: str = "", panels: list = None,
                     margin_mm: float = 20, iface=None) -> dict:
    """Create a single print-layout PDF with multiple map panels arranged in a grid.

    Args:
        layout_name: Name for the new QgsPrintLayout.
        output_path: Full path to export (PDF).
        paper_format: Paper size (A2, A3, A4, Letter).
        orientation: 'portrait' or 'landscape'.
        grid: 'rows,cols' e.g. '2,2' (default: auto from panel count).
        panels: List of dicts with 'title', 'layer_set', 'extent'.
        margin_mm: Page margin in mm (default: 20).
        iface: QGIS iface object (auto-injected by executor).

    Returns:
        Dict with success status and output path.
    """
    from qgis.core import (
        QgsProject, QgsPrintLayout, QgsLayoutItemPage,
        QgsLayoutItemMap, QgsLayoutItemLabel, QgsLayoutExporter,
        QgsLayoutPoint, QgsLayoutSize, QgsRectangle, QFont,
    )
    from PyQt6.QtCore import QRectF

    panels = panels or []
    proj = QgsProject.instance()
    mgr = proj.layoutManager()

    # Remove existing layout with same name
    for i in range(mgr.printLayouts().count()):
        if mgr.printLayouts().at(i).name() == layout_name:
            mgr.removeLayout(mgr.printLayouts().at(i))

    layout = QgsPrintLayout(proj)
    page = layout.pageCollection().pages()[0]
    page.setPageSize(paper_format,
                     QgsLayoutItemPage.Orientation.Landscape if orientation == "landscape"
                     else QgsLayoutItemPage.Orientation.Portrait)

    usable_w = page.pageSize().width() - margin_mm * 2
    usable_h = page.pageSize().height() - margin_mm * 2

    n_panels = len(panels) if panels else 1
    if grid == "auto" or not grid:
        cols = math.ceil(math.sqrt(n_panels))
        rows = math.ceil(n_panels / cols)
    else:
        p = grid.split(",")
        rows = int(p[0]) if len(p) > 0 else math.ceil(math.sqrt(n_panels))
        cols = int(p[1]) if len(p) > 1 else rows

    gap = 20
    cell_w = (usable_w - gap * (cols - 1)) / cols
    cell_h = (usable_h - gap * (rows - 1)) / rows
    all_layers = {l.name(): l for l in proj.mapLayers().values() if l.isValid()}

    for idx, pdef in enumerate(panels):
        row, col = idx // cols, idx % cols
        x = margin_mm + col * (cell_w + gap)
        y = margin_mm + row * (cell_h + gap)

        lset = pdef.get("layer_set", [])
        for lyr in all_layers.values():
            lyr.setVisible(False)
        for nm in lset:
            if nm in all_layers:
                all_layers[nm].setVisible(True)

        map_itm = QgsLayoutItemMap(layout)
        map_itm.setRect(QRectF())
        map_itm.attemptMove(QgsLayoutPoint(x, y))
        map_itm.attemptResize(QgsLayoutSize(cell_w, cell_h))
        ext_str = pdef.get("extent")
        if ext_str:
            xy = [float(v) for v in ext_str.split(",")]
            map_itm.setExtent(QgsRectangle(xy[0], xy[2], xy[1], xy[3]))
        elif iface:
            map_itm.setExtent(iface.mapCanvas().extent())
        layout.addLayoutItem(map_itm)

        tt = pdef.get("title", "")
        if tt:
            lbl = QgsLayoutItemLabel(layout)
            lbl.setText(tt)
            lbl.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            lbl.attemptMove(QgsLayoutPoint(x, y - 12))
            lbl.adjustSizeToText()
            layout.addLayoutItem(lbl)

    exporter = QgsLayoutExporter(layout)
    exported = exporter.exportToPdf(output_path, QgsLayoutExporter.PdfExportSettings())
    ok = exported == QgsLayoutExporter.ExportResult.Success
    print(f"Multi-map PDF: {ok} -> {output_path}")
    return {"success": ok, "output_path": output_path}


def save_map_theme(theme_name: str) -> dict:
    """Save the current QGIS map theme (layer visibility + renderer state).

    Args:
        theme_name: Name for the saved theme.

    Returns:
        Dict with saved theme name and all available themes.
    """
    from qgis.core import QgsProject, QgsMapThemeCollection

    proj = QgsProject.instance()
    mgr = proj.mapThemeCollection()
    theme_name = theme_name.strip()

    layer_records = [
        QgsMapThemeCollection.MapThemeLayerRecord(l)
        for l in proj.mapLayers().values() if l.isValid()
    ]

    try:
        mgr.addMapTheme(theme_name, layer_records)
    except Exception:
        pass

    proj.write()
    result = {"saved": theme_name, "themes": sorted(mgr.mapThemes())}
    print(json.dumps(result))
    return result


def load_map_theme(theme_name: str, refresh: bool = True, iface=None) -> dict:
    """Load a previously saved QGIS map theme.

    Args:
        theme_name: Name of the theme to restore.
        refresh: Redraw canvas after loading (default: True).
        iface: QGIS iface object (auto-injected by executor).

    Returns:
        Dict with loaded theme name and record count.
    """
    from qgis.core import QgsProject

    proj = QgsProject.instance()
    mgr = proj.mapThemeCollection()

    records = mgr.mapThemeRecords(theme_name)
    if not records:
        raise ValueError(f"Theme not found: {sorted(mgr.mapThemes())}")

    for rec in records:
        lyr = rec.layer()
        if lyr:
            lyr.setVisible(rec.isVisible())

    proj.write()

    if refresh and iface:
        iface.mapCanvas().refreshAllLayers()
        iface.mapCanvas().refresh()

    result = {"loaded": theme_name, "records": len(records)}
    print(json.dumps(result))
    return result


def _build_leaflet_html(layer_files: list, basemap: str = "osm",
                        include_search: bool = False, title: str = None,
                        bbox=None) -> str:
    """Build a self-contained Leaflet.js HTML string from layer file references."""
    _is_rect = hasattr(bbox, "center") and hasattr(bbox, "yMinimum")
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
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        if basemap == "osm"
        else '&copy; Esri'
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
            '<input id="q" placeholder="Search location..." style="padding:4px 8px;width:200px;">'
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

    return f"""<!DOCTYPE html>
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


def list_map_themes() -> dict:
    """List all saved map themes in the current QGIS project.

    Returns:
        Dict with list of theme names.
    """
    from qgis.core import QgsProject

    proj = QgsProject.instance()
    mgr = proj.mapThemeCollection()
    themes = sorted(mgr.mapThemes())
    result = {"themes": themes, "count": len(themes)}
    print(json.dumps(result))
    return result


def refresh_canvas(iface=None) -> dict:
    """Refresh the QGIS map canvas and all layers.

    Call after set_layer_style, set visibility toggles, layer removals,
    or any operation that changes the visual state.

    Args:
        iface: QGIS iface object (auto-injected by executor).

    Returns:
        Dict with refresh status.
    """
    if iface:
        iface.mapCanvas().refreshAllLayers()
        iface.mapCanvas().refresh()
    result = {"refreshed": True}
    print(json.dumps(result))
    return result


# Tool definitions for registration in tools.py
GEOSPATIAL_TOOLS = [
    {
        "name": "export_webmap",
        "description": (
            "Export the current QGIS project as an interactive web map using Leaflet.js. "
            "Serializes visible vector layers as GeoJSON and raster tiles as GeoTIFF, "
            "then builds a self-contained index.html with Leaflet.js. "
            "Output: index.html + data/ directory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "output_dir": {"type": "string", "description": "Full path to output directory (created if missing)"},
                "basemap": {"type": "string", "enum": ["osm", "satellite", "topo", "stamen_toner", "none"],
                            "description": "Basemap (default: osm)"},
                "extent": {"type": "string", "description": "Bbox override 'xmin,xmax,ymin,ymax' in project CRS"},
                "include_search": {"type": "boolean", "description": "Add geocoding search box (default: false)"},
                "title": {"type": "string", "description": "HTML page title (default: project name)"},
            },
            "required": ["output_dir"],
        },
        "execute": export_webmap,
    },
    {
        "name": "publish_geoserver",
        "description": (
            "Publish a vector or raster layer to a GeoServer REST endpoint. "
            "Exports to a temp file, uploads via multipart REST PUT, "
            "creates/updates the datastore and publishes the layer for WFS/WMS access."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "description": "QGIS layer name to publish"},
                "workspace": {"type": "string", "description": "GeoServer workspace name (default: 'default')"},
                "geoserver_url": {"type": "string", "description": "GeoServer base URL"},
                "layer_name": {"type": "string", "description": "GeoServer layer name (default: same as QGIS layer)"},
                "username": {"type": "string", "description": "GeoServer admin username"},
                "password": {"type": "string", "description": "GeoServer admin password"},
                "publish_as": {"type": "string", "enum": ["vector", "raster", "auto"],
                               "description": "'auto' (default), 'vector', or 'raster'"},
            },
            "required": ["layer", "geoserver_url", "username", "password"],
        },
        "execute": publish_geoserver,
    },
    {
        "name": "set_layer_style",
        "description": (
            "Apply visual styles (colormaps, RGB bands, graduated or categorized renderers) "
            "to raster and vector layers without writing raw QGIS Python."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "description": "Layer name or ID"},
                "style": {"type": "string", "enum": ["singleband", "multiband", "graduated", "categorized", "paletted"],
                          "description": "Style type"},
                "band": {"type": "number", "description": "Band for singleband (default: 1)"},
                "colormap": {"type": "string", "description": "Colormap: viridis, gray, rdylgn, spectral, terrain"},
                "min": {"type": "number", "description": "Min pixel value for stretch (auto-detected if omitted)"},
                "max": {"type": "number", "description": "Max pixel value for stretch (auto-detected if omitted)"},
                "red": {"type": "number", "description": "Red band index for multiband RGB"},
                "green": {"type": "number", "description": "Green band index for multiband RGB"},
                "blue": {"type": "number", "description": "Blue band index for multiband RGB"},
                "column": {"type": "string", "description": "Attribute column for graduated/categorized"},
                "classes": {"type": "number", "description": "Number of class bins for graduated (default: 5)"},
                "method": {"type": "string", "enum": ["jenks", "equal", "quantile", "std"],
                           "description": "Classification method (default: jenks)"},
                "color_ramp": {"type": "string", "description": "Named colour ramp (Reds, Blues, Spectral, Viridis)"},
                "legend_title": {"type": "string", "description": "Legend header text"},
                "legend_expression": {"type": "string", "description": "Rule-based legend entries separated by '|'"},
            },
            "required": ["layer", "style"],
        },
        "execute": set_layer_style,
    },
    {
        "name": "multi_map_layout",
        "description": (
            "Create a single print-layout PDF with multiple map panels arranged in a grid. "
            "Each panel shows its own layer set and optional extent. "
            "Best for before/after comparisons, multi-temporal overviews."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layout_name": {"type": "string", "description": "Name for the new QgsPrintLayout"},
                "output_path": {"type": "string", "description": "Full path to export (PDF)"},
                "paper_format": {"type": "string", "enum": ["A2", "A3", "A4", "Letter"],
                                 "description": "Default: A3"},
                "orientation": {"type": "string", "enum": ["portrait", "landscape"],
                                "description": "Default: landscape"},
                "grid": {"type": "string", "description": "'rows,cols' e.g. '2,2' (default: auto)"},
                "panels": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Panel label"},
                            "layer_set": {"type": "array", "items": {"type": "string"},
                                          "description": "Layer names to show"},
                            "extent": {"type": "string", "description": "'xmin,xmax,ymin,ymax' in project CRS"},
                        },
                    },
                    "description": "Panels to arrange in the layout",
                },
                "margin_mm": {"type": "number", "description": "Page margin in mm (default: 20)"},
            },
            "required": ["layout_name", "output_path"],
        },
        "execute": multi_map_layout,
    },
    {
        "name": "save_map_theme",
        "description": (
            "Save the current QGIS map theme (layer visibility + renderer state) under a name. "
            "Restore it later with load_map_theme to backtrack without rerunning code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "theme_name": {"type": "string", "description": "Name for the saved theme"},
            },
            "required": ["theme_name"],
        },
        "execute": save_map_theme,
    },
    {
        "name": "load_map_theme",
        "description": (
            "Load a previously saved QGIS map theme: sets layer visibility to the saved state. "
            "The fastest way to reset layer visibility without rerunning anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "theme_name": {"type": "string", "description": "Name of the theme to restore"},
                "refresh": {"type": "boolean", "description": "Redraw canvas after loading (default: true)"},
            },
            "required": ["theme_name"],
        },
        "execute": load_map_theme,
    },
    {
        "name": "list_map_themes",
        "description": (
            "List all saved map themes in the current QGIS project. "
            "Use before load_map_theme to see available themes."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "execute": list_map_themes,
    },
    {
        "name": "refresh_canvas",
        "description": (
            "Refresh the QGIS map canvas and all layers. "
            "Call after set_layer_style, set visibility toggles, layer removals, "
            "or any operation that changes the visual state."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "execute": refresh_canvas,
    },
]
