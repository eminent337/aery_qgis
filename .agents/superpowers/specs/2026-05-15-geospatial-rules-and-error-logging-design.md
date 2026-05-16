# Aery QGIS Plugin Improvements Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix silent post-execution graph-engine failures and eliminate duplicated system prompts by introducing a single JSON source of truth for geospatial rules consumed by both Python (QGIS plugin) and TypeScript (runner binary).

**Architecture:** Two independent changes with no shared state — graph-engine hardening is a Python-only correctness fix; the JSON prompt schema introduces a data file read by both Python and TS, removing duplication without coupling the two runtimes.

**Tech Stack:** Python (PyQt6, stdlib json), TypeScript (Node.js `fs` + `JSON.parse`), QGIS 4 API.

---

## Chunk 1: Graph-Engine Hardening

Files touched: `aery_plugin/qgis_executor.py`

### Task 1.1: Extract graph hooks into a testable method

**Files:**
- Modify: `aery_plugin/qgis_executor.py:379-405`

- [ ] **Step 1: Write the failing test**
  Add to `tests/test_qgis_executor.py` after the existing executor test class:
  ```python
  def test_graph_hooks_log_error_on_failure(monkeypatch):
      """When graph_engine raises, executor logs to QgsMessageLog and does NOT reraise."""
      from aery_plugin import qgis_executor as qe

      # Force graph_engine import to raise
      monkeypatch.setitem(sys.modules, "aery_plugin.graph_engine", None)

      # Patch QgsMessageLog to capture calls
      calls = []
      class FakeQML:
        @staticmethod
        def logMessage(msg, tag, level): calls.append((msg, tag, level))

      monkeypatch.setattr(qe, "_log_graph_error", lambda msg: calls.append(msg), raising=False)

      # Execute normal code — should succeed even though graph_engine is broken
      result = exec.execute("x = 1", timeout=5)
      assert result["success"] is True
      assert len(calls) > 0, "graph hook failure should be logged"
  ```

- [ ] **Step 2: Verify test fails**
  Run: `cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && python3 -m pytest tests/test_qgis_executor.py::test_graph_hooks_log_error_on_failure -v`
  Expected: FAIL — `AttributeError` or `assert len(calls) > 0` fails because hooks currently `pass`.

