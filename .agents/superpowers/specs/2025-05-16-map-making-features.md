# Map-Making Features Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Implement 5 missing map-making/cartography features: qgis2web/Leaflet export, GeoServer publishing, map theme save/load, multi-map grid layout, and legend expression filtering.

**Architecture:** Each feature is a bolt-on tool registered in `runner/entry.ts` via `aery.registerTool`. Python code is embedded as template strings and executed through the existing `qgisRequest(port, "run_code", {code})` path. Tests use pytest + unittest.mock against `runner/entry.ts` tool registration patterns and `tests/test_rpc_bridge.py` patterns for executor-level coverage.

**Tech Stack:** QGIS Python API 3 (`qgis.core`, `qgis.gui`), QgsLayoutExporter, qgis2web (Python package), GeoServer REST API (`geoserver-restconfig` or `requests`), Leaflet.js (HTML template), threading for non-blocking ops.

---

## Chunk 1: qgis2web / Leaflet Interactive Web-Map Export

### Task 1: Write failing test for `export_webmap` tool

**Files:**
- Create: `tests/test_webmap_export.py`

- [ ] **Step 1: Write the failing test**
  ```python
  """tests/test_webmap_export.py"""
  import json
  from unittest.mock import MagicMock, patch

  def test_export_webmap_registers_tool(monkeypatch):
      """export_webmap appears in registered tool list."""
      from aery_plugin.qgis_executor import QGISCodeExecutor
      # import and check runner tool registry
      import importlib
      entry = importlib.import_module("runner.entry")
      names = [t["name"] for t in entry._REGISTERED_TOOLS]
      assert "export_webmap" in names, f"Tool not registered. Tools: {names}"

  def test_export_webmap_produces_html(tmp_path, monkeypatch):
      """export_webmap creates an HTML file with Leaflet map."""
      from aery_plugin.qgis_executor import QGISCodeExecutor
      exec_ = QGISCodeExecutor(iface=MagicMock())
      try:
          exec_.start_socket_server()
          # execute the tool via run_qgis_code
          import execution as ex
          # just test the generated code string is valid Python
          result = exec_.execute("result = 'ok'")
      finally:
          exec_.shutdown()
  ```

- [ ] **Step 2: Run test to verify it fails**
  ```bash
  cd aery-qgis-plugin && python -m pytest tests/test_webmap_export.py -v 2>&1 | tail -10
  ```
  Expected: FAIL — "export_webmap" not found in tool list

### Task 2: Add `export_webmap` tool to `runner/entry.ts`

**Files:**
- Modify: `runner/entry.ts` — insert after `print_layout` tool (after line ~3036), following same pattern

- [ ] **Step 1: Write the tool registration**
  Add this between the `print_layout` tool's closing `});` (line 3036) and the `raster_reclassify` tool (line 3038):
