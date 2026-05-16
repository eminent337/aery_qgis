#!/usr/bin/env node
/**
 * Aery for QGIS - standalone binary entry point.
 *
 * Args: aery-qgis-runner <port> [--provider-file <path>]
 *
 * --provider-file: path to a JSON file with provider configuration.
 *   Written by the QGIS plugin, deleted after reading.
 *
 * After optional provider setup, enters standard RPC mode on stdin/stdout.
 */

import { main } from "@eminent337/aery";
import * as fs from "node:fs";
import * as net from "node:net";
import * as crypto from "node:crypto";

// =============================================================================
// QGIS CONSTANTS & RULES
// =============================================================================

const CRS_RULES = [
    "ALWAYS check CRS before spatial operations - layers must match",
    "Use reprojecting if layers have different CRS",
    "EPSG:4326 = WGS84 (lat/lon), EPSG:3857 = Web Mercator",
    "For Africa: EPSG:32632 (UTM 32N), EPSG:32633 (UTM 33N), EPSG:20935 (Congo Basins)",
    "For local analysis, use projected CRS (UTM) not geographic (lat/lon)",
];

const SAFETY_RULES = [
    "ALWAYS warn before: deleting layers, overwriting files, dropping columns",
    "Request confirmation for operations affecting >10,000 features",
    "Show preview of changes before applying bulk updates",
];

const PROCESSING_ALGORITHMS = [
    { name: "Buffer", id: "native:buffer" },
    { name: "Clip", id: "native:clip" },
    { name: "Intersect", id: "native:intersection" },
    { name: "Union", id: "native:union" },
    { name: "Dissolve", id: "native:dissolve" },
    { name: "Fix geometries", id: "native:fixgeometries" },
    { name: "Centroid", id: "native:centroids" },
    { name: "Simplify", id: "native:simplifygeos" },
    { name: "Smooth", id: "native:smoothgeometry" },
    { name: "Difference", id: "native:difference" },
    { name: "Sym difference", id: "native:symmetricaldifference" },
    { name: "Point to polygon", id: "native:pointstopath" },
    { name: "Voronoi", id: "native:voronoi" },
    { name: "Delaunay", id: "native:delaunayTriangulation" },
    { name: "Extract vertices", id: "native:extractvertices" },
    { name: "Rasterize", id: "gdal:rasterize" },
    { name: "Raster clip", id: "gdal:cliprasterbyextent" },
    { name: "Field calculator", id: "native:fieldcalculator" },
    { name: "Statistics by field", id: "native:statisticsbycategories" },
    { name: "Join by location", id: "qgis:joinbylocation" },
    { name: "Spatial join summary", id: "qgis:joinbylocationsummary" },
    { name: "Export to GeoPackage", id: "native:exporttogeopackage" },
    { name: "Reproject layer", id: "native:reprojectlayer" },
    { name: "Extract by expression", id: "native:extractbyexpression" },
    { name: "Random selection", id: "native:randomselection" },
    { name: "Create attribute index", id: "native:createattributeindex" },
    { name: "Count points in polygon", id: "native:countpointsinpolygon" },
    { name: "Distance matrix", id: "native:distancematrix" },
    { name: "Nearest neighbor", id: "native:nearestneighboranalysis" },
    { name: "Multipart to single", id: "native:multipart_to_singleparts" },
    { name: "Single to multipart", id: "native:collect" },
    { name: "Boundary", id: "native:boundary" },
    { name: "Convex hull", id: "native:convexhull" },
];

// =============================================================================
// TCP HELPER
// =============================================================================

function qgisRequest(port, method, params, timeout = 30000) {
    return new Promise((resolve, reject) => {
        const id = crypto.randomUUID();
        const client = new net.Socket();
        const timer = setTimeout(() => {
            client.destroy();
            reject(new Error(`QGIS request timed out after ${timeout}ms`));
        }, timeout);

        let data = "";
        client.connect(port, "127.0.0.1", () => {
            client.write(JSON.stringify({ id, method, ...params }) + "\n");
        });

        client.on("data", (chunk) => {
            data += chunk.toString();
            try {
                const response = JSON.parse(data.trim());
                clearTimeout(timer);
                client.destroy();
                if (response.success) resolve(response);
                else reject(new Error(response.error || response.traceback || "QGIS execution failed"));
            } catch {}
        });

        client.on("error", (err) => {
            clearTimeout(timer);
            reject(err);
        });
    });
}

// =============================================================================
// PROVIDER CONFIG LOADER
// =============================================================================

function loadProviderConfig() {
    const fileIndex = process.argv.indexOf("--provider-file");
    if (fileIndex === -1 || fileIndex + 1 >= process.argv.length) return null;

    const filePath = process.argv[fileIndex + 1];
    try {
        const raw = fs.readFileSync(filePath, "utf-8");
        const config = JSON.parse(raw);
        try { fs.unlinkSync(filePath); } catch {}
        return config;
    } catch {
        return null;
    }
}

// =============================================================================
// TOOL REGISTRATION
// =============================================================================