- [ ] **Step 3: Extract `_record_graph_hooks` method and fix error handling**
  In `qgis_executor.py`, replace lines 382-405:
  ```python
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
              r'["\']([^"\']+\.(?:tif|tiff|gpkg|shp|geojson|csv|pdf|png))["\']',
              code,
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
          # Log every hook failure; never silently swallow.
          try:
              from qgis.core import QgsMessageLog, Qgis
              QgsMessageLog.logMessage(
                  f"[Aery] graph hook failed: {exc}",
                  "Aery",
                  Qgis.MessageLevel.Warning,
              )
          except ImportError:
              pass  # not in QGIS context (tests)
  ```
  Then in `_process_queue` replace lines 382-405 with:
  ```python
  self._record_graph_hooks(project_dir, code, response, metadata)
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run: `cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && python3 -m pytest tests/test_qgis_executor.py -v`
  Expected: All existing tests PASS + new test PASSES.

- [ ] **Step 5: Commit**
  ```bash
  cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && \
  git add aery_plugin/qgis_executor.py tests/test_qgis_executor.py && \
  git commit -m "feat: graph engine hooks log failures instead of silently passing"
  ```

---

## Chunk 2: Shared Geospatial Rules JSON Schema

Files created/modified:
- **Create:** `aery_plugin/resources/geospatial_rules.json`
- **Modify:** `aery_plugin/rpc_bridge.py` (drop `_prompt_core`, `_prompt_advanced`, `_prompt_beyond`; replace with JSON reader)
- **Modify:** `runner/entry.ts` (drop inline CRS_RULES, SAFETY_RULES, PROCESSING_ALGORITHMS, qgisPrompt array; read from JSON at startup)

### Task 2.1: Create the JSON schema

- [ ] **Step 1: Write the JSON file**
  Create `aery_plugin/resources/geospatial_rules.json`:
  ```json
  {
    "identity": {
      "role": "elite geospatial AI operating inside QGIS with full Python access",
      "capabilities": "spatial analysis, remote sensing, ML, 3D, web scraping, automation",
      "workflow": "get_project_context → plan → execute → capture_canvas → confirm"
    },
    "workflow_steps": [
      "UNDERSTAND: Analyze the user's request",
      "EXPLORE: Get project context, inspect layers, check CRS",
      "PLAN: Select the right operations, handle CRS mismatches",
      "EXECUTE: Discover/describe/run live QGIS algorithms before falling back to custom Python",
      "VISUALIZE: Always capture_canvas after changes",
      "CONFIRM: Show results, offer follow-up operations"
    ],
    "processing_search_filter": "# Search algorithms by keyword instead of listing all:\nimport processing\nalgs = [a.id() for a in processing.algorithmProvider('native').algorithms() if 'buffer' in a.id()]\n# Or search across all providers:\nfrom qgis.core import QgsApplication\nalgs = [a.id() for a in QgsApplication.processingRegistry().algorithms() if 'interpolat' in a.id().lower()]\nresult = algs[:20]  # return top 20 matches",
    "globals_available": [
      "iface, project_dir, processing, QgsProject, QgsVectorLayer, QgsRasterLayer",
      "QgsFeature, QgsGeometry, QgsField, QgsFields, QgsVectorFileWriter",
      "QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea",
      "QgsExpression, QgsFeatureRequest, QgsSpatialIndex, QgsWkbTypes",
      "QgsSymbol, QgsMarkerSymbol, QgsSingleSymbolRenderer, QgsRendererRange",
      "QgsPalLayerSettings, QgsTextFormat, QgsMessageLog, QgsRectangle",
      "QgsPointXY, QgsPoint, QgsLayerTreeGroup, QgsLayerTreeLayer",
      "QgsMapSettings, QgsVectorLayerUtils, QgsVectorDataProvider",
      "QgsRasterBandStats, QgsProcessingFeedback",
      "Qt, QColor, QFont, QVariant, QImage, QPainter",
      "os, json, math, re, csv, pathlib, datetime, base64",
      "subprocess, shutil, tempfile, statistics, collections, itertools",
      "np (numpy), pd (pandas), plt (matplotlib), gpd (geopandas)",
      "rasterio, fiona, pyproj, nx (networkx), scipy, sklearn"
    ],
    "globals_note": "Set result = <value> to return data to the agent.",
    "crs_rules": [
      "Local <50km: UTM zone. Continental: Albers Equal Area. Global: EPSG:4326. Web: EPSG:3857.",
      "Always check layer.crs().authid(). Reproject before spatial ops if CRS differs.",
      "Distance/area needs projected CRS.",
      "EPSG:4326 = WGS84 (lat/lon), EPSG:3857 = Web Mercator",
      "For Africa: EPSG:32632 (UTM 32N), EPSG:32633 (UTM 33N), EPSG:20935 (Congo Basins)",
      "For local analysis, use projected CRS (UTM) not geographic (lat/lon)"
    ],
    "safety_rules": [
      "ALWAYS warn before: deleting layers, overwriting files, dropping columns",
      "Request confirmation for operations affecting >10,000 features",
      "Show preview of changes before applying bulk updates"
    ],
    "processing_patterns": {
      "buffer": "processing.run('native:buffer', {'INPUT':lyr,'DISTANCE':100,'SEGMENTS':5,'OUTPUT':'TEMPORARY_OUTPUT'})",
      "clip": "processing.run('native:clip', {'INPUT':src,'OVERLAY':mask,'OUTPUT':'TEMPORARY_OUTPUT'})",
      "intersect": "processing.run('native:intersection', {'INPUT':a,'OVERLAY':b,'OUTPUT':'TEMPORARY_OUTPUT'})",
      "dissolve": "processing.run('native:dissolve', {'INPUT':lyr,'FIELD':['cat'],'OUTPUT':'TEMPORARY_OUTPUT'})",
      "reproject": "processing.run('native:reprojectlayer', {'INPUT':lyr,'TARGET_CRS':'EPSG:4326','OUTPUT':'TEMPORARY_OUTPUT'})",
      "fix_geom": "processing.run('native:fixgeometries', {'INPUT':lyr,'OUTPUT':'TEMPORARY_OUTPUT'})",
      "field_calc": "processing.run('native:fieldcalculator', {'INPUT':lyr,'FIELD_NAME':'area_m2','FORMULA':'$area','OUTPUT':'TEMPORARY_OUTPUT'})",
      "zonal_stats": "processing.run('native:zonalstatisticsfb', {'INPUT':zones,'INPUT_RASTER':raster,'RASTER_BAND':1,'STATISTICS':[0,1,2],'OUTPUT':'TEMPORARY_OUTPUT'})",
      "voronoi": "processing.run('native:voronoipolygons', {'INPUT':pts,'OUTPUT':'TEMPORARY_OUTPUT'})",
      "heatmap": "processing.run('qgis:heatmapkerneldensityestimation', {'INPUT':pts,'RADIUS':500,'OUTPUT':f'{project_dir}/heatmap.tif'})",
      "raster_calc": "processing.run('native:rastercalc', {'LAYERS':[r1,r2],'EXPRESSION':'r1@1 - r2@1','OUTPUT':f'{project_dir}/diff.tif'})",
      "join_by_loc": "processing.run('native:joinbylocation', {'INPUT':target,'JOIN':source,'PREDICATE':[0],'OUTPUT':'TEMPORARY_OUTPUT'})",
      "shortest_path": "processing.run('native:shortestpathpointtopoint', {'INPUT':network,'START_POINT':start,'END_POINT':end,'OUTPUT':'TEMPORARY_OUTPUT'})",
      "interpolate": "processing.run('qgis:idwinterpolation', {'INTERPOLATION_DATA':f'{lyr}::~::0::~::2','PIXEL_SIZE':100,'OUTPUT':f'{project_dir}/idw.tif'})"
    },
    "styling_code": {
      "graduated_color": "from qgis.core import QgsGraduatedSymbolRenderer, QgsClassificationQuantile\nrenderer = QgsGraduatedSymbolRenderer.createRenderer(layer, 'population', 5, QgsClassificationQuantile(), QgsSymbol.defaultSymbol(layer.geometryType()))\nlayer.setRenderer(renderer); layer.triggerRepaint()",
      "single_color": "sym = QgsSymbol.defaultSymbol(layer.geometryType())\nsym.setColor(QColor('#e74c3c')); layer.setRenderer(QgsSingleSymbolRenderer(sym)); layer.triggerRepaint()",
      "labels": "pal = QgsPalLayerSettings(); pal.fieldName = 'name'; pal.enabled = True\nlayer.setLabeling(QgsVectorLayerSimpleLabeling(pal)); layer.setLabelsEnabled(True); layer.triggerRepaint()"
    },
    "error_recovery": [
      "CRS mismatch → reproject and retry",
      "Invalid geometry → fixgeometries and retry",
      "Empty layer → check featureCount() before running",
      "Parameter type error → pass layer.source() string instead of layer object"
    ],
    "workspace_rules": {
      "all_output_files": "ALL output files go inside project_dir",
      "always_load_results": "ALWAYS load results to canvas and call capture_canvas after every operation",
      "apply_color_ramp": "Apply appropriate color ramp to rasters before capture",
      "warn_before_delete": "Warn before deleting layers or overwriting files",
      "be_concise": "Be concise: execute first, explain after",
      "if_ambiguous": "If ambiguous, ask_user before guessing"
    }
  }
  ```

- [ ] **Step 2: Validate JSON**
  Run: `cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && python3 -c "import json; json.load(open('aery_plugin/resources/geospatial_rules.json')); print('Valid JSON')"`
  Expected: `Valid JSON`

---

### Task 2.2: Python side — read prompt from JSON

- [ ] **Step 1: Write the failing test**
  Add to `tests/test_rpc_bridge.py`:
  ```python
  def test_load_qgis_system_prompt_reads_from_json(tmp_path, monkeypatch):
      """_load_qgis_system_prompt must load from geospatial_rules.json, not hardcoded."""
      import aery_plugin.rpc_bridge as rb

      # Point to a temp JSON with known content
      rules = {"identity": {"role": "test_role"}}
      json_path = tmp_path / "geospatial_rules.json"
      json_path.write_text(json.dumps(rules))

      bogus = rb.RPCBridge("/dev/null", 0)
      bogus._get_resources_dir = lambda: str(tmp_path)
      prompt = bogus._load_qgis_system_prompt()
      assert "test_role" in prompt
      # Must NOT contain old hardcoded literals if absent from test JSON
      assert "elite geospatial AI" not in prompt
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && python3 -m pytest tests/test_rpc_bridge.py::test_load_qgis_system_prompt_reads_from_json -v`
  Expected: FAIL — `assert "elite geospatial AI" not in prompt` fails because prompt is still hardcoded.

- [ ] **Step 3: Implement JSON reader in rpc_bridge.py**
  In `rpc_bridge.py`, add a method after `_ensure_agent_dir()`:
  ```python
  def _load_rules(self) -> dict[str, Any]:
      """Load geospatial rules from resources/geospatial_rules.json."""
      resources_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")
      rules_path = os.path.join(resources_dir, "geospatial_rules.json")
      with open(rules_path) as f:
          return json.load(f)
  ```
  Replace `_load_qgis_system_prompt`, `_prompt_core`, `_prompt_advanced`, `_prompt_beyond` (lines 119-422) with:
  ```python
  def _load_qgis_system_prompt(self) -> str:
      """Build the geospatial system prompt from the shared JSON rulebook."""
      rules = self._load_rules()
      lines = [
          f"You are {rules['identity']['role']}.",
          f"You can do anything: {rules['identity']['capabilities']}.",
          f"Workflow: {rules['identity']['workflow']}",
          "",
          "=== QGIS WORKFLOW ===",
      ]
      for step in rules.get("workflow_steps", []):
          lines.append(step)
      lines.append("")
      lines.append("=== AVAILABLE TOOLS ===")
      lines.append("Run list_processing_algorithms → describe_processing_algorithm → run_processing_algorithm when you need QGIS tools themselves.")
      lines.append("")
      lines.append("=== QGIS PROCESSING SEARCH FILTER ===")
      lines.append(rules.get("processing_search_filter", ""))
      lines.append("")
      lines.append("=== GLOBALS ALWAYS AVAILABLE IN run_qgis_code ===")
      for g in rules.get("globals_available", []):
          lines.append(g)
      lines.append(rules.get("globals_note", ""))
      lines.append("")
      lines.append("=== CRS RULES ===")
      for r in rules.get("crs_rules", []):
          lines.append(r)
      lines.append("")
      lines.append("=== PROCESSING PATTERNS ===")
      for name, snippet in rules.get("processing_patterns", {}).items():
          lines.append(f"# {name}:")
          lines.append(snippet)
      lines.append("")
      lines.append("=== STYLING IN CODE ===")
      for name, snippet in rules.get("styling_code", {}).items():
          lines.append(f"# {name}:")
          lines.append(snippet)
      lines.append("")
      lines.append("=== ERROR RECOVERY ===")
      for r in rules.get("error_recovery", []):
          lines.append(r)
      return "\n".join(lines) + "\n"
  ```

- [ ] **Step 3: Run test to verify it passes**
  Run: `cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && python3 -m pytest tests/test_rpc_bridge.py -v`
  Expected: All tests PASS, including new test.

- [ ] **Step 4: Commit**
  ```bash
  cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && \
  git add aery_plugin/resources/geospatial_rules.json aery_plugin/rpc_bridge.py tests/test_rpc_bridge.py && \
  git commit -m "feat: shared geospatial_rules.json as single prompt source for Python bridge"
  ```

---

### Task 2.3: TypeScript side — read prompt from JSON

- [ ] **Step 1: Add JSON reader to entry.ts**
  Before line 87 (where CRS_RULES currently is), add:
  ```typescript
  // ── RULEBOOK ────────────────────────────────────────────────────────────────

  interface GeospatialRules {
    identity: { role: string; capabilities: string; workflow: string };
    workflow_steps: string[];
    processing_search_filter: string;
    globals_available: string[];
    globals_note: string;
    crs_rules: string[];
    safety_rules: string[];
    processing_patterns: Record<string, string>;
    styling_code: Record<string, string>;
    error_recovery: string[];
    workspace_rules: Record<string, string>;
  }

  let geospatialRules: GeospatialRules | null = null;

  function loadGeospatialRules(): GeospatialRules {
    if (geospatialRules) return geospatialRules;
    try {
      const rulesJson = fs.readFileSync(
        path.join(path.dirname(path.resolve(import.meta.url)), "..", "aery_plugin", "resources", "geospatial_rules.json"),
        "utf-8",
      );
      geospatialRules = JSON.parse(rulesJson) as GeospatialRules;
    } catch {
      geospatialRules = {
        identity: { role: "geospatial AI assistant inside QGIS", capabilities: "spatial analysis, remote sensing, ML", workflow: "Understand → Explore → Plan → Execute → Visualize → Confirm" },
        workflow_steps: ["1. UNDERSTAND", "2. EXPLORE", "3. PLAN", "4. EXECUTE", "5. VISUALIZE", "6. CONFIRM"],
        processing_search_filter: "",
        globals_available: [],
        globals_note: "",
        crs_rules: [],
        safety_rules: [],
        processing_patterns: {},
        styling_code: {},
        error_recovery: [],
        workspace_rules: {},
      };
    }
    return geospatialRules;
  }
  ```

- [ ] **Step 2: Replace CRS_RULES / SAFETY_RULES / PROCESSING_ALGORITHMS references**
  Remove lines 87-135 (`CRS_RULES`, `SAFETY_RULES`, `PROCESSING_ALGORITHMS` array).
  Remove lines 1635-area `Analyze Geospatial Task` label that duplicates workflow steps — the list now comes from JSON.
  Remove lines 2538-2667 (`algoList`, `qgisPrompt` array and its `...CRS_RULES`/`...SAFETY_RULES` spread).
  Replace lines ~2660-2670 (where `qgisPrompt` was spread into `--system-prompt`) with:
  ```typescript
  const rules = loadGeospatialRules();
  const promptLines = [
    `You are ${rules.identity.role}.`,
    `Your workflow: ${rules.identity.workflow}.`,
    "",
    "=== QGIS WORKFLOW ===",
    ...rules.workflow_steps,
    "",
    "=== AVAILABLE TOOLS ===",
    ...(toolsList || []),
    "",
    "Prefer list_processing_algorithms → describe → run when you need QGIS tools themselves.",
    "",
    rules.processing_search_filter || "",
    "",
    "=== GLOBALS ALWAYS AVAILABLE IN run_qgis_code ===",
    ...rules.globals_available,
    rules.globals_note || "",
    "",
    "=== CRS RULES ===",
    ...rules.crs_rules,
    ...rules.safety_rules,
    "",
    "=== PROCESSING PATTERNS ===",
    ...Object.entries(rules.processing_patterns).flatMap(([k, v]) => [`# ${k}:`, v]),
    "",
    "=== STYLING IN CODE ===",
    ...Object.entries(rules.styling_code).flatMap(([k, v]) => [`# ${k}:`, v]),
    "",
    "=== ERROR RECOVERY ===",
    ...rules.error_recovery,
  ];
  const systemPrompt = promptLines.join("\n");
  ```
  Then pass `systemPrompt` where `qgisPrompt` was previously spread.

- [ ] **Step 3: Verify bundle builds**
  Run: `cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && ls aery_plugin/resources/geospatial_rules.json && echo "JSON exists"`

- [ ] **Step 4: Commit**
  ```bash
  cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && \
  git add runner/entry.ts && \
  git commit -m "refactor: runner/entry.ts reads geospatial_rules.json instead of hardcoded constants"
  ```

---

### Task 2.4: Add smoke test for JSON roundtrip

- [ ] **Step 1: Write the test**
  Add to `tests/test_rpc_bridge.py`:
  ```python
  def test_geospatial_rules_json_is_valid_and_complete():
      """geospatial_rules.json must be valid JSON with all required top-level keys."""
      from pathlib import Path
      import json

      rules_path = Path(__file__).parent.parent / "aery_plugin" / "resources" / "geospatial_rules.json"
      data = json.loads(rules_path.read_text())

      required = ["identity", "workflow_steps", "crs_rules", "safety_rules",
                  "processing_patterns", "error_recovery"]
      for key in required:
          assert key in data, f"missing required key: {key}"
      assert len(data["crs_rules"]) > 0, "crs_rules must not be empty"
      assert len(data["processing_patterns"]) > 0, "processing_patterns must not be empty"
  ```

- [ ] **Step 2: Run to verify**
  Run: `cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && python3 -m pytest tests/test_rpc_bridge.py::test_geospatial_rules_json_is_valid_and_complete -v`
  Expected: PASS

- [ ] **Step 3: Commit**
  ```bash
  cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && \
  git add tests/test_rpc_bridge.py && \
  git commit -m "test: geospatial_rules.json schema validation"
  ```

---

## Chunk 3: Verification

After all changes land, run the full test suite and a quick manual sanity check:

- [ ] **Run full test suite**
  ```bash
  cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && python3 -m pytest tests/ -v
  ```
  Expected: All tests pass (plugin ×7, chat_panel ×17, qgis_executor ×7, rpc_bridge ≥9, provider_settings, integration).

- [ ] **Verify graph report readback** (using graphify)
  ```bash
  cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && PYTHON=$(cat graphify-out/.graphify_python) && \
  "$PYTHON" -c "from pathlib import Path; import json; r=json.loads(Path('graphify-out/graph.json').read_text()); print(f'{r[\"nodes\"]} nodes, {r[\"links\"]} edges OK' if r['nodes'] > 0 else 'FAIL')"
  ```

- [ ] **Final commit (one message, all chunks)**
  ```bash
  git add -A && git commit -m "refactor: shared geospatial_rules.json, graph-engine error logging

  - Extract geospatial rules (CRS, safety, patterns, styling, GEE, SAR, GDAL, data sources)
    into a single JSON file consumed by both Python bridge and TS runner
  - Replace 304 lines of hardcoded prompt duplication in rpc_bridge.py + runner/entry.ts
  - Hardening: _record_graph_hooks logs failures via QgsMessageLog instead of silently passing
  - Properly isolate graph-engine post-exec hook; GraphEngine import failure no longer
    masks itself as a clean execution result"
  ```

---