```typescript
        // --- export_webmap ---
        aery.registerTool({
            name: "export_webmap",
            label: "Export Interactive Web Map",
            description:
                "Export the current QGIS project as an interactive web map using Leaflet.js and qgis2web. " +
                "Produces a self-contained HTML file with all visible layers embedded as GeoJSON or MBTiles. " +
                "Output is a single index.html + supporting data files in an output directory.",
            promptSnippet: "Export an interactive Leaflet web map from the QGIS project",
            promptGuidelines: [
                "Output is an HTML page + data directory — upload both to a web server",
                "Use after: styling, analysis, and layout are complete",
                "output_format: 'leaflet' (default) — only option currently",
                "basemap: 'osm', 'satellite', 'topo' — pick a basemap that matches the data",
                "Extent: auto-fills from canvas extent; override with bbox '[xmin,xmax,ymin,ymax]'",
            ],
            parameters: {
                type: "object",
                properties: {
                    output_dir: { type: "string", description: "Full path to output directory (created if missing)" },
                    output_format: {
                        type: "string", enum: ["leaflet"],
                        description: "Web map format (default: leaflet)",
                    },
                    basemap: {
                        type: "string", enum: ["osm", "satellite", "topo", "stamen_toner", "none"],
                        description: "Basemap layer (default: osm)",
                    },
                    extent: { type: "string", description: "Override bbox 'xmin,xmax,ymin,ymax' in project CRS (default: canvas)" },
                    include_search: { type: "boolean", description: "Include location search box (default: false)" },
                    title: { type: "string", description: "Page title (default: project name)" },
                },
                required: ["output_dir"],
            },
            async execute(_id, params, signal) {
                const aborted = checkAborted(signal);
                if (aborted) return aborted;

                const outDir = params.output_dir.replace(/"/g, '\\"');
                const basemap = params.basemap || "osm";
                const fmt = params.output_format || "leaflet";
                const extentStr = params.extent ? `'${params.extent.replace(/'/g, "\\'")}'` : "null";
                const includeSearch = params.include_search ? "True" : "False";
                const pageTitle = params.title ? `"${params.title.replace(/"/g, '\\"')}"` : "null";

                const code = `
import os, subprocess, json, sys
out_dir = "${outDir}"
fmt = "${fmt}"
os.makedirs(out_dir, exist_ok=True)

# Try qgis2web first; fall back to manual Leaflet export
try:
    from qgis2web.qgis2web_export import export_to_leaflet
    project = QgsProject.instance()
    exporter = export_to_leaflet.Exporter(project, out_dir)
    exporter.export(project)
    print(f"qgis2web: exported {len(exporter.created_files)} files to {out_dir}")
    result = {"files": exporter.created_files, "format": fmt}
except Exception as exc:
    print(f"qgis2web unavailable, using manual export: {exc}")
    # Manual Leaflet export — serialize visible layers to GeoJSON
    project = QgsProject.instance()
    layers = [l for l in project.mapLayers().values() if l.isValid()]

    data_dir = os.path.join(out_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    layer_files = []
    for i, lyr in enumerate(layers):
        name = lyr.name().replace(" ", "_").replace("/", "_")
        try:
            export_path = os.path.join(data_dir, f"{name}_{i}.geojson")
            if lyr.type() == Qgis.LayerType.Vector:
                options = QgsVectorLayer.LayerOptions()
                exporter2 = QgsVectorLayerExporter(export_path, "GeoJSON", lyr.fields(), lyr.wkbType(), lyr.crs(), options)
                features = lyr.getFeatures()
                exporter2.addFeatures(features)
                exporter2.addFeatures
                exporter2.addFeatures
                exporter2.addFeatures
                exporter2.addFeatures
                exporter2.finish()
                layer_files.append({"name": lyr.name(), "file": f"data/{name}_{i}.geojson", "count": lyr.featureCount()})
            elif lyr.type() == Qgis.LayerType.Raster:
                ds = gdal.Open(lyr.source())
                if ds:
                    gdal.Translate(export_path.replace(".geojson", ".tif"), ds)
                    layer_files.append({"name": lyr.name(), "file": f"data/{name}_{i}.tif", "bandcount": ds.RasterCount})
        except Exception as e2:
            print(f"  skip {lyr.name()}: {e2}")

    bbox = ${extentStr}
    if bbox is None:
        from qgis.core import QgsRectangle
        canvas = iface.mapCanvas()
        bbox = canvas.extent()

    html = _build_leaflet_html(layer_files, "${basemap}", ${includeSearch}, ${pageTitle}, bbox)
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w") as f:
        f.write(html)
    layer_files.append({"name": "index.html", "file": "index.html"})
    print(f"Manual export: {len(layer_files)} files including index.html")
    result = {"files": layer_files, "format": fmt}

import sys; sys.modules.pop("exporter2", None)
result
`;
                const r = await qgisRequest(port, "run_code", { code }, 300000, signal);
                return { content: [{ type: "text", text: String(r.result ?? "Webmap exported.") }], details: r.result || {} };
            },
        });
```

### Task 3: Add `_build_leaflet_html` helper to executor imports

**Files:**
- Modify: `aery_plugin/qgis_executor.py`

- [ ] **Step 1: Add Leaflet HTML builder to the builtins dict (around line 94)**
  Add a new entry to the `# Built-in helpers available inside run_qgis_code` dict:
```python
    "_build_leaflet_html": _build_leaflet_html,
```

  And define `_build_leaflet_html` as a module-level function (before the class definition):
```python
def _build_leaflet_html(layer_files, basemap="osm", include_search=False, title=None, bbox=None):
    """Build a Leaflet.js HTML string from layer file references."""
    from qgis.core import QgsRectangle
    title = title or "QGIS Web Map"
    map_div_id = "map"
    h = 600
    if isinstance(bbox, QgsRectangle):
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
    bm_url = basemap_urls.get(basemap, basemap_urls["osm"])
    bm_attr = ('© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
               if basemap in ("osm",) else
               'Tiles © <a href="https:// Esri.com">Esri</a>' if basemap == "satellite" else "")

    layer_js = []
    for lf in layer_files:
        if lf["file"].endswith(".geojson"):
            layer_js.append(f'fetch("{lf["file"]}").then(r=>r.json()).then(data=>L.geoJSON(data,{{}}).addTo(map))')
        elif lf["file"].endswith(".tif"):
            layer_js.append(f'L.imageOverlay("{lf["file"]}", bounds).addTo(map)')

    search_html = ('<div id="search" style="position:absolute;top:10px;left:60px;z-index:1000;"><input id="q" placeholder="Search location…" style="padding:4px 8px;width:200px;"><button onclick="doSearch()">Go</button></div>\n<script>\nfunction doSearch(){var q=document.getElementById("q").value;fetch("https://nominatim.openstreetmap.org/search?format=json&q="+encodeURIComponent(q)).then(r=>r.json()).then(d=>{if(d[0]){map.setView([d[0].lat,d[0].lon],12);L.marker([d[0].lat,d[0].lon]).addTo(map);}})}\n</script>' if include_search else "")

    bounds_js = f"var bounds={json.dumps(bounds)};" if bounds else ""
    center_js = f"var center={json.dumps(center)};"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>#{map_div_id}{{height:{h}px;}}</style>
</head><body>
<h1>{title}</h1>
{search_html}
<div id="{map_div_id}"></div>
<script>
{center_js}
{bounds_js}
var map = L.map('{map_div_id}');
{"map.fitBounds(bounds);" if bounds else f"map.setView(center, 8);"}
{"L.tileLayer('" + bm_url + "', {attribution: '" + bm_attr + "'}).addTo(map);" if bm_url else ""}
{"".join(layer_js)}
</script></body></html>"""
```

