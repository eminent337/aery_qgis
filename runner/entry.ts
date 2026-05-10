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

available = [l.name() for l in iface.project().mapLayers().values()]
layers_info = []
for l in iface.project().mapLayers().values():
    layers_info.append({
        "name": l.name(),
        "type": str(l.type()),
        "crs": l.crs().authid() if l.crs() else "unknown",
        "features": l.featureCount() if hasattr(l, "featureCount") else "N/A",
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

layer = next((l for l in iface.project().mapLayers().values() if l.name() == "${params.layer_name}"), None)
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

source = next((l for l in iface.project().mapLayers().values() if l.name() == "${params.source_layer}"), None)
ref = next((l for l in iface.project().mapLayers().values() if l.name() == "${params.reference_layer}"), None)
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

layer = next((l for l in iface.project().mapLayers().values() if l.name() == "${params.layer_name}"), None)
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

layer = next((l for l in iface.project().mapLayers().values() if l.name() == "${params.layer_name}"), None)
if layer is None:
    available = [l.name() for l in iface.project().mapLayers().values()]
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
result = f"Loaded {l.name()} ({l.featureCount()} features)"
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
settings.render(painter)
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
                        { type: "image", source: { type: "base64", media_type: "image/png", data: b64 } },
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
        ee.Initialize(project=ee.data.getCloudCredentials()?.get("project_id") or "")
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