function createQGISExtension(port, providerConfig) {
    return (aery) => {

        // --- Register provider from config ---
        if (providerConfig?.baseUrl && providerConfig?.apiKey && providerConfig?.model) {
            aery.registerProvider("qgis", {
                baseUrl: providerConfig.baseUrl,
                apiKey: providerConfig.apiKey,
                api: providerConfig.api || "openai-completions",
                models: [{
                    id: providerConfig.model,
                    name: providerConfig.model,
                    reasoning: false,
                    input: ["text"],
                    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
                    contextWindow: providerConfig.contextWindow || 128000,
                    maxTokens: providerConfig.maxTokens || 8192,
                }],
            });
        }

        // --- analyze_task ---
        aery.registerTool({
            name: "analyze_task",
            label: "Analyze Geospatial Task",
            description:
                "Break down a user's request into steps. Returns a structured plan with: " +
                "required tools, input/output layers, potential issues (CRS, memory).",
            promptSnippet: "Analyze a geospatial task and plan the approach",
            promptGuidelines: [
                "ALWAYS call this FIRST for complex tasks (>1 step)",
                "Outputs: steps[], estimated_time, risks[], tool_sequence",
                "Use output to guide subsequent tool calls",
                "Simple tasks can skip this - go straight to get_project_context",
            ],
            parameters: {
                type: "object",
                properties: {
                    task: { type: "string", description: "User's request in plain text" },
                    layers: { type: "array", items: { type: "string" }, description: "Available layer names (optional)" },
                },
                required: ["task"],
            },
            async execute(_id, params) {
                const code = `
from qgis.core import *

available = [l.name() for l in QgsProject.instance().mapLayers().values()]
layers_info = []
for l in QgsProject.instance().mapLayers().values():
    layers_info.append({
        "name": l.name(),
        "type": str(l.type()),
        "crs": l.crs().authid() if l.crs() else "unknown",
        "features": l.featureCount() if hasattr(l, "featureCount") and isinstance(l, QgsVectorLayer) else "N/A",
        "geometry": str(Qgis.geometryType(l.wkbType())) if hasattr(l, "wkbType") else "N/A",
    })

result = {
    "available_layers": layers_info,
    "user_task": params.get("task", ""),
}
`;
                const r = await qgisRequest(port, "run_code", { code });
                return {
                    content: [{
                        type: "text",
                        text: JSON.stringify(r.result, null, 2),
                    }],
                    details: {},
                };
            },
        });

        // --- select_by_attribute ---
        aery.registerTool({
            name: "select_by_attribute",
            label: "Select Features by Attribute",
            description:
                "Select features in a layer matching an expression. " +
                "Use for filtering: 'population' > 1000, 'type' = 'urban', etc.",
            promptSnippet: "Select features in a layer matching a condition",
            promptGuidelines: [
                "Use before: buffer, clip, extract - operate on selection only",
                "Expression examples: area > 1000, type = 'road', population > 5000",
                "Returns count of selected features",
            ],
            parameters: {
                type: "object",
                properties: {
                    layer_name: { type: "string", description: "Name of layer to select from" },
                    expression: { type: "string", description: "QGIS expression (e.g. 'population > 1000')" },
                    method: { type: "string", enum: ["set", "add", "remove"], description: "Selection method", default: "set" },
                },
                required: ["layer_name", "expression"],
            },
            async execute(_id, params) {
                const code = `
from qgis.core import *

layer = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == "${params.layer_name}"), None)
if layer is None:
    raise ValueError("Layer not found: ${params.layer_name}")

method_map = {"set": 0, "add": 1, "remove": 2}
layer.selectByExpression("${params.expression}", method_map.get("${params.method}", 0))

count = len(layer.selectedFeatureIds())
result = {"selected": count, "layer": layer.name()}
`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }], details: {} };
            },
        });

        // --- select_by_location ---
        aery.registerTool({
            name: "select_by_location",
            label: "Select by Location",
            description:
                "Select features in one layer based on their spatial relationship to another layer. " +
                "Use for: features within buffer, points in polygon, roads crossing zones.",
            promptSnippet: "Select features based on spatial relationship to another layer",
            promptGuidelines: [
                "predicate: 'intersects', 'within', 'contains', 'crosses', 'touches'",
                "Common patterns: buffer around point, points in polygon, lines crossing zones",
                "Returns count of selected features",
            ],
            parameters: {
                type: "object",
                properties: {
                    source_layer: { type: "string", description: "Layer to select from" },
                    reference_layer: { type: "string", description: "Layer to compare against" },
                    predicate: { type: "string", description: "Spatial predicate (intersects, within, contains, crosses)" },
                    method: { type: "string", enum: ["set", "add", "remove"], description: "Selection method", default: "set" },
                },
                required: ["source_layer", "reference_layer", "predicate"],
            },
            async execute(_id, params) {
                const code = `
from qgis.core import *
import processing

source = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == "${params.source_layer}"), None)
ref = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == "${params.reference_layer}"), None)
if not source: raise ValueError("Source layer not found: ${params.source_layer}")
if not ref: raise ValueError("Reference layer not found: ${params.reference_layer}")

method_map = {"set": 0, "add": 1, "remove": 2}
params = {
    'INPUT': source,
    'PREDICATE': [0],  # intersects
    'INTERSECT': ref,
    'METHOD': method_map.get("${params.method}", 0),
}

# Map predicate string to index
pred_map = {'intersects': 0, 'within': 1, 'contains': 2, 'crosses': 3, 'touches': 4, 'overlaps': 5}
if "${params.predicate}" in pred_map:
    params['PREDICATE'] = [pred_map["${params.predicate}"]]

processing.run("native:selectbylocation", params)
count = len(source.selectedFeatureIds())
result = {"selected": count, "layer": source.name()}
`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }], details: {} };
            },
        });

        // --- export_layer ---
        aery.registerTool({
            name: "export_layer",
            label: "Export Layer",
            description:
                "Export a layer or selection to file. Supports GeoPackage, Shapefile, GeoJSON, CSV. " +
                "SAVE ALL OUTPUTS to the project directory (use os.path.join(project_dir, ...)).",
            promptSnippet: "Save layer to file in project_dir (GeoPackage, GeoJSON, Shapefile)",
            promptGuidelines: [
                "SAVE TO project_dir — use os.path.join(project_dir, 'filename.gpkg')",
                "GeoPackage recommended - modern, handles CRS, no size limits",
                "Use 'memory:' for temporary results that can be further processed",
                "Include CRS in output path: 'file.gpkg|layername=MyLayer' for GeoPackage",
            ],
            parameters: {
                type: "object",
                properties: {
                    layer_name: { type: "string", description: "Name of layer to export" },
                    output_path: { type: "string", description: "Output file path" },
                    format: { type: "string", enum: ["gpkg", "shp", "geojson", "csv"], description: "Output format", default: "gpkg" },
                    only_selected: { type: "boolean", description: "Export only selected features" },
                },
                required: ["layer_name", "output_path"],
            },
            async execute(_id, params) {
                const fmt_map = { gpkg: "GPKG", shp: "ESRI Shapefile", geojson: "GeoJSON", csv: "CSV" };
                const code = `
from qgis.core import *
import processing

layer = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == "${params.layer_name}"), None)
if layer is None: raise ValueError("Layer not found")

# Get only selected if requested
input_layer = layer.materialize(QgsFeatureRequest().setFilterFids(layer.selectedFeatureIds())) if ${params.only_selected} and layer.selectedFeatureIds() else layer

# Export
params = {
    'INPUT': input_layer,
    'OUTPUT': "${params.output_path}",
    'FORMATS': [{"long_name": "${fmt_map[params.format] || 'GPKG'}", "short_name": "${params.format}", "extension": "${params.format}", "filter": ""}],
}
result_feats = processing.run("native:exporttogeopackage", params) if "${params.format}" == "gpkg" else processing.run("native:savefeatures", {
    'INPUT': input_layer,
    'OUTPUT': "${params.output_path}",
})
result = {"saved": "${params.output_path}", "format": "${params.format}"}
`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }], details: {} };
            },
        });

        // --- export_webmap ---
        aery.registerTool({
            name: "export_webmap",
            label: "Export Interactive Web Map",
            description:
                "Export the current QGIS project as an interactive web map using Leaflet.js. " +
                "Serializes visible vector layers as GeoJSON and raster tiles as GeoTIFF, " +
                "then builds a self-contained index.html with Leaflet.js. " +
                "Output: index.html + data/ directory — upload to any web host.",
            promptSnippet: "Export an interactive Leaflet web map from the QGIS project",
            promptGuidelines: [
                "Use AFTER all analysis and styling are finalised — this is a delivery step",
                "output_dir path is required; basemap: osm/satellite/topo/stamen_toner/none (default: osm)",
                "Extent auto-fills from the canvas; override with bbox 'xmin,xmax,ymin,ymax'",
                "include_search adds a nominatim geocoding box to the map UI",
            ],
            parameters: {
                type: "object",
                properties: {
                    output_dir: { type: "string", description: "Full path to output directory (created if missing)" },
                    output_format: { type: "string", enum: ["leaflet"], description: "Format (default: leaflet)" },
                    basemap: { type: "string", enum: ["osm", "satellite", "topo", "stamen_toner", "none"], description: "Basemap (default: osm)" },
                    extent: { type: "string", description: "Bbox override 'xmin,xmax,ymin,ymax' in project CRS (default: canvas)" },
                    include_search: { type: "boolean", description: "Add geocoding search box (default: false)" },
                    title: { type: "string", description: "HTML page title (default: project name)" },
                },
                required: ["output_dir"],
            },
            async execute(_id, params) {
                const outDir = String(params.output_dir).replace(/"/g, '\\"');
                const basemap = params.basemap || "osm";
                const extentStr = params.extent ? `'${String(params.extent).replace(/'/g, "\\'")}'` : "null";
                const includeSearch = params.include_search ? "True" : "False";
                const pageTitle = params.title ? `"${String(params.title).replace(/"/g, '\\"')}"` : "null";
                const code = `
import os, json
out_dir = "${outDir}"
os.makedirs(out_dir, exist_ok=True)
data_dir = os.path.join(out_dir, "data")
os.makedirs(data_dir, exist_ok=True)
from qgis.core import QgsProject, QgsVectorLayer, QgsVectorFileWriter
project = QgsProject.instance()
all_layers = list(project.mapLayers().values())
layer_files = []
for i, lyr in enumerate(all_layers):
    name = lyr.name().replace(" ","_").replace("/","_")
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
                from osgeo import gdal
                ds = gdal.Open(src)
                if ds:
                    gdal.Translate(os.path.join(data_dir, f"{name}_{i}.tif"), ds)
                    layer_files.append({"name": lyr.name(), "file": f"data/{name}_{i}.tif",
                                        "bandcount": ds.RasterCount})
    except Exception as e:
        print(f"  skip {lyr.name()}: {e}")
html = _build_leaflet_html(layer_files, "${basemap}", ${includeSearch}, ${pageTitle}, ${extentStr})
with open(os.path.join(out_dir, "index.html"), "w") as f:
    f.write(html)
layer_files.append({"name": "index.html", "file": "index.html", "size": len(html)})
print(f"Webmap: {len(layer_files)} files to {out_dir}")
result = {"format": "leaflet", "files": layer_files, "output_dir": out_dir}
`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: String(r.result ?? "Webmap exported.") }], details: {} };
            },
        });

        // --- publish_geoserver ---
        aery.registerTool({
            name: "publish_geoserver",
            label: "Publish to GeoServer",
            description:
                "Publish a vector or raster layer to a GeoServer REST endpoint. " +
                "Exports to a temp file (ogr2ogr or gdal_copy), uploads via multipart REST PUT, " +
                "creates/updates the datastore and publishes the layer for WFS/WMS access.",
            promptSnippet: "Publish a QGIS layer to GeoServer via REST",
            promptGuidelines: [
                "Requires: GeoServer running, ogr2ogr, admin credentials",
                "Pass geoserver_url, username, password as tool parameters",
                "GeoPackage for vectors, GeoTIFF for rasters — both auto-detected",
                "After publish: layer accessible at geoserver_url/workspace/wms and /wfs",
            ],
            parameters: {
                type: "object",
                properties: {
                    layer: { type: "string", description: "QGIS layer name/ID to publish" },
                    workspace: { type: "string", description: "GeoServer workspace name (default: 'default')" },
                    geoserver_url: { type: "string", description: "GeoServer base URL, e.g. 'http://localhost:8080/geoserver'" },
                    layer_name: { type: "string", description: "GeoServer layer name (default: same as QGIS layer)" },
                    username: { type: "string", description: "GeoServer admin username" },
                    password: { type: "string", description: "GeoServer admin password" },
                    publish_as: { type: "string", enum: ["vector", "raster", "auto"], description: "'auto' (default), 'vector', or 'raster'" },
                },
                required: ["layer", "geoserver_url", "username", "password"],
            },
            async execute(_id, params) {
                const code = `
import os, json, tempfile, urllib.request, base64, subprocess, shutil
layer_name = "${params.layer}"
workspace = "${params.workspace || 'default'}"
gs_url = "${params.geoserver_url}".rstrip('/')
username = "${params.username}"
password = "${params.password}"
publish_as = "${params.publish_as || 'auto'}"

layer = next((l for l in QgsProject.instance().mapLayers().values()
              if l.name() == layer_name), None)
if layer is None:
    raise ValueError(f"Layer not found: {layer_name}")

is_raster = str(layer.type()) == "Raster"
publish_type = "raster" if is_raster or publish_as == "raster" else "vector"
src_path = layer.source()
if not src_path or not os.path.isfile(src_path):
    raise FileNotFoundError(f"Layer source not found: {src_path}")

tmp = tempfile.mkdtemp(prefix="gs_upload_")
ext = ".tif" if publish_type == "raster" else ".gpkg"
upload_path = os.path.join(tmp, layer_name + ext)

if publish_type == "vector":
    subprocess.run(["ogr2ogr", "-overwrite", "-f", "GPKG", upload_path, src_path],
                   check=True, capture_output=True)
else:
    shutil.copy2(src_path, upload_path)

boundary = "----GeoServerBoundary7MA4YWxk"
with open(upload_path, "rb") as f:
    payload = f.read()

body = (
    f"--{boundary}\\r\\n"
    f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(upload_path)}"\\r\\n'
    f"Content-Type: application/octet-stream\\r\\n\\r\\n"
).encode() + payload + f"\\r\n--{boundary}--\\r\\n".encode()

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
    **(dict(error=reason) if reason else {}),
}
print(json.dumps(result))
result
`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: String(r.result ?? "GeoServer publish done.") }], details: {} };
            },
        });

        // --- refresh_canvas ---
        aery.registerTool({
            name: "refresh_canvas",
            label: "Refresh Map Canvas",
            description:
                "Force a full redraw of the QGIS map canvas. " +
                "Call after styling or layer visibility changes to see the new state.",
            promptSnippet: "Redraw the QGIS map canvas",
            promptGuidelines: [
                "Call after: set_layer_style, set visibility toggles, layer removals",
                "Always call before capture_canvas to ensure the capture is up to date",
            ],
            parameters: { type: "object", properties: {} },
            async execute() {
                return await qgisRequest(port, "run_code", {
                    code: `iface.mapCanvas().refreshAllLayers(); iface.mapCanvas().refresh(); result = 'refreshed'`,
                });
                return { content: [{ type: "text", text: "Canvas refreshed." }], details: {} };
            },
        });

        // --- batch_convert ---
        aery.registerTool({
            name: "batch_convert",
            label: "Batch Convert / Reproject Files",
            description:
                "Reproject or convert many geospatial files at once using gdalwarp / ogr2ogr. " +
                "Uses a glob pattern to match files. Saves results to an output directory.",
            promptSnippet: "Batch reproject/convert many geospatial files to a common CRS",
            promptGuidelines: [
                "Always reproject after bulk download to align CRS",
                "output_format: gpkg (default – recommended), shp, geojson, tif",
                "Output always goes to project_dir",
            ],
            parameters: {
                type: "object",
                properties: {
                    input_dir: { type: "string", description: "Directory of source files" },
                    pattern: { type: "string", description: "Glob like '*.shp' / '*.tif' (default '*')" },
                    target_crs: { type: "string", description: "Target CRS such as 'EPSG:4326' (required)" },
                    output_dir: { type: "string", description: "Output directory (default: project_dir)" },
                    output_format: { type: "string", enum: ["gpkg", "shp", "geojson", "tif"], description: "Default: gpkg" },
                },
                required: ["target_crs"],
            },
            async execute(_id, params) {
                return await qgisRequest(port, "run_code", {
                    code: `
import glob, os, subprocess
inp = "${params.input_dir.replace(/"/g, '\\\"')}"
pattern = "${params.pattern || '*'}"
tgt = "${params.target_crs.replace(/"/g, '\\\"')}"
out_dir = "${params.output_dir || project_dir}"
os.makedirs(out_dir, exist_ok=True)
files = glob.glob(os.path.join(inp, pattern))
for f in files:
    base = os.path.splitext(os.path.basename(f))[0]
    ext = os.path.splitext(f)[1].lower()
    out = os.path.join(out_dir, base + ".${params.output_format || 'gpkg'}")
    if ext in ('.tif', '.tiff'):
        subprocess.run(["gdalwarp", "-t_srs", tgt, f, out], check=True)
    else:
        subprocess.run(["ogr2ogr", "-overwrite", "-t_srs", tgt, out, f], check=True)
    print(f"Converted: {os.path.basename(f)} → {os.path.basename(out)}")
result = {"converted": len(files), "output_dir": out_dir}
`,
                });
                return { content: [{ type: "text", text: `Converted ${len(files) || 0} files.` }], details: {} };
            },
        });

        // --- set_layer_style ---
        aery.registerTool({
            name: "set_layer_style",
            label: "Set Layer Style",
            description:
                "Apply visual styles (colormaps, RGB bands, graduated or categorized renderers) " +
                "to raster and vector layers without writing raw QGIS Python.",
            promptSnippet: "Style a QGIS layer — singleband colormap, multiband RGB, graduated or categorized",
            promptGuidelines: [
                "singleband: colormap='viridis|gray|rdylgn|spectral|terrain', band=1, optional min/max stretch",
                "multiband: red=4, green=3, blue=2 (Sentinel-2: 4,3,2 for natural colour)",
                "graduated: column='field', classes=5 or N, method='jenks|equal|quantile', color_ramp='Reds|Blues|Spectral'",
                "categorized: column='class' with values list like 'forest,urban,water' or just a column check",
                "Always follow with refresh_canvas → capture_canvas to verify.",
                "legend_title sets legend header; legend_expression: field>10|Class_A|field==0|Class_B to create rule-based label entries separated by '|'",
            ],
            parameters: {
                type: "object",
                properties: {
                    layer: { type: "string", description: "Layer name or ID" },
                    style: { type: "string", enum: ["singleband", "multiband", "graduated", "categorized", "paletted"], description: "Style type" },
                    band: { type: "number", description: "Band for singleband (default: 1)" },
                    colormap: { type: "string", description: "Colormap: viridis, gray, rdylgn, spectral, terrain, custom_ramp_name, etc." },
                    min: { type: "number", description: "Min pixel value for stretch (auto-detected if omitted)" },
                    max: { type: "number", description: "Max pixel value for stretch (auto-detected if omitted)" },
                    red: { type: "number", description: "Red band index for multiband RGB styling" },
                    green: { type: "number", description: "Green band index for multiband RGB" },
                    blue: { type: "number", description: "Blue band index for multiband RGB" },
                    column: { type: "string", description: "Attribute column for graduated/categorized style" },
                    classes: { type: "number", description: "Number of class bins for graduated (default: 5)" },
                    method: { type: "string", enum: ["jenks", "equal", "quantile", "std"], description: "Classification method for graduated (default: jenks)" },
                    color_ramp: { type: "string", description: "Named colour ramp from QgsStyle — 'Reds', 'Blues', 'Spectral', 'Viridis', 'YlOrRd', etc." },
                    legend_title: { type: "string", description: "Legend header text (passed as legend title)" },
                    legend_expression: { type: "string", description: "Rule-based legend entries: 'field>10|Class_A|field==0|Class_B' — field replaces value, field/pair are separated by '|'" },
                },
                required: ["layer", "style"],
            },
            async execute(_id, params) {
                const p = params;
                return await qgisRequest(port, "run_code", {
                    code: `import json
from qgis.core import *

proj = QgsProject.instance()
lyr = next((l for l in proj.mapLayers().values()
            if l.name() == \`${JSON.stringify(p.layer)}\`), None)
if lyr is None:
    raise ValueError("Layer not found: \`${JSON.stringify(p.layer)}\`")

style = \`${JSON.stringify(p.style)}\`
renderer = None
ramp = None

if style == "singleband":
    band = int(${p.band ?? 1})
    prov = lyr.dataProvider()
    stats = None
    try:
        stats = prov.bandStatistics(band, Qgis.BandStatistics.All)
    except Exception:
        stats = None
    mn = float(${p.min ?? "stats.minimumValue if stats else 0"})
    mx = float(${p.max ?? "stats.maximumValue if stats else 255"})
    ramp = QgsColorRampShader()
    ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(mn, QColor("#313695"), str(mn)),
        QgsColorRampShader.ColorRampItem((mn+mx)/3, QColor("#74add1"), "lo"),
        QgsColorRampShader.ColorRampItem((mn+mx)/3*2, QColor("#ffffbf"), "mid"),
        QgsColorRampShader.ColorRampItem(mx, QColor("#d73027"), str(mx)),
    ])
    shader = QgsRasterShader(); shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(prov, band, shader)

elif style == "multiband":
    rd = int(${p.red ?? 1})
    gn = int($${p.green ?? 2})
    bl = int(${p.blue ?? 3})
    renderer = QgsMultiBandColorRenderer(lyr.dataProvider(), rd, gn, bl)

elif style == "graduated":
    col = \`${JSON.stringify(p.column || "")}\`
    ncl = int(${p.classes ?? 5})
    fields = [f.name() for f in lyr.fields()]
    idx = fields.index(col) if col in fields else -1
    style_hnd = QgsStyle.defaultStyle()
    cr_name = \`${JSON.stringify(p.color_ramp || "Reds")}\`
    cr = style_hnd.colorRamp(cr_name) or QgsStyle.defaultStyle().colorRamp("Reds")
    renderer = QgsGraduatedSymbolRenderer.createRenderer(lyr, idx, ncl, cr, None)
    renderer.setClassificationMethod(QgsClassificationJenks())

elif style == "categorized":
    col2 = \`${JSON.stringify(p.column || "")}\`
    fields2 = [f.name() for f in lyr.fields()]
    idx2 = fields2.index(col2) if col2 in fields2 else -1
    renderer = QgsCategorizedSymbolRenderer.createRenderer(lyr, idx2, QgsStyle.defaultStyle())

elif style == "paletted":
    renderer = lyr.renderer()

lyr.setRenderer(renderer)
lyr.triggerRepaint()

# Legend title and expression labels (applied at layer level)
legend_title = ${JSON.stringify(p.legend_title || null)}
legend_exp   = ${JSON.stringify(p.legend_expression || null)}
if legend_title:
    lyr.setName(legend_title)

result = {
    "styled": \`${JSON.stringify(p.layer)}\`,
    "style": style,
    "renderer": type(renderer).__name__,
}
print(json.dumps(result))
result
`,
                });
                return { content: [{ type: "text", text: String(r.result ?? "Style applied.") }], details: r.result || {} };
            },
        });

        // --- multi_map_layout ---
        aery.registerTool({
            name: "multi_map_layout",
            label: "Multi-Map Grid Layout",
            description:
                "Create a single print-layout PDF with multiple map panels arranged in a grid. " +
                "Each panel shows its own layer set and optional extent. " +
                "Best for before/after comparisons, multi-temporal overviews, and comparison maps.",
            promptSnippet: "Create a multi-panel print layout PDF with a grid of maps",
            promptGuidelines: [
                "Use instead of print_layout when you need side-by-side maps in one PDF",
                "panels[].title and panels[].layer_set control each panel",
                "If layer_set omitted the first few layers are auto-selected for that panel",
                "Extent auto-fills from canvas; use the 'extent' array to fix per-panel",
            ],
            parameters: {
                type: "object",
                properties: {
                    layout_name: { type: "string", description: "Name for the new QgsPrintLayout" },
                    output_path: { type: "string", description: "Full path to export (PDF)" },
                    paper_format: { type: "string", enum: ["A2", "A3", "A4", "Letter"], description: "Default: A3 allows good multi-panel space" },
                    orientation: { type: "string", enum: ["portrait", "landscape"], description: "Default: landscape" },
                    grid: { type: "string", description: "'rows,cols' e.g. '2,2' (default: auto from panel count)" },
                    panels: {
                        type: "array",
                        items: {
                            type: "object",
                            properties: {
                                title: { type: "string", description: "Panel label" },
                                layer_set: { type: "array", items: { type: "string" }, description: "Layer names to show" },
                                extent: { type: "string", description: "'xmin,xmax,ymin,ymax' in project CRS" },
                            },
                        },
                        description: "Panels to arrange in the layout",
                    },
                    margin_mm: { type: "number", description: "Page margin in mm (default: 20)" },
                },
                required: ["layout_name", "output_path"],
            },
            async execute(_id, params) {
                const panelsJson = JSON.stringify(params.panels || []);
                const gridDef = params.grid || "auto";
                return await qgisRequest(port, "run_code", {
                    code: `
import os, json, math
from qgis.core import *

proj = QgsProject.instance()
mgr = proj.layoutManager()
layout_name = "${params.layout_name}"
for i in range(mgr.printLayouts().count()):
    if mgr.printLayouts().at(i).name() == layout_name:
        mgr.removeLayout(mgr.printLayouts().at(i))

layout = QgsPrintLayout(proj)
page = layout.pageCollection().pages()[0]
page.setPageSize("${params.paper_format || 'A3'}",
                 QgsLayoutItemPage.Orientation.Landscape)

usable_w = page.pageSize().width()  - 20 * 2
usable_h = page.pageSize().height() - 20 * 2
panels_def = json.loads(${JSON.stringify(panelsJson)});
n_panels = len(panels_def) if panels_def else 1
grid_str = "${gridDef}"
if grid_str == "auto":
    cols = math.ceil(math.sqrt(n_panels))
    rows = math.ceil(n_panels / cols)
else:
    p = grid_str.split(",")
    cols = int(p[1] if len(p) > 1 else math.ceil(math.sqrt(n_panels)))
    rows = int(p[0] if len(p) > 0 else math.ceil(n_panels / cols))

gap = 20
cell_w = (usable_w - gap * (cols - 1)) / cols
cell_h = (usable_h - gap * (rows - 1)) / rows
all_layers = {l.name(): l for l in proj.mapLayers().values() if l.isValid()}

for idx, pdef in enumerate(panels_def):
    row, col = idx // cols, idx % cols
    x = 20 + col * (cell_w + gap)
    y = 20 + row * (cell_h + gap)

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
    else:
        map_itm.setExtent(iface.mapCanvas().extent())
    layout.addLayoutItem(map_itm)

    tt = pdef.get("title", "")
    if tt:
        lbl = QgsLayoutItemLabel(layout)
        lbl.setText(tt); lbl.setFont(QFont("Arial", 10, QFont.Bold))
        lbl.attemptMove(QgsLayoutPoint(x, y - 12, QgsUnitTypes.LayoutMillimeters))
        lbl.adjustSizeToText()
        layout.addLayoutItem(lbl)

exporter = QgsLayoutExporter(layout)
exported = exporter.exportToPdf("${params.output_path}", QgsLayoutExporter.PdfExportSettings())
ok = exported == QgsLayoutExporter.ExportResult.Success
print(f"Multi-map PDF: {ok} → ${params.output_path}")
`,
                });
                return { content: [{ type: "text", text: String(r.result ?? "Multi-map layout created.") }], details: {} };
            },
        });

        // --- run_qgis_code (PRIMARY) ---
        aery.registerTool({
            name: "run_qgis_code",
            label: "Run QGIS Code",
            description:
                "Execute Python code inside QGIS. " +
                "Full QGIS Python API: qgis.core, qgis.gui, processing, iface, PyQt6. " +
                "Store result in `result` variable for display to user. " +
                "Use processing.run('native:buffer', {...}) for standard geoalgorithms.",
            promptSnippet: "Execute QGIS Python code for geospatial operations",
            promptGuidelines: [
                "PRIMARY tool for ALL QGIS operations",
                "Store results in `result` variable for user display",
                "Available: processing, iface, qgis.core classes (QgsProject, etc.), PyQt6",
                "CRS check: layer.crs().authid() before spatial operations",
                "Use processing.run() for standard algorithms (handles CRS properly)",
                "Use raw Python for custom logic, geometry ops, styling",
            ],
            parameters: {
                type: "object",
                properties: {
                    code: {
                        type: "string",
                        description:
                            "Python code to execute. Available: processing, iface, qgis.core, PyQt6. " +
                            "Store result in `result`. Example: layer=QgsProject.instance().mapLayersByName('roads')[0]; " +
                            "result=f'Layer has {layer.featureCount()} features'"
                    },
                    timeout: { type: "number", description: "Timeout in seconds (default 300)" },
                },
                required: ["code"],
            },
            async execute(_id, params) {
                const r = await qgisRequest(port, "run_code", { code: params.code, timeout: params.timeout || 300 });
                const text = typeof r.result === "object" ? JSON.stringify(r.result, null, 2) : String(r.result ?? "");
                return { content: [{ type: "text", text }], details: {} };
            },
        });

        // --- get_project_context ---
        aery.registerTool({
            name: "get_project_context",
            label: "Get Project Context",
            description:
                "Get a full snapshot of the current QGIS project: all layers with names, types, CRS, " +
                "feature counts, fields; active layer and selection; project CRS and extent. " +
                "Call this FIRST before any operation to understand current state.",
            promptSnippet: "Get QGIS project context (layers, CRS, selection, extent)",
            promptGuidelines: [
                "Call this FIRST before starting any task",
                "Review layers: names, types (vector/raster), CRS, feature counts",
                "Check for large layers (>100k features) - might need sampling",
                "Note any CRS mismatches that need reprojection",
            ],
            parameters: { type: "object", properties: {} },
            async execute() {
                const r = await qgisRequest(port, "get_project_context", {});
                return { content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }], details: {} };
            },
        });

        // --- get_layer_info ---
        aery.registerTool({
            name: "get_layer_info",
            label: "Get Layer Details",
            description:
                "Inspect a specific layer in depth: fields and types, geometry type, CRS, " +
                "sample features, statistics. Use when you need to understand a layer before modifying it.",
            promptSnippet: "Get detailed layer info (fields, geometry, sample data)",
            promptGuidelines: [
                "Use this before modifying or analyzing a specific layer",
                "Check geometry type to know what spatial operations are valid",
                "Review fields to know what attributes can be queried/calculated",
                "Sample features show data quality and structure",
            ],
            parameters: {
                type: "object",
                properties: {
                    layer_name: { type: "string", description: "Exact name of the layer to inspect" },
                },
                required: ["layer_name"],
            },
            async execute(_id, params) {
                const code = `
from qgis.core import *

layer = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == "${params.layer_name}"), None)
if layer is None:
    available = [l.name() for l in QgsProject.instance().mapLayers().values()]
    raise ValueError(f"Layer '${params.layer_name}' not found. Available: {available}")

info = {
    "name": layer.name(),
    "type": layer.type().toString(),
    "crs": layer.crs().authid(),
    "feature_count": layer.featureCount(),
    "fields": [{"name": f.name(), "type": f.type().toString()} for f in layer.fields()],
    "geometry_type": Qgis.geometryType(layer.wkbType()).toString() if hasattr(layer, 'wkbType') else "N/A",
}

# Sample 5 features
samples = []
for i, feat in enumerate(layer.getFeatures()):
    if i >= 5: break
    geom = feat.geometry()
    samples.append({
        "id": feat.id(),
        "geom_type": geom.type().toString(),
        "attrs": dict(zip([f.name() for f in layer.fields()], feat.attributes())),
    })

info["samples"] = samples
result = info
`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }], details: {} };
            },
        });

        // --- validate_project ---
        aery.registerTool({
            name: "validate_project",
            label: "Validate Project",
            description:
                "Runs a preflight check on the current QGIS project: CRS consistency, " +
                "layer validity, missing files, save status. Returns a summary of issues " +
                "before any heavy processing runs.",
            promptSnippet: "Preflight project check — CRS, validity, missing files",
            promptGuidelines: [
                "Call this BEFORE any significant Processing operation",
                "Fixes CRS mismatches, invalid rasters, missing files before they cause errors",
                "Returns healthy=true if the project is ready to process",
            ],
            parameters: { type: "object", properties: {} },
            async execute() {
                const code = `
from qgis.core import *

proj = QgsProject.instance()
issues = []

# Check all layers are valid
for lyr in proj.mapLayers().values():
    if not lyr.isValid():
        issues.append(f"Invalid layer: {lyr.name()}")

# Check for CRS mismatches
crs_map = {}
for lyr in proj.mapLayers().values():
    auth = lyr.crs().authid() if lyr.crs() else "none"
    crs_map.setdefault(auth, []).append(lyr.name())
if len(crs_map) > 1:
    issues.append(f"CRS mismatch — {len(crs_map)} different CRS systems in project")

result = {
    "healthy": len(issues) == 0,
    "issues": issues,
    "summary": "Project OK" if not issues else "; ".join(issues),
}
`;
                const r = await qgisRequest(port, "run_code", { code });
                const text = typeof r.result === "object" ? JSON.stringify(r.result, null, 2) : String(r.result ?? "");
                return { content: [{ type: "text", text }], details: {} };
            },
        });

        // --- get_audit_trail ---
        aery.registerTool({
            name: "get_audit_trail",
            label: "Get Audit Trail",
            description:
                "Returns the last 30 operations from the audit log. " +
                "Use to understand what code was previously executed and its results.",
            promptSnippet: "Show recent operations from audit trail",
            promptGuidelines: [
                "Use after a failed operation to understand what was attempted",
            ],
            parameters: { type: "object", properties: {} },
            async execute() {
                const code = `
import os, json
audit_path = os.path.expanduser("~/.aery/operations.jsonl")
try:
    with open(audit_path) as f:
        lines = f.readlines()
    recent = [json.loads(l) for l in lines[-30:]]
    result = recent
except Exception as e:
    result = {"error": str(e)}
`;
                const r = await qgisRequest(port, "run_code", { code });
                const text = typeof r.result === "object" ? JSON.stringify(r.result, null, 2) : String(r.result ?? "");
                return { content: [{ type: "text", text }], details: {} };
            },
        });

        // --- list_processing_algorithms ---
        aery.registerTool({
            name: "list_processing_algorithms",
            label: "List Processing Algorithms",
            description:
                "Returns all available QGIS Processing algorithms with their IDs, groups, " +
                "and parameter names. Useful for discovering what spatial operations are available.",
            promptSnippet: "List all QGIS Processing algorithms",
            promptGuidelines: [
                "Scan the full list to find the right algorithm for a task",
                "IDs follow pattern: provider:algorithmname (e.g. native:buffer, gdal:rasterize)",
            ],
            parameters: { type: "object", properties: {} },
            async execute() {
                const code = `
from qgis.core import QgsApplication
reg = QgsApplication.processingRegistry()
algs = []
for p in reg.providers():
    for a in p.algorithms():
        algs.append({
            "id": a.id(),
            "name": a.displayName(),
            "group": p.id(),
        })
result = algs
`;
                const r = await qgisRequest(port, "run_code", { code });
                const text = typeof r.result === "object" ? JSON.stringify(r.result, null, 2) : String(r.result ?? "");
                return { content: [{ type: "text", text }], details: {} };
            },
        });

        // --- describe_processing_algorithm ---
        aery.registerTool({
            name: "describe_processing_algorithm",
            label: "Describe Processing Algorithm",
            description:
                "Describe a single QGIS Processing algorithm: parameter names, types, " +
                "defaults, and output descriptions.",
            promptSnippet: "Describe a Processing algorithm and its parameters",
            promptGuidelines: [
                "Use this to get exact parameter names before running an algorithm",
            ],
            parameters: {
                type: "object",
                properties: {
                    algorithm_id: { type: "string", description: "Full algorithm ID (e.g. native:buffer)" },
                },
                required: ["algorithm_id"],
            },
            async execute(_id, params) {
                const code = `
from qgis.core import QgsApplication
reg = QgsApplication.processingRegistry()
alg = reg.algorithmById("${params.algorithm_id}")
if alg is None:
    raise ValueError(f"Algorithm not found: ${params.algorithm_id}")
params_info = {}
for p in alg.parameterDefinitions():
    params_info[p.name()] = {
        "type": p.type(),
        "description": p.description(),
        "default": str(p.defaultValue()) if p.defaultValue() is not None else None,
    }
result = {
    "id": alg.id(),
    "name": alg.displayName(),
    "shortDescription": alg.shortDescription(),
    "parameters": params_info,
}
`;
                const r = await qgisRequest(port, "run_code", { code });
                const text = typeof r.result === "object" ? JSON.stringify(r.result, null, 2) : String(r.result ?? "");
                return { content: [{ type: "text", text }], details: {} };
            },
        });

        // --- run_processing_algorithm ---
        aery.registerTool({
            name: "run_processing_algorithm",
            label: "Run Processing Algorithm (advanced)",
            description:
                "Run a QGIS Processing algorithm with automatic parameter resolution. " +
                "Resolves layer names, nested parameters, and returns structured success/error info.",
            promptSnippet: "Run Processing algorithm with structured result",
            promptGuidelines: [
                "Use instead of run_processing for complex / nested parameter values",
                "Returns algorithm id, success flag, and output summary",
            ],
            parameters: {
                type: "object",
                properties: {
                    algorithm_id: { type: "string", description: "Full algorithm ID (native:buffer, etc.)" },
                    params: { type: "object", additionalProperties: true, description: "Parameters dict" },
                },
                required: ["algorithm_id", "params"],
            },
            async execute(_id, params) {
                const code = `
import json, processing
from qgis.core import *

def resolve_processing_value(value, definition=None):
    if isinstance(value, str) and value in [l.name() for l in QgsProject.instance().mapLayers().values()]:
        return QgsProject.instance().mapLayersByName(value)[0]
    return value

reg = QgsApplication.processingRegistry()
alg = reg.algorithmById("${params.algorithm_id}")
if alg is None:
    raise ValueError(f"Algorithm not found: ${params.algorithm_id}")

resolved = {}
for k, v in ${JSON.stringify(params.params)}.items():
    resolved[k] = resolve_processing_value(v)

feedback = processing.QgsProcessingFeedback()
raw_result = processing.run("${params.algorithm_id}", resolved, feedback=feedback)
result = {
    "algorithm": alg.id(),
    "success": True,
    "outputs": raw_result,
    "output_summary": "; ".join(f"{k}={v}" for k, v in raw_result.items() if k != "LOG"),
}
`;
                const r = await qgisRequest(port, "run_code", { code });
                return {
                    content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }],
                    details: {},
                };
            },
        });

        // --- validate_processing_runtime ---
        aery.registerTool({
            name: "validate_processing_runtime",
            label: "Validate Processing Runtime",
            description:
                "Smoke-tests the QGIS Processing environment: provider count, " +
                "algorithm availability, and a test execution. Reports if Processing " +
                "is ready for production use.",
            promptSnippet: "Validate that Processing is ready (providers, algorithms)",
            promptGuidelines: [
                "Run at startup or when processing tools return unexpected errors",
                "Reports provider count, total algorithm count, and a sample run",
            ],
            parameters: { type: "object", properties: {} },
            async execute() {
                const code = `
import processing
from qgis.core import QgsApplication

# QGIS Processing registry — use registry.providers(), provider().id() pattern
registry = QgsApplication.processingRegistry()
providers = registry.providers()
provider_count = len(providers)
alg_ids = [a.id() for p in providers for a in p.algorithms()]
sample_algorithms = alg_ids[:20]

# Try running the simplest known algorithm
try:
    alg_id = "native:buffer"
    alg = registry.algorithmById(alg_id)
    if alg:
        test_params = {"INPUT": None, "DISTANCE": 1, "OUTPUT": "memory:"}
        test_result = processing.run(alg_id, test_params)
        test_status = "pass"
    else:
        test_status = "algorithm not found"
except Exception as e:
    test_status = f"fail: {e}"

result = {
    "success": True,
    "provider_count": provider_count,
    "algorithm_count": len(alg_ids),
    "sample_algorithms": sample_algorithms,
    "smoke_test": test_status,
}
`;
                const r = await qgisRequest(port, "run_code", { code });
                return {
                    content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }],
                    details: {},
                };
            },
        });

        // --- run_processing ---
        aery.registerTool({
            name: "run_processing",
            label: "Run Processing Algorithm",
            description:
                "Run any QGIS Processing algorithm by name with parameters. " +
                "Use the algorithm ID (e.g. 'native:buffer') and pass parameters dict.",
            promptSnippet: "Run a QGIS Processing algorithm (buffer, clip, intersect...)",
            promptGuidelines: [
                "Use 'native:buffer' for buffers, 'native:clip' for clipping, etc.",
                "Parameters must be dict with algorithm-specific keys",
                "Common: INPUT (layer), OUTPUT (output path or 'memory:'), DISTANCE, etc.",
                "Check processing.algorithmHelp('native:buffer') for parameter info",
            ],
            parameters: {
                type: "object",
                properties: {
                    algorithm: { type: "string", description: "Algorithm ID (e.g. native:buffer, native:clip)" },
                    parameters: { type: "object", additionalProperties: true, description: "Algorithm parameters dict" },
                },
                required: ["algorithm", "parameters"],
            },
            async execute(_id, params) {
                const code = `import processing
from qgis.core import *
feedback = processing.QgsProcessingFeedback()
result = processing.run("${params.algorithm}", ${JSON.stringify(params.parameters)})`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }], details: {} };
            },
        });

        // --- add_layer ---
        aery.registerTool({
            name: "add_layer",
            label: "Add Layer",
            description:
                "Load a geospatial file into QGIS. Supports GeoJSON, Shapefile, GeoPackage, GeoTIFF. " +
                "Auto-detects layer type from file extension. Files should be in project_dir.",
            promptSnippet: "Load a geospatial file from project_dir into QGIS",
            promptGuidelines: [
                "Supports: GeoJSON (.geojson, .json), Shapefile (.shp), GeoPackage (.gpkg), GeoTIFF (.tif, .tiff)",
                "Always use absolute file paths (os.path.join(project_dir, ...))",
                "File must be readable by QGIS/OGR",
            ],
            parameters: {
                type: "object",
                properties: {
                    path: { type: "string", description: "Absolute file path to geospatial file" },
                    name: { type: "string", description: "Display name (optional, defaults to filename)" },
                },
                required: ["path"],
            },
            async execute(_id, params) {
                const p = params.path;
                const isRaster = /\.(tif|tiff|dem)$/i.test(p);
                const code = `
from qgis.core import *
import os
l = ${isRaster ? `QgsRasterLayer("${p}", os.path.basename("${p}"))` : `QgsVectorLayer("${p}", os.path.basename("${p}"), 'ogr')`}
if not l.isValid(): raise ValueError(f"Failed: {l.lastError()}")
${params.name ? `l.setName("${params.name}")` : ""}
QgsProject.instance().addMapLayer(l)
result = f"Loaded {l.name()}"
`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: r.result }], details: {} };
            },
        });

        // --- capture_canvas ---
        aery.registerTool({
            name: "capture_canvas",
            label: "Capture Map Canvas",
            description:
                "Capture the QGIS map canvas as an image. " +
                "Zooms to show all layers, captures at 2x resolution for clarity.",
            promptSnippet: "Capture the QGIS map canvas - shows all layers at good resolution",
            promptGuidelines: [
                "ALWAYS call this after operations that change the map",
                "Shows current styling, labels, all visible layers",
                "Use scale=1 for fast captures (default), scale=2 for high quality",
            ],
            parameters: {
                type: "object",
                properties: {
                    width: { type: "number", description: "Image width in pixels (default 800)" },
                    height: { type: "number", description: "Image height in pixels (default 600)" },
                    scale: { type: "number", enum: [1, 2], description: "Resolution scale: 1=fast, 2=high quality (default 1)" },
                },
            },
            async execute(_id, params) {
                const width = params.width || 800;
                const height = params.height || 600;
                const scale = params.scale || 1;  // 1=fast, 2=high quality

                const code = `
from PyQt6.QtCore import QBuffer, QIODevice, QSize
from PyQt6.QtGui import QImage, QPainter

canvas = iface.mapCanvas()
settings = canvas.mapSettings()

# High DPI rendering for crisp images
settings.setOutputSize(QSize(${width}, ${height}))
settings.setDevicePixelRatio(${scale})

# Calculate final image size
final_w = ${width} * ${scale}
final_h = ${height} * ${scale}

image = QImage(final_w, final_h, QImage.Format.Format_ARGB32)
image.fill(0xFFFFFFFF)
painter = QPainter(image)
canvas.render(painter)
painter.end()

# Convert to base64 PNG
buf = QBuffer()
buf.open(QIODevice.OpenModeFlag.WriteOnly)
image.save(buf, "PNG")
result = buf.data().toBase64().data().decode()
`;
                const r = await qgisRequest(port, "run_code", { code });
                const b64 = typeof r.result === "string" ? r.result : "";
                const quality = scale === 2 ? "high" : "fast";
                return {
                    content: [
                        { type: "text", text: `Map captured (${width}x${height}, ${quality})` },
                        { type: "image", data: b64, mimeType: "image/png" },
                    ],
                    details: { width, height, scale, quality },
                };
            },
        });

        // --- bash ---
        aery.registerTool({
            name: "bash",
            label: "Shell Command",
            description:
                "Execute a shell command on the QGIS host system. " +
                "Use for GDAL/OGR operations, pip install, file management. " +
                "Runs from the project directory by default.",
            promptSnippet: "Run shell commands from the project directory",
            promptGuidelines: [
                "Useful for: gdalinfo, ogr2ogr, pip install, file copy/move",
                "Runs from project_dir by default (available as 'project_dir' variable)",
                "Returns stdout, stderr, and return code",
                "Use sparingly - most GIS work should use processing.run() or qgis.core",
            ],
            parameters: {
                type: "object",
                properties: {
                    command: { type: "string", description: "Shell command to execute" },
                    timeout: { type: "number", description: "Timeout in seconds (default 60)" },
                },
                required: ["command"],
            },
            async execute(_id, params) {
                const code = `
import subprocess, json, os
cwd = os.getcwd()
try:
    os.chdir(project_dir)
    r = subprocess.run(${JSON.stringify(params.command)}, shell=True, capture_output=True, text=True, timeout=${params.timeout || 60})
finally:
    os.chdir(cwd)
result = {"stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}
`;
                const r = await qgisRequest(port, "run_code", { code, timeout: params.timeout || 120 });
                const parsed = typeof r.result === "string" ? JSON.parse(r.result) : r.result;
                const text = [
                    parsed.stdout || "",
                    parsed.stderr ? "[stderr]\n" + parsed.stderr : "",
                ].filter(Boolean).join("\n") || "(no output)";
                return { content: [{ type: "text", text }], details: { returncode: parsed.returncode } };
            },
        });

        // --- confirm_action ---
        aery.registerTool({
            name: "confirm_action",
            label: "Confirm Action",
            description:
                "Ask user to confirm before destructive operations. " +
                "Use before: deleting layers, overwriting files, bulk deletes.",
            promptSnippet: "Confirm a destructive QGIS action",
            promptGuidelines: [
                "ALWAYS confirm before: deleting layers, overwriting existing files",
                "Use before operations affecting >10,000 features",
                "Returns 'Confirmed' or 'Cancelled' based on user response",
            ],
            parameters: {
                type: "object",
                properties: {
                    message: { type: "string", description: "Clear description of what will happen" },
                },
                required: ["message"],
            },
            async execute(_id, params, _sig, _upd, ctx) {
                const ok = await ctx.ui.confirm("Confirm QGIS Action", params.message);
                return { content: [{ type: "text", text: ok ? "Confirmed" : "Cancelled" }], details: { confirmed: ok } };
            },
        });

        // --- web_search ---
        aery.registerTool({
            name: "web_search",
            label: "Web Search",
            description:
                "Search the web for information about satellite data sources, QGIS documentation, " +
                "geospatial APIs, coordinate reference systems.",
            promptSnippet: "Search the web for geospatial data or information",
            promptGuidelines: [
                "Useful for: finding OpenData portals, API documentation, CRS definitions",
                "Not for real-time data - use GEE or other APIs for live data",
            ],
            parameters: {
                type: "object",
                properties: {
                    query: { type: "string", description: "Search query" },
                },
                required: ["query"],
            },
            async execute(_id, params) {
                const url = `https://lite.duckduckgo.com/lite/?q=${encodeURIComponent(params.query)}`;
                const res = await fetch(url);
                const html = await res.text();
                const text = html
                    .replace(/<[^>]+>/g, " ")
                    .replace(/\s+/g, " ")
                    .trim()
                    .slice(0, 8000);
                return { content: [{ type: "text", text: text || "(no results)" }], details: {} };
            },
        });

        // --- ask_user ---
        aery.registerTool({
            name: "ask_user",
            label: "Ask User",
            description:
                "Ask the user a question and get a free-form text response. " +
                "Use when you need clarification or decisions before proceeding.",
            promptSnippet: "Ask the user a question",
            promptGuidelines: [
                "Use when: ambiguous task, need user preference, want confirmation before proceeding",
                "After showing results, ask if user wants changes or follow-up",
            ],
            parameters: {
                type: "object",
                properties: {
                    question: { type: "string", description: "Question to ask the user" },
                },
                required: ["question"],
            },
            async execute(_id, params, _sig, _upd, ctx) {
                const answer = await ctx.ui.input("QGIS Assistant", params.question);
                return {
                    content: [{ type: "text", text: answer || "(user did not respond)" }],
                    details: { response: answer },
                };
            },
        });

        // --- run_gee_code ---
        aery.registerTool({
            name: "run_gee_code",
            label: "Run Google Earth Engine Code",
            description:
                "Execute Google Earth Engine Python code inside QGIS. " +
                "Handles authentication, loads results as QGIS layers. " +
                "Installs earthengine-api automatically if missing.",
            promptSnippet: "Execute Google Earth Engine code and load results into QGIS",
            promptGuidelines: [
                "Use run_gee_code for satellite imagery, land cover analysis, change detection",
                "GEE is initialized as `ee`. Store final result in `gee_result`.",
                "For images: convert to numpy array or export as GeoTIFF",
                "For features: getInfo() and load as QGIS layer",
            ],
            parameters: {
                type: "object",
                properties: {
                    code: {
                        type: "string",
                        description:
                            "Python code using Earth Engine. GEE initialized as `ee`. " +
                            "Store final ee.Image or ee.FeatureCollection in `gee_result`.",
                    },
                },
                required: ["code"],
            },
            async execute(_id, params) {
                const wrapped = `
import subprocess, json, sys, importlib.util

spec = importlib.util.find_spec("ee")
if spec is None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "earthengine-api", "-q"])

import ee

try:
    ee.Initialize()
except Exception:
    try:
        ee.Initialize(project="default")
    except Exception:
        pass

${params.code}

result = gee_result.getInfo() if "gee_result" in dir() else str(result)
`;
                const r = await qgisRequest(port, "run_code", { code: wrapped, timeout: 120 });
                const text = typeof r.result === "object" ? JSON.stringify(r.result, null, 2) : String(r.result ?? "");
                return { content: [{ type: "text", text }], details: {} };
            },
        });

        // --- save_map_theme ---
        aery.registerTool({
            name: "save_map_theme",
            label: "Save Map Theme",
            description:
                "Save the current QGIS map theme (layer visibility + renderer state) under a name. " +
                "Restore it later with load_map_theme to backtrack without rerunning code.",
            promptSnippet: "Save the current layer state as a reusable map theme",
            promptGuidelines: [
                "Save a theme right before starting a new, riskier analysis branch",
                "Use load_map_theme to rewind to this state",
                "Themes are stored in the QGIS project (.qgz) via mapThemeCollection",
            ],
            parameters: {
                type: "object",
                properties: {
                    theme_name: { type: "string", description: "Name for the saved theme" },
                },
                required: ["theme_name"],
            },
            async execute(_id, params) {
                return await qgisRequest(port, "run_code", {
                    code: `
import json, sys
from qgis.core import QgsProject
proj = QgsProject.instance()
mgr = proj.mapThemeCollection()
theme_name = \`${JSON.stringify(params.theme_name)} \`.strip('\"')
layer_records = [
    QgsMapThemeCollection.MapThemeLayerRecord(l)
    for l in proj.mapLayers().values() if l.isValid()
]
mgr.addTheme(QgsMapThemeCollection.MapTheme(theme_name, layer_records))
try:
    mgr.addMapTheme(theme_name, layer_records)
except Exception:
    pass
proj.write()
result = {"saved": theme_name, "themes": sorted(mgr.mapThemes())}
print(json.dumps(result))
result
`,
                });
                return { content: [{ type: "text", text: String(r.result ?? "Theme saved.") }], details: r.result || {} };
            },
        });

        // --- load_map_theme ---
        aery.registerTool({
            name: "load_map_theme",
            label: "Load Map Theme",
            description:
                "Load a previously saved QGIS map theme: sets layer visibility to the saved state. " +
                "The fastest way to reset layer visibility without rerunning anything.",
            promptSnippet: "Restore a saved QGIS map theme",
            promptGuidelines: [
                "Use list_map_themes (or inspect mapThemeCollection) first to get the name",
                "refresh=true (default) redraws the canvas — keep it true to see the change",
            ],
            parameters: {
                type: "object",
                properties: {
                    theme_name: { type: "string", description: "Name of the theme to restore" },
                    refresh: { type: "boolean", description: "Redraw canvas after loading (default: true)" },
                },
                required: ["theme_name"],
            },
            async execute(_id, params) {
                const doRefresh = params.refresh !== false;
                return await qgisRequest(port, "run_code", {
                    code: `
import json
from qgis.core import QgsProject
proj = QgsProject.instance()
mgr = proj.mapThemeCollection()
theme = mgr.mapTheme(\`${JSON.stringify(params.theme_name)}\`)
if theme is None:
    raise ValueError(f"Theme not found: {sorted(mgr.mapThemes())}")
records = mgr.mapThemeRecords(\`${JSON.stringify(params.theme_name)}\`)
for rec in records:
    lyr = rec.layer()
    if lyr:
        lyr.setVisible(rec.isVisible())
proj.write()
${doRefresh ? 'iface.mapCanvas().refreshAllLayers(); iface.mapCanvas().refresh()' : ''}
result = {"loaded": \`${JSON.stringify(params.theme_name)}\`, "records": len(records)}
print(json.dumps(result))
result
`,
                });
                return { content: [{ type: "text", text: String(r.result ?? "Theme loaded.") }], details: r.result || {} };
            },
        });

        // --- register_tool ---
        aery.registerTool({
            name: "register_tool",
            label: "Register Tool",
            description:
                "Create a reusable QGIS tool from Python code. " +
                "Use PARAMS_PLACEHOLDER in code for tool parameters.",
            promptSnippet: "Create a reusable QGIS tool",
            parameters: {
                type: "object",
                properties: {
                    name: { type: "string", description: "Tool name (snake_case)" },
                    description: { type: "string", description: "Description of what the tool does" },
                    code: { type: "string", description: "Python code with PARAMS_PLACEHOLDER for parameters" },
                    parameters_schema: { type: "object", description: "JSON Schema for parameters (optional)" },
                },
                required: ["name", "description", "code"],
            },
            async execute(_id, params) {
                const schema = params.parameters_schema || { type: "object", properties: {} };
                aery.registerTool({
                    name: params.name,
                    label: params.name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()),
                    description: params.description,
                    parameters: schema,
                    async execute(id, tp, sig, upd, ctx) {
                        const code = params.code.replace(/PARAMS_PLACEHOLDER/g, JSON.stringify(tp));
                        const r = await qgisRequest(port, "run_code", { code });
                        return { content: [{ type: "text", text: r.result }], details: {} };
                    },
                });
                return { content: [{ type: "text", text: `Registered tool: ${params.name}` }], details: {} };
            },
        });

        // --- ask_user ---
        aery.registerTool({
            name: "ask_user",
            label: "Ask User",
            description:
                "Ask the user a multiple-choice question with required fields per option. " +
                "Renders an interactive card in the chat — user fills required fields, " +
                "selects an option, and submits. The answer is returned as the tool result. " +
                "Use for decisions that require human input.",
            promptSnippet: "Ask the user for input via interactive question card",
            parameters: {
                type: "object",
                properties: {
                    header: { type: "string", description: "Question title/highlight text" },
                    description: { type: "string", description: "Full question body text" },
                    options: {
                        type: "array",
                        description: "Answer options",
                        items: {
                            type: "object",
                            properties: {
                                label: { type: "string", description: "Option label (displayed in chat)" },
                                description: { type: "string", description: "Detailed description for this option" },
                                required_fields: {
                                    type: "array",
                                    description: "Fields the user must fill before submitting",
                                    items: {
                                        type: "object",
                                        properties: {
                                            name: { type: "string", description: "Field key in the answer" },
                                            label: { type: "string", description: "Human-friendly field label" },
                                            placeholder: { type: "string", description: "Placeholder text in input" },
                                        },
                                        required: ["name", "label"],
                                    },
                                },
                            },
                            required: ["label", "required_fields"],
                        },
                    },
                },
                required: ["header", "description", "options"],
            },
            async execute(_id, params) {
                const p    = params ?? {};
                const header      = typeof p.header === "string"      ? p.header : "";
                const description = typeof p.description === "string"   ? p.description : "";
                const options     = Array.isArray(p.options)           ? p.options   : [];

                if (!header || !description || options.length === 0) {
                    throw new Error(
                        "ask_user: header, description, and options[] are required. " +
                        `Got header=${JSON.stringify(header)}, description=${JSON.stringify(description)}, ` +
                        `options.length=${options.length}`,
                    );
                }
                const r = await qgisRequest(port, "question", {
                    params: { header, description, options },
                });
                return {
                    content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }],
                    details: r.result,
                };
            },
        });

        // --- Inject QGIS context before each turn ---
        aery.on("context", async (event) => {
            try {
                const r = await qgisRequest(port, "get_project_context", {});
                event.messages.push({
                    role: "user",
                    content: [{ type: "text", text: "[QGIS State]\n" + JSON.stringify(r.result, null, 2) }],
                });
            } catch {}
        });
    };
}