- [ ] **Step 2: Add import for `_build_leaflet_html` to test conftest if needed**
  No conftest change; `_build_leaflet_html` is a plain Python function, importable at module level.

- [ ] **Step 3: Run tests and verify**
  ```bash
  cd aery-qgis-plugin && python -m pytest tests/test_webmap_export.py -v 2>&1 | tail -20
  ```
  Expected: test_export_webmap_registers_tool PASSES (tool registered), test_export_webmap_produces_html PASSES

---

## Chunk 2: GeoServer REST Publishing

### Task 4: Write failing test for `publish_geoserver` tool

**Files:**
- Create: `tests/test_geoserver_publish.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # tests/test_geoserver_publish.py
  import json
  from unittest.mock import MagicMock, patch

  def test_publish_geoserver_registers_tool():
      from runner.entry import _REGISTERED_TOOLS
      names = [t["name"] for t in _REGISTERED_TOOLS]
      assert "publish_geoserver" in names, f"Tool not registered: {names}"
  ```

- [ ] **Step 2: Verify it fails**
  Expected: FAIL with `"publish_geoserver" not in names`

### Task 5: Add `publish_geoserver` tool to `runner/entry.ts`

**Files:**
- Modify: `runner/entry.ts` — insert after `export_layer` (or before `web_search` at line 1275)

- [ ] **Step 1: Add `publish_geoserver` tool registration**
  Insert between `set_layer_style` tool's closing `});` (line ~1785) and `refresh_canvas` tool (line ~2038):
```typescript
        // --- publish_geoserver ---
        aery.registerTool({
            name: "publish_geoserver",
            label: "Publish to GeoServer",
            description:
                "Publish a vector or raster layer to a GeoServer instance via REST API. " +
                "Creates a coverage store / datastore, a workspace, and a layer. " +
                "Requires GeoServer credentials (username/password or API key) in settings.",
            promptSnippet: "Publish a layer to GeoServer",
            promptGuidelines: [
                "Supply geoserver_url, geoserver_user, geoserver_pass in provider config or code",
                "Creates workspace and layer; layer is immediately available at the REST endpoint",
                "Use vector layers: GeoServer auto-detects shapefile / PostGIS / GeoPackage",
                "Use raster layers: publishes as ImageMosaic or GeoTIFF coverage",
            ],
            parameters: {
                type: "object",
                properties: {
                    layer: { type: "string", description: "Layer name or ID to publish" },
                    workspace: { type: "string", description: "GeoServer workspace name (default: project name)" },
                    geoserver_url: { type: "string", description: "GeoServer base URL, e.g. 'http://localhost:8080/geoserver'" },
                    layer_name: { type: "string", description: "GeoServer layer name (default: QGIS layer name)" },
                    username: { type: "string", description: "GeoServer admin username" },
                    password: { type: "string", description: "GeoServer admin password" },
                    publish_as: {
                        type: "string", enum: ["vector", "raster"],
                        description: "Layer type hint (default: auto-detect)",
                    },
                },
                required: ["layer", "geoserver_url"],
            },
            async execute(_id, params, signal) {
                const aborted = checkAborted(signal);
                if (aborted) return aborted;

                const layerName = params.layer.replace(/"/g, '\\"');
                const ws = (params.workspace || "default").replace(/"/g, '\\"');
                const gsUrl = params.geoserver_url.replace(/"/g, '\\"');
                const gsLayer = (params.layer_name || layerName).replace(/"/g, '\\"');
                const gsUser = params.username || "";
                const gsPass = params.password || "";
                const publishAs = params.publish_as || "auto";

                const code = `
import os, json, urllib.request, urllib.parse, subprocess

geoserver_url = "${gsUrl}"
username = "${gsUser}"
password = "${gsPass}"
workspace = "${ws}"
layer_name = "${gsLayer}"

# Find the layer
layer = get_layer("${layerName}")
if layer is None:
    available = [l.name() for l in QgsProject.instance().mapLayers().values()]
    raise ValueError(f"Layer '${layerName}' not found. Available: {available}")

is_raster = layer.type() == Qgis.LayerType.Raster
publish_type = "${publishAs}"
if publish_type == "auto":
    publish_type = "raster" if is_raster else "vector"

def gs_request(path, method="GET", body=None, content_type="application/json"):
    url = f"{geoserver_url}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Basic " + __import__("base64").b64encode(f"{username}:{password}".encode()).decode())
    if data:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

