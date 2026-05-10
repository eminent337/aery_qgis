#!/usr/bin/env node
/**
 * Aery for QGIS — standalone binary entry point.
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
import * as path from "node:path";

// ── QGIS TCP helpers ────────────────────────────────────

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

// ── Provider config loader ──────────────────────────────

function loadProviderConfig() {
    const fileIndex = process.argv.indexOf("--provider-file");
    if (fileIndex === -1 || fileIndex + 1 >= process.argv.length) return null;

    const filePath = process.argv[fileIndex + 1];
    try {
        const raw = fs.readFileSync(filePath, "utf-8");
        const config = JSON.parse(raw);
        // Remove the file after reading
        try { fs.unlinkSync(filePath); } catch {}
        return config;
    } catch {
        return null;
    }
}

// ── Extension factory ────────────────────────────────────

function createQGISExtension(port, providerConfig) {
    return (aery) => {
        // ── Register provider from config ──
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

        // ── run_qgis_code (primary) ──
        aery.registerTool({
            name: "run_qgis_code",
            label: "Run QGIS Code",
            description:
                "Execute Python code inside QGIS. Full QGIS API: QgsProject, QgsVectorLayer, " +
                "QgsRasterLayer, processing, iface. Store result in `result` variable.",
            promptSnippet: "Execute QGIS Python code for geospatial operations",
            promptGuidelines: [
                "Use run_qgis_code for ALL QGIS operations",
                "Store results in the `result` Python variable",
                "Add layers: QgsProject.instance().addMapLayer(layer)",
                "Import processing: import processing",
                "Use layer.selectByExpression('expression') for selections",
                "Use processing.run('native:buffer', {...}) for geoalgorithms",
            ],
            parameters: {
                type: "object",
                properties: {
                    code: { type: "string", description: "Python code to execute inside QGIS" },
                    timeout: { type: "number", description: "Timeout in seconds" },
                },
                required: ["code"],
            },
            async execute(_id, params) {
                const r = await qgisRequest(port, "run_code", { code: params.code, timeout: params.timeout || 300 });
                const text = typeof r.result === "object" ? JSON.stringify(r.result, null, 2) : String(r.result ?? "");
                return { content: [{ type: "text", text }], details: {} };
            },
        });

        // ── get_project_context ──
        aery.registerTool({
            name: "get_project_context",
            label: "Get Project Context",
            description: "Full snapshot of the current QGIS project: layers, CRS, extent, selection.",
            promptSnippet: "Get QGIS project context",
            parameters: { type: "object", properties: {} },
            async execute() {
                const r = await qgisRequest(port, "get_project_context", {});
                return { content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }], details: {} };
            },
        });

        // ── run_processing ──
        aery.registerTool({
            name: "run_processing",
            label: "Run Processing Algorithm",
            description: "Run any QGIS Processing algorithm by name with parameters.",
            promptSnippet: "Run a QGIS Processing algorithm (buffer, clip, intersect...)",
            parameters: {
                type: "object",
                properties: {
                    algorithm: { type: "string", description: "e.g. native:buffer, native:clip" },
                    parameters: { type: "object", additionalProperties: true },
                },
                required: ["algorithm", "parameters"],
            },
            async execute(_id, params) {
                const code = `import processing\nfeedback = QgsProcessingFeedback()\nresult = processing.run("${params.algorithm}", ${JSON.stringify(params.parameters)})`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: JSON.stringify(r.result, null, 2) }], details: {} };
            },
        });

        // ── add_layer ──
        aery.registerTool({
            name: "add_layer",
            label: "Add Layer",
            description: "Load a file (GeoJSON, Shapefile, GeoPackage, GeoTIFF) into QGIS.",
            promptSnippet: "Load a geospatial file into QGIS",
            parameters: {
                type: "object",
                properties: {
                    path: { type: "string", description: "Absolute file path" },
                    name: { type: "string", description: "Display name" },
                },
                required: ["path"],
            },
            async execute(_id, params) {
                const p = params.path;
                const isRaster = /\.(tif|tiff|dem)$/i.test(p);
                const code = `
from qgis.core import QgsRasterLayer, QgsVectorLayer, QgsProject
import os
l = ${isRaster ? `QgsRasterLayer("${p}", os.path.basename("${p}"))` : `QgsVectorLayer("${p}", os.path.basename("${p}"), 'ogr')`}
if not l.isValid(): raise ValueError(f"Failed: {l.lastError()}")
${params.name ? `l.setName("${params.name}")` : ""}
QgsProject.instance().addMapLayer(l)
result = f"Loaded {l.name()} ({l.featureCount()} features)"`;
                const r = await qgisRequest(port, "run_code", { code });
                return { content: [{ type: "text", text: r.result }], details: {} };
            },
        });

        // ── capture_canvas ──
        aery.registerTool({
            name: "capture_canvas",
            label: "Capture Canvas",
            description: "Capture the current QGIS map canvas as an image. Returns a screenshot showing layers, labels, and styling.",
            promptSnippet: "Capture the QGIS map canvas to see the current map state",
            parameters: { type: "object", properties: {} },
            async execute(_id, params) {
                const code = `
from PyQt6.QtCore import QBuffer, QIODevice
import base64

canvas = iface.mapCanvas()
pixmap = canvas.grab()
buf = QBuffer()
buf.open(QIODevice.OpenModeFlag.WriteOnly)
pixmap.save(buf, "PNG")
buf.close()
result = base64.b64encode(bytes(buf.data())).decode()
`;
                const r = await qgisRequest(port, "run_code", { code });
                const b64 = typeof r.result === "string" ? r.result : "";
                return {
                    content: [
                        { type: "text", text: "Canvas captured." },
                        { type: "image", source: { type: "base64", media_type: "image/png", data: b64 } },
                    ],
                    details: {},
                };
            },
        });

        // ── bash ──
        aery.registerTool({
            name: "bash",
            label: "Shell Command",
            description: "Execute a shell command on the QGIS host system (GDAL/OGR, pip, file ops).",
            promptSnippet: "Run shell commands",
            parameters: {
                type: "object",
                properties: {
                    command: { type: "string", description: "Shell command to execute (e.g. gdalinfo, ogr2ogr, pip install)" },
                    timeout: { type: "number", description: "Timeout in seconds" },
                },
                required: ["command"],
            },
            async execute(_id, params) {
                const code = `
import subprocess, json
r = subprocess.run(${JSON.stringify(params.command)}, shell=True, capture_output=True, text=True, timeout=${params.timeout || 60})
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

        // ── confirm_action ──
        aery.registerTool({
            name: "confirm_action",
            label: "Confirm Action",
            description: "Ask user to confirm before destructive operations (delete layer, overwrite file).",
            promptSnippet: "Confirm a destructive QGIS action",
            parameters: {
                type: "object",
                properties: {
                    message: { type: "string", description: "Confirmation message" },
                },
                required: ["message"],
            },
            async execute(_id, params, _sig, _upd, ctx) {
                const ok = await ctx.ui.confirm("Confirm QGIS Action", params.message);
                return { content: [{ type: "text", text: ok ? "Confirmed" : "Cancelled" }], details: { confirmed: ok } };
            },
        });

        // ── web_search ──
        aery.registerTool({
            name: "web_search",
            label: "Web Search",
            description: "Search the web for information (satellite data sources, documentation, APIs).",
            promptSnippet: "Search the web for geospatial data or information",
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
                // Extract meaningful text: skip HTML tags, keep result links + snippets
                const text = html
                    .replace(/<[^>]+>/g, " ")
                    .replace(/\s+/g, " ")
                    .trim()
                    .slice(0, 8000);
                return { content: [{ type: "text", text: text || "(no results)" }], details: {} };
            },
        });

        // ── ask_user ──
        aery.registerTool({
            name: "ask_user",
            label: "Ask User",
            description: "Ask the user a question and get a free-form text response. Use when you need clarification, feedback on results, or decisions before proceeding.",
            promptSnippet: "Ask the user a question",
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

        // ── run_gee_code ──
        aery.registerTool({
            name: "run_gee_code",
            label: "Run Google Earth Engine Code",
            description:
                "Execute Google Earth Engine Python code inside QGIS. Handles authentication, " +
                "loads results as QGIS layers. Install earthengine-api automatically if missing.",
            promptSnippet: "Execute Google Earth Engine code and load results into QGIS",
            promptGuidelines: [
                "Use run_gee_code for any Google Earth Engine operation",
                "Store results in the `result` Python variable",
                "gee_result (an ee.Image or ee.FeatureCollection) is automatically loaded as a QGIS layer",
                "For images: export via ee.Image.getThumbURL or convert to numpy array",
                "For feature collections: convert to GeoJSON via gee_result.getInfo() and load via QgsVectorLayer",
            ],
            parameters: {
                type: "object",
                properties: {
                    code: {
                        type: "string",
                        description:
                            "Python code using Earth Engine. GEE is already initialized as `ee`. " +
                            "Store the final ee.Image or ee.FeatureCollection in `gee_result`. " +
                            "The result variable is automatically captured.",
                    },
                },
                required: ["code"],
            },
            async execute(_id, params) {
                const wrapped = `
import subprocess, json, sys, importlib.util

# Ensure earthengine-api is installed
spec = importlib.util.find_spec("ee")
if spec is None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "earthengine-api", "-q"])

import ee

# Initialize (attempt both with and without explicit credentials)
try:
    ee.Initialize()
except Exception:
    try:
        ee.Initialize(project=ee.data.getCloudCredentials()?.get("project_id") or "")
    except Exception:
        pass  # Will fail with clear error message

${params.code}

# Capture result
result = gee_result.getInfo() if "gee_result" in dir() else str(result)
`;
                const r = await qgisRequest(port, "run_code", { code: wrapped, timeout: 120 });
                const text = typeof r.result === "object" ? JSON.stringify(r.result, null, 2) : String(r.result ?? "");
                return { content: [{ type: "text", text }], details: {} };
            },
        });

        // ── register_tool ──
        aery.registerTool({
            name: "register_tool",
            label: "Register Tool",
            description: "Create a reusable QGIS tool. Use PARAMS_PLACEHOLDER in code for tool parameters.",
            promptSnippet: "Create a reusable QGIS tool",
            parameters: {
                type: "object",
                properties: {
                    name: { type: "string", description: "Tool name (snake_case)" },
                    description: { type: "string", description: "Description" },
                    code: { type: "string", description: "Python code with PARAMS_PLACEHOLDER" },
                    parameters_schema: { type: "object", description: "JSON Schema" },
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

        // ── Inject QGIS context before each turn ──
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

// ── Entry point ──────────────────────────────────────────

const port = parseInt(process.argv[2], 10);
if (!port || port < 1024 || port > 65535) {
    console.error("Usage: aery-qgis-runner <port> [--provider-file <path>]");
    process.exit(1);
}

const providerConfig = loadProviderConfig();

// If provider configured, auto-select its model as default
const modelArg = providerConfig?.model
    ? ["--model", `qgis/${providerConfig.model}`]
    : [];

// QGIS-focused system prompt — no Aery infra, no skills, no superpowers
const qgisPrompt = [
    "You are a geospatial AI assistant operating inside QGIS. Your only purpose is to help the user with GIS tasks.",
    "",
    "Available tools:",
    "- bash: Execute shell commands (GDAL/OGR, system operations, pip install)",
    "- run_qgis_code: Execute Python code in QGIS (primary tool for ALL geospatial work)",
    "- get_project_context: Get current QGIS project info (layers, CRS, extent)",
    "- run_processing: Run QGIS Processing algorithms (buffer, clip, intersect)",
    "- add_layer: Load a file (GeoJSON, Shapefile, GeoTIFF) into QGIS",
    "- confirm_action: Ask user to confirm destructive operations",
    "- capture_canvas: Capture the QGIS map canvas as an image to see current map state",
    "- web_search: Search the web for information (satellite data, APIs, documentation)",
    "- ask_user: Ask you a question and wait for your free-form response",
    "- run_gee_code: Execute Google Earth Engine code and load results into QGIS",
    "- register_tool: Save a QGIS Python snippet as a reusable tool",
    "",
    "Guidelines:",
    "- Use run_qgis_code for ALL QGIS operations",
    "- Be extremely concise — respond in 1-3 sentences",
    "- No thinking out loud, no planning steps, no reasoning chains",
    "- If you need more info, just ask a direct question",
    "- Store Python results in the `result` variable",
    "- Use bash for GDAL/OGR commands and system-level tasks",
    "- Use run_processing as a shortcut for native:buffer, native:clip, etc.",
    "- Never explain what you're going to do — just do it",
    "- If a command fails, examine the error and retry with a fix",
    "- Before starting work, call get_project_context to understand current state",
    "- After ANY operation that changes the map (add layer, style, run processing), call capture_canvas to show the result visually",
    "- Use capture_canvas to verify that your operations had the intended effect",
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