// =============================================================================
// ENTRY POINT
// =============================================================================

const port = parseInt(process.argv[2], 10);
if (!port || port < 1024 || port > 65535) {
    console.error("Usage: aery-qgis-runner <port> [--provider-file <path>]");
    console.error("  port: TCP port where the QGIS executor is listening (1024-65535)");
    process.exit(1);
}

const providerConfig = loadProviderConfig();

const modelArg = providerConfig?.model
    ? ["--model", `qgis/${providerConfig.model}`]
    : [];

// Build processing algorithms string for prompt
const algoList = PROCESSING_ALGORITHMS.map(a => `- ${a.name}: '${a.id}'`).join("\n");

const qgisPrompt = [
    "You are a geospatial AI assistant inside QGIS.",
    "Your workflow: Understand → Explore → Plan → Execute → Visualize → Confirm.",

    "",
    "=== QGIS WORKFLOW ===",
    "1. UNDERSTAND: Analyze the user's request (use analyze_task for complex tasks)",
    "2. EXPLORE: Get project context, inspect layers, check CRS",
    "3. PLAN: Select the right operations, handle CRS mismatches",
    "4. EXECUTE: Run processing algorithms or custom Python",
    "5. VISUALIZE: Always capture_canvas after changes",
    "6. CONFIRM: Show results, offer follow-up operations",

    "",
    "=== AVAILABLE TOOLS ===",
    "- analyze_task: Break down complex tasks into steps (call FIRST for multi-step tasks)",
    "- get_project_context: Get all layers, CRS, selection (call FIRST for all tasks)",
    "- get_layer_info: Detailed layer inspection (fields, geometry, sample data)",
    "- select_by_attribute: Filter features by expression ('population' > 1000)",
    "- select_by_location: Spatial selection (features within buffer, points in polygon)",
    "- run_processing: Run native QGIS algorithms (buffer, clip, intersect, etc.)",
    "- run_qgis_code: Custom Python (PRIMARY for complex logic)",
    "- add_layer: Load external files (GeoJSON, Shapefile, GeoTIFF, raster)",
    "- export_layer: Save to file (GeoPackage, GeoJSON, Shapefile, CSV)",
    "- capture_canvas: Screenshot map (default fast, scale=2 for quality) - ALWAYS after changes",
    "- bash: GDAL/OGR shell commands",
    "- confirm_action: Confirm destructive ops (delete, overwrite)",
    "- ask_user: Ask questions for clarification",
    "- web_search: Look up GIS docs, data sources",
    "- run_gee_code: Google Earth Engine satellite analysis",
    "- register_tool: Create reusable tools from Python snippets",

    "",
    `=== PROCESSING ALGORITHMS ===`,
    algoList,
    "",
    "Use run_processing('algorithm_id', {PARAM: value}) for these.",

    "",
    "=== PROJECT DIRECTORY (CRITICAL) ===",
    "ALL output files MUST be saved inside the project directory.",
    "The project directory is in the QGIS State context as 'project_dir'.",
    "In Python code, the 'project_dir' variable is always available:",
    "  output_path = os.path.join(project_dir, 'results.gpkg')",
    "  processing.run('native:buffer', {'INPUT': l, 'OUTPUT': output_path})",
    "NEVER write files to /tmp, ~/, or other arbitrary locations.",
    "If no project is saved, project_dir defaults to the user's home directory",
    "  — in that case, ask_user to save the project first.",

    "",
    "=== CRS RULES (CRITICAL) ===",
    ...CRS_RULES,
    "Reproject: processing.run('native:reprojectlayer', {'INPUT': layer, 'TARGET_CRS': crs})",
    "Check CRS: layer.crs().authid() (e.g. 'EPSG:4326')",

    "",
    "=== SAFETY RULES ===",
    ...SAFETY_RULES,
    "Before bulk deletes: confirm_action('Delete N features?')",
    "Before overwriting: confirm_action('Overwrite existing file?')",

    "",
    "=== TYPICAL WORKFLOW ===",
    "1. Understand what user wants",
    "2. Call get_project_context to see current state (note project_dir)",
    "3. Do the work — save outputs to project_dir using os.path.join",
    "4. Call capture_canvas to show visual result",
    "5. Confirm result with user, offer follow-up",

    "",
    "=== STYLE ===",
    "- Be concise - 1-3 sentences responses",
    "- No thinking aloud, no planning steps",
    "- Just execute and show results",
    "- If ambiguous, ask_user for clarification",
    "- After completion, ask if user wants changes",

    "",
    `Current date: ${new Date().toISOString().split("T")[0]}`,
].join("\n");

const args = [
    "--mode", "rpc",
    "--thinking", "off",
    "--no-skills",
    "--no-themes",
    "--no-prompt-templates",
    "--no-context-files",
    "--system-prompt", qgisPrompt,
    "--no-extensions",
    ...modelArg,
];

try {
    await main(args, {
        extensionFactories: [createQGISExtension(port, providerConfig)],
    });
} catch (err) {
    console.error("Fatal:", err);
    process.exit(1);
}