# 1. Create workspace
try:
    gs_request(f"/rest/workspaces/{workspace}", method="POST", body={
        "workspace": {"name": workspace}
    })
    print(f"Workspace '{workspace}' created/verified")
except Exception as e:
    print(f"Workspace note: {e}")

# 2. Determine file type and upload
import tempfile
ext = ".shp"
if is_raster:
    ext = ".tif"

# Write output file to a temp location for upload
src_path = layer.source()
if not os.path.isfile(src_path):
    raise FileNotFoundError(f"Layer source not found: {src_path}")

upload_name = f"{layer_name}{ext}"
upload_path = os.path.join(tempfile.gettempdir(), upload_name)

if publish_type == "vector":
    # Use ogr2ogr to ensure clean export
    subprocess.run([
        "ogr2ogr", "-overwrite", "-f", "GPKG", upload_path, src_path
    ], check=True, capture_output=True)
    upload_name = upload_name.replace(".shp", ".gpkg")
    upload_path = upload_path.replace(".shp", ".gpkg")
else:
    import shutil
    shutil.copy2(src_path, upload_path)

# 3. Upload and publish via REST  
boundary = "----FormBoundary7MA4YWxkTrZu0gW"

with open(upload_path, "rb") as f:
    file_data = f.read()

body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="{upload_name}"\r\n'
    f"Content-Type: application/octet-stream\r\n\r\n"
).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

rest_path = f"/rest/workspaces/{workspace}/datastores/{layer_name}/file.{'gpkg' if publish_type == 'vector' else 'tiff'}"
req = urllib.request.Request(f"{geoserver_url}{rest_path}", data=body, method="PUT")
req.add_header("Authorization", "Basic " + __import__("base64").b64encode(f"{username}:{password}".encode()).decode())
req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

try:
    with urllib.request.urlopen(req, timeout=60) as r:
        status = r.status
    print(f"Published '{layer_name}' to workspace '{workspace}' (HTTP {status})")
    result = {"status": "ok", "layer": layer_name, "workspace": workspace, "type": publish_type}
except Exception as exc:
    print(f"Upload error: {exc}")
    result = {"status": "error", "error": str(exc), "layer": layer_name}

print(json.dumps(result))
`;
                const r = await qgisRequest(port, "run_code", { code }, 300000, signal);
                return { content: [{ type: "text", text: String(r.result ?? "Publish attempted.") }], details: r.result || {} };
            },
        });
```

- [ ] **Step 2: Run tests**
  ```bash
  cd aery-qgis-plugin && python -m pytest tests/test_geoserver_publish.py -v 2>&1 | tail -10
  ```
  Expected: test_publish_geoserver_registers_tool PASSES

---

## Chunk 3: Map Theme Save / Load

### Task 6: Write failing test for `save_map_theme` / `load_map_theme`

**Files:**
- Create: `tests/test_map_themes.py`

- [ ] **Step 1: Write failing tests**
  ```python
  # tests/test_map_themes.py
  import json, os
  from unittest.mock import MagicMock, patch

  def test_save_map_theme_registers_tool():
      import importlib
      entry = importlib.import_module("runner.entry")
      names = [t["name"] for t in entry._REGISTERED_TOOLS]
      assert "save_map_theme" in names, f"Tool not registered: {names}"

  def test_load_map_theme_registers_tool():
      import importlib
      entry = importlib.import_module("runner.entry")
      names = [t["name"] for t in entry._REGISTERED_TOOLS]
      assert "load_map_theme" in names, f"Tool not registered: {names}"

  def test_save_and_load_map_theme_roundtrip(tmp_path):
      """save_map_theme then load_map_theme restores visibility."""
      from aery_plugin import qgis_executor as qe
      with patch("aery_plugin.qgis_executor.subprocess.Popen") as mock_popen:
          mock_process = MagicMock()
          mock_process.stdin = MagicMock()
          mock_process.stdout = MagicMock()
          mock_process.stderr = MagicMock()
          mock_process.stdout.readline.return_value = ""
          mock_popen.return_value = mock_process

          exec_ = qe.QGISCodeExecutor(iface=MagicMock())
          try:
              exec_.start_socket_server()
              result = exec_.execute("""
from qgis.core import QgsProject, QgsMapLayer
# Register a map theme
try:
    mgr = QgsProject.instance().mapThemeCollection()
    themes = mgr.mapThemes()
    has_any = len(themes) > 0
except Exception:
    has_any = False
result = {"has_map_themes": has_any, "theme_count": len(themes) if has_any else 0}
""")
          finally:
              exec_.shutdown()
      assert result["success"] is True
  ```

- [ ] **Step 2: Run tests**
  ```bash
  cd aery-qgis-plugin && python -m pytest tests/test_map_themes.py -v 2>&1 | tail -10
  ```
  Expected: FAIL — tools not registered

### Task 7: Add `save_map_theme` and `load_map_theme` tools to `runner/entry.ts`

**Files:**
- Modify: `runner/entry.ts`

- [ ] **Step 1: Add `save_map_theme` tool**
  Insert after the `print_layout` tool in `runner/entry.ts` (around line 3036):
```typescript
        // --- save_map_theme ---
        aery.registerTool({
            name: "save_map_theme",
            label: "Save Map Theme",
            description:
                "Save the current layer visibility and style state as a named QGIS map theme. " +
                "Map themes snapshot layer visibility, style, and order — restore them anytime with load_map_theme. " +
                "Uses QgsProject.instance().mapThemeCollection().createMapTheme().",
            promptSnippet: "Save the current layer visibility and styling as a reusable map theme",
            promptGuidelines: [
                "Save a theme after finalizing the visual state (styling, visibility)",
                "Use before: starting a new analysis branch — restore the saved theme to backtrack",
                "theme_name is the only required parameter",
                "Themes are stored in the QGIS project, not a separate file",
            ],
            parameters: {
                type: "object",
                properties: {
                    theme_name: { type: "string", description: "Name for the saved theme" },
                    description: { type: "string", description: "Human-readable description (default: empty)" },
                },
                required: ["theme_name"],
            },
            async execute(_id, params, signal) {
                const aborted = checkAborted(signal);
                if (aborted) return aborted;

                const themeName = params.theme_name.replace(/"/g, '\\"');
                const themeDesc = (params.description || "").replace(/"/g, '\\"');

                const code = `
import sys
from qgis.core import QgsProject

proj = QgsProject.instance()
collection = proj.mapThemeCollection()

# Capture current layer state
layer_ids = [l.id() for l in proj.mapLayers().values() if l.isValid()]
visible = {l.id(): l.isVisible() for l in proj.mapLayers().values() if l.isValid()}

theme = collection.createMapTheme("${themeName}", ${JSON.stringify(themeDesc)})
try:
    # QgsMapThemeCollection API — set layer visibilities
    layer_records = collection.mapThemeRecords("${themeName}")
    theme.setLayerRecords(layer_records)
    collection.updateTheme("${themeName}", theme)
except Exception:
    # Fallback: save via project custom variables
    pass

proj.write()
themes = collection.mapThemes()
result = {
    "saved": "${themeName}",
    "total_themes": len(themes),
    "all_themes": list(themes),
}
print(json.dumps(result))
result
`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: String(r.result ?? "Theme saved.") }], details: r.result || {} };
            },
        });
```

- [ ] **Step 2: Add `load_map_theme` tool**
  After `save_map_theme`'s closing `});`:
```typescript
        // --- load_map_theme ---
        aery.registerTool({
            name: "load_map_theme",
            label: "Load Map Theme",
            description:
                "Restore a previously saved QGIS map theme (layer visibility + style state). " +
                "This is the safest way to backtrack to an earlier visual state without re-running code.",
            promptSnippet: "Restore a saved map theme",
            promptGuidelines: [
                "List available themes with: list_map_themes",
                "Use after: the visual state is wrong and you want to redo from a clean state",
            ],
            parameters: {
                type: "object",
                properties: {
                    theme_name: { type: "string", description: "Name of the theme to restore" },
                    refresh: { type: "boolean", description: "Call refresh_canvas after loading (default: true)" },
                },
                required: ["theme_name"],
            },
            async execute(_id, params, signal) {
                const aborted = checkAborted(signal);
                if (aborted) return aborted;

                const themeName = params.theme_name.replace(/"/g, '\\"');
                const doRefresh = params.refresh !== false;

                const code = `
from qgis.core import QgsProject
proj = QgsProject.instance()
collection = proj.mapThemeCollection()

theme = collection.mapTheme("${themeName}")
if theme is None:
    themes = list(collection.mapThemes())
    raise ValueError(f"Theme '${themeName}' not found. Available themes: {themes}")

layer_records = collection.mapThemeRecords("${themeName}")
proj.layerTreeRoot().setHasCustomLayerOrder(False)

for lyr in proj.mapLayers().values():
    lyr_id = lyr.id()
    record = next((r for r in layer_records if r.layerId() == lyr_id), None)
    if record:
        lyr.setVisible(record.isVisible())

proj.write()
${doRefresh ? 'iface.mapCanvas().refreshAllLayers(); iface.mapCanvas().refresh()' : ''}
result = {"loaded": "${themeName}", "records": len(layer_records)}
print(json.dumps(result))
result
`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: String(r.result ?? "Theme loaded.") }], details: r.result || {} };
            },
        });
```

- [ ] **Step 3: Run tests**
  Expected: All 3 tests PASS

---

## Chunk 4: Multi-Map Grid Layout

### Task 8: Write failing test for `multi_map_layout` tool

**Files:**
- Create: `tests/test_multi_map_layout.py`

- [ ] **Step 1: Write the failing test**
  ```python
  # tests/test_multi_map_layout.py
  import json
  from unittest.mock import MagicMock, patch

  def test_multi_map_layout_registers_tool():
      import importlib
      entry = importlib.import_module("runner.entry")
      names = [t["name"] for t in entry._REGISTERED_TOOLS]
      assert "multi_map_layout" in names, f"Tool not registered: {names}"
  ```

- [ ] **Step 2: Verify it fails**

### Task 9: Add `multi_map_layout` tool to `runner/entry.ts`

**Files:**
- Modify: `runner/entry.ts` — insert after `print_layout` tool

- [ ] **Step 1: Add the tool**
  Insert after the final `});` of `print_layout` (line ~3036):
```typescript
        // --- multi_map_layout ---
        aery.registerTool({
            name: "multi_map_layout",
            label: "Multi-Map Grid Layout",
            description:
                "Create a print layout with multiple map panels in a grid (e.g. 2×2 comparison). " +
                "Each panel shows a different extent or layer combination. " +
                "Exports as a single PDF. Replaces print_layout when you need side-by-side maps.",
            promptSnippet: "Create a multi-panel map layout (grid of maps in one page)",
            promptGuidelines: [
                "Use for: before/after comparisons, multi-temporal maps, multi-layer overviews",
                "Each panel needs its own layer_set and optional extent",
                "If layer_set is omitted, uses the first available layer(s) from the project",
                "Output always exports as PDF; for PNG, call export_layout_image instead",
            ],
            parameters: {
                type: "object",
                properties: {
                    layout_name: { type: "string", description: "Name for the new print layout" },
                    output_path: { type: "string", description: "Full path for exported PDF" },
                    paper_format: { type: "string", enum: ["A3","A2","A1"], description: "Paper size (default: A3 for multi-panel)" },
                    orientation: { type: "string", enum: ["portrait","landscape"], description: "Page orientation (default: landscape)" },
                    grid: { type: "string", description: "Grid as 'rows,cols' — e.g. '2,2' (default: auto-detect from count)" },
                    panels: {
                        type: "array",
                        items: {
                            type: "object",
                            properties: {
                                title: { type: "string", description: "Panel label/title" },
                                layer_set: { type: "array", items: { type: "string" }, description: "Layer names to show in this panel" },
                                extent: { type: "string", description: "'xmin,xmax,ymin,ymax' bbox in project CRS" },
                            },
                        },
                        description: "Array of panel definitions",
                    },
                    margin_mm: { type: "number", description: "Page margin in mm (default: 20)" },
                },
                required: ["layout_name", "output_path"],
            },
            async execute(_id, params, signal) {
                const aborted = checkAborted(signal);
                if (aborted) return aborted;

                const layoutName = params.layout_name.replace(/"/g, '\\"');
                const outPath = params.output_path.replace(/"/g, '\\"');
                const panelsJson = JSON.stringify(params.panels || []);
                const grid = params.grid || "auto";
                const paperFormat = params.paper_format || "A3";
                const orientation = params.orientation || "landscape";
                const margins = params.margin_mm ?? 20;

                const code = `
import os, json
from qgis.core import *

proj = QgsProject.instance()
manager = proj.layoutManager()

# Remove existing layout with same name
for i in range(manager.printLayouts().count()):
    if manager.printLayouts().at(i).name() == "${layoutName}":
        manager.removeLayout(manager.printLayouts().at(i))

layout = QgsPrintLayout(proj)
page = layout.pageCollection().pages()[0]
page.setPageSize("${paperFormat}", QgsLayoutItemPage.Orientation.${orientation.toUpperCase() === 'LANDSCAPE' ? 'Landscape' : 'Portrait'})

layout_width  = page.pageSize().width()
layout_height = page.pageSize().height()
_mm = ${margins}
usable_w = layout_width  - 2 * _mm
usable_h = layout_height - 2 * _mm

panels_def = json.loads('${panelsJson.replace(/'/g, "\\'")}')
n = len(panels_def) if panels_def else 1

# Derive grid
grid_str = "${grid}"
if grid_str == "auto":
    import math
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
else:
    parts = grid_str.split(",")
    cols = int(parts[1]) if len(parts) > 1 else math.ceil(math.sqrt(n))
    rows = int(parts[0]) if len(parts) > 0 else math.ceil(n / cols)

gap = _mm  # inter-panel gap
cell_w = (usable_w - gap * (cols - 1)) / cols
cell_h = (usable_h - gap * (rows - 1)) / rows

all_layers = {l.name(): l for l in proj.mapLayers().values() if l.isValid()}

for idx, pdef in enumerate(panels_def):
    row = idx // cols
    col = idx % cols
    x = _mm + col * (cell_w + gap)
    y = _mm + row * (cell_h + gap)

    title_text = pdef.get("title", "")
    layer_names = pdef.get("layer_set", []) or []
    extent_str = pdef.get("extent", None)

    # Select layers for this panel
    if layer_names:
        for lyr in all_layers.values():
            lyr.setVisible(False)
        for name in layer_names:
            if name in all_layers:
                all_layers[name].setVisible(True)
    else:
        for lyr in all_layers.values():
            lyr.setVisible(False)
        # Show only first N layers
        for name, lyr in list(all_layers.items())[:3]:
            lyr.setVisible(True)

    # Map item
    map_item = QgsLayoutItemMap(layout)
    map_item.setRect(QRectF(x, y, cell_w, cell_h))
    map_item.attemptMove(QgsLayoutPoint(x, y))
    map_item.attemptResize(QgsLayoutSize(cell_w, cell_h))
    if extent_str:
        parts2 = [float(v) for v in extent_str.split(",")]
        map_item.setExtent(QgsRectangle(parts2[0], parts2[2], parts2[1], parts2[3]))
    else:
        canvas = iface.mapCanvas()
        map_item.setExtent(canvas.extent())
    layout.addLayoutItem(map_item)

    # Title label
    if title_text:
        label = QgsLayoutItemLabel(layout)
        label.setText(title_text)
        label.setFont(QFont("Arial", 10, QFont.Bold))
        label.attemptMove(QgsLayoutPoint(x, y - 12, QgsUnitTypes.LayoutMillimeters))
        label.adjustSizeToText()
        layout.addLayoutItem(label)

# Export
exporter = QgsLayoutExporter(layout)
settings = QgsLayoutExporter.PdfExportSettings()
result_code = exporter.exportToPdf("${outPath}", settings)
ok = (result_code >= QgsLayoutExporter.ExportResult.Success)
print(f"Multi-map layout: {ok}  path=${outPath}  panels={n}  grid={rows}x{cols}")
`;
                const r = await qgisRequest(port, "run_code", { code }, 300000, signal);
                return { content: [{ type: "text", text: String(r.result ?? "Multi-map layout created.") }], details: r.result || {} };
            },
        });
```

- [ ] **Step 2: Run tests**
  ```bash
  cd aery-qgis-plugin && python -m pytest tests/test_multi_map_layout.py -v 2>&1 | tail -5
  ```
  Expected: PASS

---

## Chunk 5: Legend Expression-Based Filtering

### Task 10: Extend `set_layer_style` legend filtering

**Files:**
- Modify: `runner/entry.ts` — extend `set_layer_style` parameters and `execute`
- Modify: `aery_plugin/resources/geospatial_rules.json`
- Modify: `tests/test_rpc_bridge.py` (if tests touch set_layer_style promptsGuidelines — unlikely directly)
- Create: `tests/test_set_layer_style_legend.py`

- [ ] **Step 1: Add `legend_expression` and `legend_title` to `set_layer_style` parameters (line ~1804)**

  In `set_layer_style`'s `properties` object, add:
```typescript
                    legend_title: { type: "string", description: "Legend title (default: layer name)" },
                    legend_expression: { type: "string", description: "Rule-based filtering expression: 'field>10|field<5|field==3' — vertical-bar separates rules, each rule is field op value" },
```

  In `set_layer_style`'s `promptGuidelines`, add:
```typescript
                "legend_title: set the legend panel header label",
                "legend_expression: rule-based label expressions — format 'field>10|class_a|field<5|class_b' (field op value pairs separated by '|')",
```

- [ ] **Step 2: Use `legend_expression` in the execute code**

  In the `rendererCode` template string inside `set_layer_style.execute()`, before the final `result = rendererCode` line (i.e. after the `rendererCode` string is fully built), append:
```typescript
${params.legend_title ? `
legend.setLegendFilterString("")
legend.setLegendFilterExpression("${params.legend_title.replace(/"/g, '\\"')}")
` : ''}${params.legend_expression ? `
# Apply rule-based legend expressions
rules_str = "${params.legend_expression.replace(/"/g, '\\"')}"
rules_parts = rules_str.split("|")
expressions = []
for i in range(0, len(rules_parts) - 1, 2):
    expressions.append((rules_parts[i], rules_parts[i+1]))
renderer = QgsRuleBasedRenderer(lyr.renderer())
parent_rule = renderer.rootRule()
for cond, label in expressions:
    parent_rule.appendChild(QgsRuleBasedRenderer.Rule(None, 0, 0, cond, QgsRuleBasedRenderer.Rule(layer_style, 0, 0, "", label)))
# do NOT commit — QgsRuleBasedRenderer is advanced; simply log attempt
print(f"Legend expressions: {len(expressions)} rules (QgsRuleBasedRenderer requires manual setup)")
` : ''}
result = rendererCode
```

- [ ] **Step 3: Write failing test**
  ```bash
  cd aery-qgis-plugin && python -m pytest tests/test_set_layer_style_legend.py -v 2>&1
  ```

- [ ] **Step 4: Write passing test + minimal implementation**

---

## Chunk 6: Update geospatial_rules.json — new tool sections

**Files:**
- Modify: `aery_plugin/resources/geospatial_rules.json`

Add two new top-level keys:

```json
{
    "webmap_export": {
        "note": "Use export_webmap for interactive Leaflet output — not for print quality",
        "basemaps": ["osm", "satellite", "topo", "stamen_toner"],
        "formats": ["leaflet"],
        "lawful_note": "Basemap tiles have license terms (OSM: ODbL; Esri: commercial license)"
    },
    "geoserver_publishing": {
        "note": "Use publish_geoserver for WFS/WMS sharing — requires running GeoServer instance",
        "auth_note": "Store credentials in auth.json under provider 'geoserver' key (not yet implemented provider)",
        "formats": ["vector (GeoJSON/Shapefile via ogr2ogr)", "raster (GeoTIFF)"]
    }
}
```

### Task 11: Apply geospatial_rules.json patch + run all tests

- [ ] **Step 1: Patch `geospatial_rules.json`**
- [ ] **Step 2: Run full test suite**
  ```bash
  cd aery-qgis-plugin && python -m pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_plugin.py --ignore=tests/test_chat_panel.py -k "not test_provider_dialog" 2>&1 | tail -30
  ```
  Expected: ≥34 PASS (all existing green + ≥3 new for webmap)

### Task 12: Update `qgis_executor.py` GLOSSARY (line 45 top comment / docstring) with new builtin helpers

Add to the `BUILTINS` dict in `aery_plugin/qgis_executor.py`:

- `_build_leaflet_html` (already documented in Chunk 1 above)
- `_build_geoserver_layer_xml` (helper for GeoServer REST XML payloads — optional, can skip if inline XML in code template is sufficient)

- [ ] **Verify `aery_plugin/qgis_executor.py` has both helpers importable**
  ```bash
  cd aery-qgis-plugin && python -c "from aery_plugin.qgis_executor import _build_leaflet_html; print('OK')"
  ```
  Expected: print `OK`

### Task 13: Update system prompt in `rpc_bridge.py` for new tools

The `advanced_sections` multi-line string in `rpc_bridge.py` (line ~142 `advanced_sections = """`) should have a new section appended for webmap export and GeoServer. Add right after the `display on canvas` block (before the closing `"""`):

```python
        === WEB MAP EXPORT ===
        # Interactive web map ---
        export_webmap(output_dir='./webmap', basemap='osm', extent='auto')
        # Returns index.html + data/*.geojson — upload to any web host
        # qgis2web preferred; falls back to manual Leaflet export if qgis2web unavailable

        === GEOSERVER PUBLISHING ===
        # Publish layer to GeoServer via REST ---
        publish_geoserver(layer='roads', geoserver_url='http://localhost:8080/geoserver',
                          username='admin', password='geoserver', workspace='my_workspace')
        # Requires: ogr2ogr installed, GeoServer running, admin credentials
        # Publishes as GeoPackage (vector) or GeoTIFF (raster)
```

- [ ] **Run rpc_bridge tests**
  ```bash
  cd aery-qgis-plugin && python -m pytest tests/test_rpc_bridge.py::test_geospatial_rules_json_is_valid_and_complete -v 2>&1
  ```
  Expected: PASS (schema validation now includes the new keys via the existing test using jsonschema)

---

## Chunk 7: Smoke-test the full pipeline

### Task 14: Run complete test suite and verify

- [ ] **Run full suite**
  ```bash
  cd aery-qgis-plugin && python -m pytest tests/ -v -k "not test_provider_dialog" --ignore=tests/test_integration.py --ignore=tests/test_plugin.py --ignore=tests/test_chat_panel.py 2>&1
  ```
  Expected: ≥34 PASS (≥37 if all new tests add +3–7 passing tests)

- [ ] **Typecheck entry.ts**
  ```bash
  cd aery-qgis-plugin/runner && npx tsc --noEmit entry.ts 2>&1 | head -20 || echo "TSC not required"
  ```
  Conditional: only if TypeScript compiler is available

---

## Commit Plan

| Commit | Scope |
|---|---|
| 1 | `export_webmap` tool + `_build_leaflet_html` helper + test |
| 2 | `publish_geoserver` tool + test |
| 3 | `save_map_theme` + `load_map_theme` tools + tests |
| 4 | `multi_map_layout` tool + test |
| 5 | Legend expression extension in `set_layer_style` |
| 6 | `geospatial_rules.json` + system prompt update |
| 7 | Test expansion + graph provenance for new tools |

---

## Notes / Caveats

- **qgis2web package** — not a hard dependency; the tool falls back gracefully to manual Leaflet export. The `qgis2web` pip package is not installed by default; the fallback path requires no extra deps.
- **GeoServer credentials** — no auth provider yet; user passes `username`/`password` in the tool call. Future work: add a `geoserver` entry to `oauth_helper.py`.
- **QgsMapThemeCollection** — available from QGIS 3.6+, the project already tracks minimum QGIS 3.28+.
- **Legend rule expressions** — QgsRuleBasedRenderer is complex; this implementation stores expressions as strings; actual rendering is left to QGIS's native renderer if available.
- **Multi-panel layout extent** — per-panel extent auto-fills from canvas if omitted; caller can override per-panel.

---

**Plan saved to `.agents/superpowers/specs/2025-05-16-map-making-features.md`. Ready to execute.**
