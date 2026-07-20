"""Tool definitions for the Aery QGIS agent.

Each tool is a dict with name, description, parameters (JSON Schema),
and an execute function that takes params and returns a result.
"""

import asyncio
import json
import os
import re
from typing import Any, Callable, Optional
from aery_plugin.logger import logger

DESTRUCTIVE_TOOLS = {
    "run_qgis_code": ["removeMapLayer", "deleteFeatures", "os.remove", "shutil.rmtree"],
}

PERMISSION_MODES = {"default", "acceptEdits", "bypassPermissions", "plan", "dontAsk"}

# Curated XYZ tile basemaps the assistant can add by name (mirrors GeoLibre's
# NAMED_TILE_BASEMAPS registry). Undocumented/private endpoints (e.g. Google
# mt*.google.com) are intentionally excluded. Add by raw HTTPS XYZ URL is also
# supported through load_basemap.
BASEMAP_REGISTRY = {
    "osm": {
        "label": "OpenStreetMap",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenStreetMap contributors",
    },
    "esri-imagery": {
        "label": "Esri World Imagery",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "© Esri, Maxar, Earthstar Geographics",
    },
    "esri-topo": {
        "label": "Esri World Topo",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        "attribution": "© Esri",
    },
    "opentopomap": {
        "label": "OpenTopoMap",
        "url": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenTopoMap (CC-BY-SA)",
    },
    "carto-dark": {
        "label": "CARTO Dark Matter",
        "url": "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        "attribution": "© CARTO, © OpenStreetMap contributors",
    },
}

# GeoLibre-style fuzzy matcher: exact id, exact label, substring, then token-subset.
def resolve_basemap(reference: str) -> dict | None:
    """Resolve a basemap name/id/url to a registry entry dict.

    Accepts a known name/id (e.g. 'osm', 'esri imagery'), or a raw
    HTTPS XYZ tile URL template. Returns None when nothing matches.
    """
    target = (reference or "").strip().lower()
    if not target:
        return None
    if target.startswith("https://"):
        return {
            "label": reference.strip().split("/")[-1] or "Basemap",
            "url": reference.strip(),
            "attribution": "",
        }
    # exact id
    if target in BASEMAP_REGISTRY:
        return BASEMAP_REGISTRY[target]
    # exact label
    for entry in BASEMAP_REGISTRY.values():
        if entry["label"].lower() == target:
            return entry
    # substring on label
    for entry in BASEMAP_REGISTRY.values():
        if target in entry["label"].lower():
            return entry
    # token-subset match
    query_tokens = [t for t in re.split(r"[^a-z0-9]+", target) if t]
    if query_tokens:
        for entry in BASEMAP_REGISTRY.values():
            haystack = re.split(r"[^a-z0-9]+", f"{entry['label']}".lower())
            if all(tok in haystack for tok in query_tokens):
                return entry
    return None


class HookRegistry:
    def __init__(self):
        self._hooks: dict[str, list[Callable]] = {
            "PreToolUse": [],
            "PostToolUse": [],
            "PostToolUseFailure": [],
        }

    def register(self, event: str, fn: Callable):
        self._hooks[event].append(fn)

    async def emit(self, event: str, **kwargs):
        for fn in self._hooks.get(event, []):
            await fn(**kwargs)


class ToolRegistry:
    """Registry of tools available to the agent."""

    def __init__(self, executor, iface=None, agent=None):
        self.executor = executor
        self.iface = iface
        self._agent = agent
        self._tools: dict[str, dict] = {}
        self._custom_tools: dict[str, dict] = {}
        self._project_context_cache: Optional[str] = None
        self._project_context_dirty = True
        self.hooks = HookRegistry()
        self._permission_mode = "default"
        # Pull the PermissionManager from the agent if provided so that
        # check_permission can read per-session flags (e.g. code_approved).
        self._permissions = getattr(agent, "permissions", None) if agent is not None else None
        self._register_core_tools()
        self._register_default_hooks()
        self._load_custom_tools()
        self._initialized = True
    def _register_default_hooks(self):
        async def pre_validate(**kwargs):
            tool_name = kwargs.get("tool_name", "")
            params = kwargs.get("params", {})
            code = kwargs.get("code")
            validation_error = self.validate_params(tool_name, params)
            if validation_error:
                raise ValueError(validation_error)

        async def pre_permission(**kwargs):
            tool_name = kwargs.get("tool_name", "")
            params = kwargs.get("params", {})
            code = kwargs.get("code")
            perm = self.check_permission(tool_name, params, code)
            if perm["behavior"] == "deny":
                raise PermissionError(perm.get("message", "Permission denied"))
            kwargs["permission"] = perm

        async def post_record(**kwargs):
            pass

        self.hooks.register("PreToolUse", pre_validate)
        self.hooks.register("PreToolUse", pre_permission)
        self.hooks.register("PostToolUse", post_record)

    def _register_core_tools(self):
        self.register({
            "name": "run_qgis_code",
            "description": "Execute Python code inside QGIS. Full access to qgis.core, processing, iface, PyQt6. Store result in `result` variable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                },
                "required": ["code"],
            },
            "execute": self._execute_qgis_code,
        })

        self.register({
            "name": "get_project_context",
            "description": "Get a lightweight summary of the current QGIS project (layers, CRS, visibility). For detailed field lists/attributes, use get_layer_schema.",
            "parameters": {"type": "object", "properties": {}},
            "execute": self._execute_get_project_context,
        })
        self.register({
            "name": "get_layer_schema",
            "description": "Get detailed schema information for a specific layer: fields, extent, feature count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "layer_name": {"type": "string", "description": "Name or ID of the layer"},
                },
                "required": ["layer_name"],
            },
            "execute": self._execute_get_layer_schema,
        })

        self.register({
            "name": "capture_canvas",
            "description": "Capture the QGIS map canvas as a base64 PNG image.",
            "parameters": {"type": "object", "properties": {}},
            "execute": self._execute_capture_canvas,
        })

        self.register({
            "name": "web_search",
            "description": "Search the web for GIS documentation, data portals, and spatial datasets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
            "execute": self._execute_web_search,
        })

        self.register({
            "name": "web_fetch",
            "description": "Fetch and parse content from a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                },
                "required": ["url"],
            },
            "execute": self._execute_web_fetch,
        })

        self.register({
            "name": "run_qgis_algorithms_by_id",
            "description": "Run a QGIS processing algorithm by its ID and parameters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "algorithm_id": {"type": "string", "description": "The QGIS algorithm ID, e.g. native:buffer"},
                    "parameters": {"type": "object", "description": "Parameters for the algorithm"},
                },
                "required": ["algorithm_id"],
            },
            "execute": self._execute_run_qgis_algorithms_by_id,
        })

        self.register({
            "name": "subagent",
            "description": "Spawn a sub-agent to handle a specific subtask.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["single", "chain", "parallel"],
                        "description": "Execution mode",
                    },
                    "task": {"type": "string", "description": "The task description for single mode"},
                    "chain": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string", "description": "Task description"},
                            },
                            "required": ["task"],
                        },
                        "description": "Sequence of tasks for chain mode",
                    },
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string", "description": "Task description"},
                            },
                            "required": ["task"],
                        },
                        "description": "Parallel tasks for parallel mode",
                    },
                    "max_turns": {"type": "integer", "description": "Maximum turns", "default": 5},
                },
                "required": ["mode"],
            },
            "execute": self._execute_subagent,
        })

        self.register({
            "name": "process_workflow",
            "description": "Execute a workflow of processing algorithms to solve a complex task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The processing task description"},
                    "max_turns": {"type": "integer", "description": "Maximum turns", "default": 5},
                },
                "required": ["task"],
            },
            "execute": self._execute_process_workflow,
        })

        # Register geospatial tools (export_webmap, publish_geoserver, set_layer_style,
        # multi_map_layout, save_map_theme, load_map_theme, list_map_themes, refresh_canvas)
        self._register_geospatial_tools()

        # Register graph query tools
        self._register_graph_tools()
        # Register self-extension tools (AERY can register its own tools)
        self._register_self_extension_tools()
        # Register GeoLibre-inspired dedicated view/layer tools
        self._register_dedicated_tools()
        # Register processing discovery tools
        self._register_processing_discovery()
    def register(self, tool: dict):
        """Register a tool definition. Core tools in _always_include cannot be
        overwritten by dynamically-discovered tools (e.g. processing algorithms)."""
        name = tool["name"]
        if getattr(self, "_initialized", False) and name in self._tools and name in self._always_include:
            logger.debug(f"[Aery ToolRegistry] refusing to overwrite core tool '{name}'")
            return
        self._tools[name] = tool
    _always_include = {
        "run_qgis_code", "get_project_context", "capture_canvas",
        "zoom_to_layer", "set_map_extent", "pan_to", "zoom_to_place", "refresh_canvas",
        "toggle_layer_visibility", "set_layer_style", "export_layer",
        "remove_layer", "run_processing_algorithm",
        "web_search", "web_fetch", "run_qgis_algorithms_by_id",
        "subagent", "process_workflow", "get_layer_schema",
        "load_basemap",
    }
    _context_invalidating_tools = {
        "run_qgis_code", "run_processing_algorithm", "load_basemap",
        "add_layer", "remove_layer", "toggle_layer_visibility", "set_layer_style",
        "run_qgis_algorithms_by_id",
    }
    def invalidate_project_context(self) -> None:
        """Mark the cached project snapshot stale after a project mutation."""
        self._project_context_dirty = True
    def retrieve_tools(self, query: str = "") -> list[dict]:
        """Return tools relevant to the query. Currently returns all tools;
        keyword/vector filtering can be added later without changing callers."""
        return self.list_tools()
    def list_tools(self) -> list[dict]:
        """Return all registered tools as OpenAI-format tool definitions."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in self._tools.values()
        ]

    def validate_params(self, name: str, params: dict) -> Optional[str]:
        """Validate tool parameters against the tool's JSON Schema.
        Returns:
            Error message if validation fails, None if valid.
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Unknown tool: {name}"
        schema = tool.get("parameters", {})
        if not schema:
            return None
        errors = []
        # Check required fields
        required = schema.get("required", [])
        for field in required:
            if field not in params:
                errors.append(f"Missing required parameter: '{field}'")
        # Parse cached layer names if available and context is clean
        cached_layers = set()
        perform_layer_check = not self._project_context_dirty and self._project_context_cache is not None
        if perform_layer_check:
            try:
                ctx = json.loads(self._project_context_cache)
                for lyr in ctx.get("layers", []):
                    if "name" in lyr:
                        cached_layers.add(lyr["name"])
                    if "id" in lyr:
                        cached_layers.add(lyr["id"])
            except Exception:
                pass
        # Check property types
        properties = schema.get("properties", {})
        for key, value in list(params.items()):
            prop_schema = properties.get(key, {})
            expected_type = prop_schema.get("type")
            if expected_type and value is not None:
                # Type Coercion for object/array passed as string
                if expected_type in ("object", "array") and isinstance(value, str):
                    try:
                        parsed_val = json.loads(value)
                        params[key] = parsed_val
                        value = parsed_val
                    except Exception:
                        pass
                type_map = {
                    "string": str,
                    "integer": int,
                    "number": (int, float),
                    "boolean": bool,
                    "array": (list, tuple),
                    "object": dict,
                }
                python_type = type_map.get(expected_type)
                if python_type and not isinstance(value, python_type):
                    err_msg = f"Parameter '{key}' expected type '{expected_type}', got '{type(value).__name__}'"
                    if expected_type in ("object", "array"):
                        err_msg += "\nHint: if this is a JSON list/object, pass it as a native list/dict"
                    errors.append(err_msg)
                # Check enum constraint
                if "enum" in prop_schema:
                    if value not in prop_schema["enum"]:
                        errors.append(f"Parameter '{key}' value '{value}' is not in allowed values: {prop_schema['enum']}")
                # Check range constraints (minimum/maximum)
                if isinstance(value, (int, float)):
                    if "minimum" in prop_schema:
                        if value < prop_schema["minimum"]:
                            errors.append(f"Parameter '{key}' value {value} is below minimum {prop_schema['minimum']}")
                    if "maximum" in prop_schema:
                        if value > prop_schema["maximum"]:
                            errors.append(f"Parameter '{key}' value {value} is above maximum {prop_schema['maximum']}")
                # Check pattern constraint
                if "pattern" in prop_schema and isinstance(value, str):
                    if not re.match(prop_schema["pattern"], value):
                        errors.append(f"Parameter '{key}' value does not match pattern {prop_schema['pattern']}")
                # Check format constraint (format == "crs")
                if "format" in prop_schema and prop_schema["format"] == "crs" and isinstance(value, str):
                    if not re.match(r"^epsg:\d+$", value, re.IGNORECASE):
                        errors.append(f"Parameter '{key}' is not a valid CRS format: {value}")
            # Check layer existence if applicable
            if perform_layer_check and isinstance(value, str) and key in ("layer_name", "layer_id", "layer"):
                if value not in cached_layers:
                    errors.append(f"Layer '{value}' not found in current project.")
        return "; ".join(errors) if errors else None

    def set_permission_mode(self, mode: str):
        if mode in PERMISSION_MODES:
            self._permission_mode = mode

    def check_permission(self, tool_name: str, params: dict, code: str = None) -> dict:
        if self._permission_mode == "bypassPermissions":
            return {"behavior": "allow"}
        if self._permission_mode == "acceptEdits":
            return {"behavior": "allow"}
        if self._permission_mode == "dontAsk":
            return {"behavior": "deny", "message": "Tool execution blocked in dontAsk mode"}
        is_destructive = False
        if tool_name in DESTRUCTIVE_TOOLS:
            patterns = DESTRUCTIVE_TOOLS[tool_name]
            check_text = code or json.dumps(params)
            is_destructive = any(p in check_text for p in patterns)
        if not is_destructive:
            return {"behavior": "allow"}
        descriptions = {
            "run_qgis_code": self._describe_qgis_code_risk(code or ""),
        }
        risk_level = "high" if any(p in (code or "") for p in ["removeMapLayer", "deleteFeatures", "shutil.rmtree"]) else "medium"
        return {
            "behavior": "ask",
            "tool_name": tool_name,
            "description": descriptions.get(tool_name, f"Execute {tool_name}"),
            "risk_level": risk_level,
        }

    def _describe_qgis_code_risk(self, code: str) -> str:
        indicators = []
        if "removeMapLayer" in code:
            indicators.append("remove layers from the project")
        if "deleteFeatures" in code:
            indicators.append("delete features from a layer")
        if "os.remove" in code or "os.unlink" in code:
            indicators.append("delete files from disk")
        if "shutil.rmtree" in code:
            indicators.append("delete directories")
        if "deleteAttribute" in code:
            indicators.append("delete attributes from a layer")
        if indicators:
            return "This will " + "; ".join(indicators)
        return "Execute Python code in QGIS"

    async def execute(self, name: str, params: dict, on_progress=None) -> Any:
        """Execute a tool by name with the given parameters."""
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        if name in self._context_invalidating_tools:
            self.invalidate_project_context()
        fn = tool.get("execute")
        if fn is None:
            raise ValueError(f"Tool '{name}' has no execute function")
        try:
            return await fn(params, on_progress=on_progress)
        except TypeError:
            return await fn(params)

    async def _execute_qgis_code(self, params: dict) -> str:
        code = params["code"]
        if params.get("dry_run"):
            preview = self._build_dry_run_preview(code)
            return json.dumps(preview)
        code = self._normalize_qgis4_code(code)
        # Run sync executor in thread pool to avoid blocking the event loop
        result = await asyncio.to_thread(self.executor.execute, code, 300)
        if result.get("success"):
            r = result.get("result")
            return json.dumps(r, indent=2) if isinstance(r, dict) else str(r)
        raise RuntimeError(result.get("error", "Execution failed"))

    @staticmethod
    def _normalize_qgis4_code(code: str) -> str:
        """Rewrite known QGIS 3.x patterns to QGIS 4.0-safe equivalents.

        Only handles the two removed APIs that still appear in model-generated
        run_qgis_code blocks. Dedicated tools (load_basemap, etc.) already
        emit correct QGIS 4.0 code and do NOT need normalization.
        """
        if not code:
            return code
        lines = code.splitlines()
        out = []
        for line in lines:
            stripped = line.strip()
            # QGIS 4.0+: QgsMapCanvas.instance() was removed
            if "QgsMapCanvas.instance()" in stripped:
                line = line.replace("QgsMapCanvas.instance()", "iface.mapCanvas()")
            # QGIS 4.0+: QgsProject.instance().triggerRepaint() was removed
            if stripped.startswith("QgsProject.instance().triggerRepaint()"):
                line = "iface.mapCanvas().refresh()"
            out.append(line)
        return "\n".join(out)

    def _build_dry_run_preview(self, code: str) -> dict:
        from aery_plugin.sandbox import check_ast
        lines = code.splitlines()
        line_count = len(lines)
        if line_count > 30:
            preview_str = "\n".join(lines[:30]) + "\n... [truncated]"
        else:
            preview_str = code
        risk_flags = []
        if any(d in code for d in ["removeMapLayer", "deleteFeatures", "os.remove", "shutil.rmtree"]):
            risk_flags.append("destructive")
        if "processing.run" in code:
            risk_flags.append("executes_algorithm")
        if any(c in code for c in ["mapCanvas", "refresh", "setExtent", "setCenter"]):
            risk_flags.append("modifies_canvas")
        try:
            sandbox_violations = check_ast(code)
        except Exception as e:
            sandbox_violations = [str(e)]
        if "destructive" in risk_flags or len(sandbox_violations) > 0:
            risk = "high"
        elif "executes_algorithm" in risk_flags:
            risk = "medium"
        else:
            risk = "low"
        return {
            "dry_run": True,
            "would_execute": False,
            "risk": risk,
            "risk_flags": risk_flags,
            "line_count": line_count,
            "preview": preview_str,
            "sandbox_violations": sandbox_violations,
        }
    def _build_processing_run_code(self, algorithm_id: str, parameters: dict) -> str:
        return f"""
import processing
from qgis.core import QgsProject
def layer_from_ref(name):
    for l in QgsProject.instance().mapLayers().values():
        if l.name() == name or l.id() == name:
            return l
    return name
alg_params = {repr(parameters)}
resolved = {{}}
for k, v in alg_params.items():
    if isinstance(v, str):
        resolved[k] = layer_from_ref(v)
    else:
        resolved[k] = v
res = processing.run({repr(algorithm_id)}, resolved)
out = {{}}
for k, v in res.items():
    if hasattr(v, 'id'):
        out[k] = v.id()
    else:
        out[k] = str(v)
result = f"Algorithm execution complete: {{out}}"
"""
    async def _execute_run_qgis_algorithms_by_id(self, params: dict) -> str:
        alg_id = params["algorithm_id"]
        alg_params = params.get("parameters", {})
        code = self._build_processing_run_code(alg_id, alg_params)
        return await self._execute_qgis_code({"code": code})
    async def _execute_subagent(self, params: dict) -> str:
        if not self._agent or not getattr(self._agent, "_client", None):
            return "Error: No active LLM client."
        mode = params.get("mode", "single")
        max_turns = params.get("max_turns", 5)
        if mode == "single":
            task = params.get("task", "")
            return await self._run_subagent_loop(task, max_turns, system_prompt=params.get("system"))
        elif mode == "chain":
            chain = params.get("chain", [])
            previous = ""
            results = []
            for step in chain:
                task = step.get("task", "")
                if "{previous}" in task:
                    task = task.replace("{previous}", previous)
                res = await self._run_subagent_loop(task, max_turns)
                previous = res
                results.append(res)
            return "\n".join(results)
        elif mode == "parallel":
            tasks = params.get("tasks", [])
            coros = [self._run_subagent_loop(t.get("task", ""), max_turns) for t in tasks]
            results = await asyncio.gather(*coros)
            return "\n".join(results)
        else:
            return f"Error: Unknown mode '{mode}'"
    async def _execute_process_workflow(self, params: dict) -> str:
        task = params.get("task", "")
        if not task:
            return "Error: 'task' is required."
        processing_prompt = (
            "You are a Processing Expert. Your job is to run QGIS processing algorithms.\n"
            "You can use tools like:\n"
            "- discover_qgis_algorithms\n"
            "- get_algorithm_parameters\n"
            "- resolve_algorithm_param\n"
            "- validate_algorithm_run\n"
            "- run_qgis_algorithms_by_id\n"
            "- chain_processing_algorithms\n"
            "- summarize_processing_result\n"
            "- run_qgis_code\n"
            "- capture_canvas\n"
            "\n"
            "Follow these WORKFLOW steps: Understand, Discover, Plan, Resolve, Execute, Verify, Report."
        )
        subagent_params = {
            "mode": "single",
            "task": task,
            "system": processing_prompt,
            "max_turns": params.get("max_turns", 5),
        }
        return await self._execute_subagent(subagent_params)
    async def _run_subagent_loop(self, task: str, max_turns: int, system_prompt: Optional[str] = None) -> str:
        if system_prompt is None:
            system_prompt = "You are a helpful sub-agent assistant. Solve the user's task."
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        tools = []
        for t in self.list_tools():
            func = t.get("function", {})
            name = func.get("name", "")
            if name != "subagent":
                tools.append(t)
        for turn in range(max_turns):
            response = await self._agent._client.chat(
                model=self._agent._model,
                messages=messages,
                tools=tools,
            )
            choices = response.get("choices", [])
            if not choices:
                return "Error: Empty LLM response."
            message = choices[0].get("message", {})
            messages.append(message)
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                return message.get("content", "")
            for tool_call in tool_calls:
                func_data = tool_call.get("function", {})
                name = func_data.get("name", "")
                args_str = func_data.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except Exception as e:
                    args = {}
                try:
                    result = await self.execute(name, args)
                except Exception as e:
                    result = f"Error: {e}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": result,
                })
        return "Sub-agent reached maximum turns."
    def _format_tool_error(self, tool_name: str, error: Exception) -> str:
        from aery_plugin.error_classifier import format_for_agent
        return format_for_agent(error)
    async def _execute_get_project_context(self, params: dict) -> str:
        if not self._project_context_dirty and self._project_context_cache is not None:
            return self._project_context_cache
        result = await asyncio.to_thread(self.executor.execute, "__get_project_context__", 30)
        if result.get("success"):
            serialized = json.dumps(result["result"], indent=2)
            self._project_context_cache = serialized
            self._project_context_dirty = False
            return serialized
        raise RuntimeError(result.get("error", "Failed to get project context"))
    async def _execute_get_layer_schema(self, params: dict) -> str:
        layer_name = params["layer_name"]
        code = f"__get_layer_schema__:{layer_name}"
        result = await asyncio.to_thread(self.executor.execute, code, 30)
        if result.get("success"):
            return json.dumps(result["result"], indent=2)
        raise RuntimeError(result.get("error", f"Failed to get schema for layer {layer_name}"))

    async def _execute_capture_canvas(self, params: dict) -> str:
        result = await asyncio.to_thread(self.executor.execute, "__capture_canvas__", 30)
        if result.get("success"):
            return result["result"]
        raise RuntimeError(result.get("error", "Canvas capture failed"))

    async def _execute_web_search(self, params: dict) -> str:
        """Search the web with robust error handling and anti-bot detection."""
        import urllib.request
        import urllib.parse
        import urllib.error
        import re
        import ssl

        query = params["query"]

        # Try multiple search backends
        backends = [
            self._search_duckduckgo,
            self._search_lite_duckduckgo,
        ]

        for backend in backends:
            try:
                results = await backend(query)
                if results:
                    return json.dumps(results, indent=2)
            except Exception:
                continue

        return f"No search results found for '{query}'. Try a different query or use web_fetch with a specific URL."

    async def _search_duckduckgo(self, query: str) -> Optional[list]:
        """Search DuckDuckGo HTML with improved parsing."""
        import urllib.request
        import urllib.parse
        import urllib.error
        import re

        encoded = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Check for anti-bot detection (DuckDuckGo blocks automated requests)
        if "prove you are human" in html.lower() or "captcha" in html.lower():
            return None  # Trigger fallback

        # Improved regex that handles DuckDuckGo's actual HTML structure
        results = []
        # DuckDuckGo uses result__a links with snippets in result__snippet
        link_pattern = re.compile(
            r'<a[^>]*class="[^"]*result[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<a[^>]*class="[^"]*result[^"]*"[^>]*>.*?</a>.*?'
            r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        links = link_pattern.findall(html)
        for href, title in links[:15]:
            # Skip DuckDuckGo internal links
            if "duckduckgo.com" in href and "/html/" not in href:
                continue
            # Clean title
            clean_title = re.sub(r"<[^>]+>", "", title).strip()
            if not clean_title:
                continue
            # Decode URL (DuckDuckGo uses uddg redirects)
            if "uddg=" in href:
                actual_url = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
            else:
                actual_url = href
            results.append({"url": actual_url, "title": clean_title})
            if len(results) >= 10:
                break

        return results if results else None

    async def _search_lite_duckduckgo(self, query: str) -> Optional[list]:
        """Fallback: DuckDuckGo Lite (simpler HTML, less likely to trigger anti-bot)."""
        import urllib.request
        import urllib.parse
        import re

        encoded = urllib.parse.quote(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded}"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        results = []
        # Lite version uses simpler <a> tags
        for match in re.finditer(r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html):
            href, title = match.groups()
            clean_title = re.sub(r"<[^>]+>", "", title).strip()
            if clean_title and "duckduckgo" not in href.lower():
                results.append({"url": href, "title": clean_title})
                if len(results) >= 10:
                    break

        return results if results else None

    async def _execute_web_fetch(self, params: dict) -> str:
        """Fetch and parse content from a URL with SSRF protection and robust error handling."""
        import urllib.request
        import urllib.error
        import urllib.parse
        import re
        import ipaddress
        import socket

        url = params["url"]

        # Validate URL scheme — block file://, ftp://, etc.
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme:
            url = "https://" + url
            parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"Fetch failed: unsupported scheme '{parsed.scheme}'. Only http/https allowed."

        # Block private/reserved IP ranges (SSRF protection)
        hostname = parsed.hostname or ""
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            # It's a hostname — resolve and check
            try:
                resolved = socket.getaddrinfo(hostname, None)
                for family, _, _, _, sockaddr in resolved:
                    addr = sockaddr[0]
                    ip = ipaddress.ip_address(addr)
                    if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                        return f"Fetch failed: '{hostname}' resolves to a private/reserved address ({addr}). Access denied."
            except socket.gaierror:
                return f"Fetch failed: cannot resolve '{hostname}'."
        else:
            # It was already an IP address
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                return f"Fetch failed: access to private/reserved IP '{hostname}' is denied."

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return f"Fetch failed: HTTP {e.code} {e.reason}"
        except urllib.error.URLError as e:
            return f"Fetch failed: {e.reason}"
        except Exception as e:
            return f"Fetch failed: {e}"

        # Strip HTML tags for readable text
        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return text[:10000] if text else "No readable content found."

    def _register_geospatial_tools(self):
        """Register geospatial tools from geospatial_tools.py."""
        from aery_plugin.geospatial_tools import GEOSPATIAL_TOOLS
        for tool_def in GEOSPATIAL_TOOLS:
            fn = tool_def["execute"]
            self.register({
                "name": tool_def["name"],
                "description": tool_def["description"],
                "parameters": tool_def["parameters"],
                "execute": self._make_geospatial_executor(fn),
            })

    def _make_geospatial_executor(self, fn):
        """Create an async executor that injects iface into geospatial tool calls."""
        async def executor(params: dict) -> str:
            import inspect
            sig = inspect.signature(fn)
            if "iface" in sig.parameters:
                params["iface"] = self.iface
            result = await asyncio.to_thread(fn, **params)
            if isinstance(result, dict):
                return json.dumps(result, indent=2)
            return str(result)
        return executor

    def _register_graph_tools(self):
        """Register graph query tools for provenance, tool chains, and spatial relationships."""
        self.register({
            "name": "query_provenance",
            "description": (
                "Query the provenance chain of a layer: what produced it, what it was derived from. "
                "Use when the user asks 'where did this layer come from?' or 'what created this?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "layer_name": {"type": "string", "description": "Name of the layer to trace"},
                },
                "required": ["layer_name"],
            },
            "execute": self._execute_query_provenance,
        })

        self.register({
            "name": "query_tool_chain",
            "description": (
                "Query what tools can follow a given tool in a processing pipeline. "
                "Use when the user asks 'what should I do after buffer?' or 'what comes next?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Name of the tool to query"},
                },
                "required": ["tool_name"],
            },
            "execute": self._execute_query_tool_chain,
        })

        self.register({
            "name": "query_graph",
            "description": (
                "Query the project knowledge graph for spatial relationships, layer lineage, "
                "and tool capability chains. Use when the user asks about relationships "
                "between layers or wants to understand the project structure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query about the graph"},
                },
                "required": ["query"],
            },
            "execute": self._execute_query_graph,
        })

        self.register({
            "name": "query_spatial_relationships",
            "description": (
                "Query spatial relationships between layers (overlaps, contains, within, touches). "
                "Use when the user asks 'which layers overlap?' or 'what layers are near roads?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "layer_name": {"type": "string", "description": "Layer to query relationships for (optional)"},
                },
            },
            "execute": self._execute_query_spatial,
        })

    async def _execute_query_provenance(self, params: dict) -> str:
        from aery_plugin.graph_engine import query_provenance
        project_dir = self._get_project_dir()
        if not project_dir:
            return "No project directory available."
        return query_provenance(project_dir, params["layer_name"])

    async def _execute_query_spatial(self, params: dict) -> str:
        from aery_plugin.graph_engine import query_spatial_relationships
        project_dir = self._get_project_dir()
        if not project_dir:
            return "No project directory available."
        return query_spatial_relationships(project_dir, params.get("layer_name", ""))

    async def _execute_query_tool_chain(self, params: dict) -> str:
        from aery_plugin.graph_engine import query_what_can_follow
        project_dir = self._get_project_dir()
        if not project_dir:
            return "No project directory available."
        followers = query_what_can_follow(project_dir, params["tool_name"])
        if not followers:
            return f"No tool chains found for '{params['tool_name']}'."
        return json.dumps({"tool": params["tool_name"], "can_follow": followers}, indent=2)

    async def _execute_query_graph(self, params: dict) -> str:
        from aery_plugin.graph_engine import get_context_for_prompt
        project_dir = self._get_project_dir()
        if not project_dir:
            return "No project directory available."
        return get_context_for_prompt(project_dir, params["query"])

    def _get_project_dir(self) -> str:
        """Get the current QGIS project directory."""
        try:
            from qgis.core import QgsProject
            proj = QgsProject.instance()
            if proj.fileName():
                return os.path.dirname(proj.fileName())
        except Exception:
            pass
        return ""

    def _register_self_extension_tools(self):
        """Register tools that allow AERY to register and unregister its own tools."""
        self.register({
            "name": "register_tool",
            "description": (
                "Register a new custom tool that will be available for future use. "
                "The tool code runs inside QGIS with full access to qgis.core, processing, iface, PyQt6. "
                "Store the result in the `result` variable. "
                "Use this to create specialized tools for repetitive tasks, custom analyses, "
                "or workflows you'll use multiple times."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Tool name (snake_case, e.g. 'calculate_ndvi', 'batch_clip')",
                    },
                    "description": {
                        "type": "string",
                        "description": "What the tool does and when to use it",
                    },
                    "parameters_schema": {
                        "type": "string",
                        "description": (
                            "JSON Schema for the tool's parameters as a JSON string. "
                            "Example: {\"type\":\"object\",\"properties\":{\"layer\":{\"type\":\"string\"}},\"required\":[\"layer\"]}"
                        ),
                    },
                    "code": {
                        "type": "string",
                        "description": (
                            "Python code to execute. Available globals: iface, QgsProject, QgsVectorLayer, "
                            "QgsRasterLayer, processing, QgsGeometry, QgsFeature, QgsField, QgsPointXY, "
                            "QgsRectangle, QgsCoordinateReferenceSystem, QgsDistanceArea, QgsSpatialIndex, "
                            "and all standard library modules. Store the final result in `result`."
                        ),
                    },
                },
                "required": ["name", "description", "parameters_schema", "code"],
            },
            "execute": self._execute_register_tool,
        })

        self.register({
            "name": "unregister_tool",
            "description": (
                "Remove a previously registered custom tool. "
                "Cannot remove core tools (run_qgis_code, get_project_context, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the custom tool to remove",
                    },
                },
                "required": ["name"],
            },
            "execute": self._execute_unregister_tool,
        })

        self.register({
            "name": "list_custom_tools",
            "description": (
                "List all custom tools that AERY has registered. "
                "Shows name, description, and parameters for each."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
            "execute": self._execute_list_custom_tools,
        })
    def _register_processing_discovery(self):
        """Register processing discovery tools."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS
        for tool in PROCESSING_DISCOVERY_TOOLS:
            fn = tool["execute"]
            self.register({
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
                "execute": self._make_geospatial_executor(fn),
            })

    def _register_dedicated_tools(self):
        """GeoLibre-inspired narrow tools for common view and layer operations.
        These tools resolve existing project layers by name and refuse to
        fabricate data. They are the preferred path for view and layer
        actions; `run_qgis_code` remains available as the powerful fallback.
        """
        self.register({
            "name": "zoom_to_layer",
            "description": (
                "Zoom the map canvas to the extent of an EXISTING layer "
                "in the current project. Resolves the layer by name or ID. "
                "Do NOT use this to create, load, or fetch data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "layer_name": {
                        "type": "string",
                        "description": "Name or ID of an existing layer in the project",
                    },
                },
                "required": ["layer_name"],
            },
            "execute": self._execute_zoom_to_layer,
            "examples": [
                '{"layer_name": "buildings"}',
                '{"layer_name": "roads"}',
            ],
        })
        self.register({
            "name": "set_map_extent",
            "description": (
                "Set the map canvas extent to a bounding box. "
                "Coordinates are transformed from the input CRS to the "
                "project CRS if needed. Does NOT create or modify any layer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "xmin": {"type": "number", "description": "Minimum X coordinate"},
                    "ymin": {"type": "number", "description": "Minimum Y coordinate"},
                    "xmax": {"type": "number", "description": "Maximum X coordinate"},
                    "ymax": {"type": "number", "description": "Maximum Y coordinate"},
                    "crs": {
                        "type": "string",
                        "description": "CRS of the provided coordinates (e.g. 'EPSG:4326')",
                        "default": "EPSG:4326",
                    },
                },
                "required": ["xmin", "ymin", "xmax", "ymax"],
            },
            "execute": self._execute_set_map_extent,
            "examples": [
                '{"xmin": -74.1, "ymin": 40.6, "xmax": -73.9, "ymax": 40.8, "crs": "EPSG:4326"}',
            ],
        })
        self.register({
            "name": "pan_to",
            "description": (
                "Center the map canvas on a point. Coordinates are "
                "transformed from the input CRS to the project CRS if needed. "
                "Does NOT create or modify any layer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number", "description": "X coordinate"},
                    "y": {"type": "number", "description": "Y coordinate"},
                    "crs": {
                        "type": "string",
                        "description": "CRS of the provided coordinates (e.g. 'EPSG:4326')",
                        "default": "EPSG:4326",
                    },
                },
                "required": ["x", "y"],
            },
            "execute": self._execute_pan_to,
            "examples": [
                '{"x": -74.0, "y": 40.7, "crs": "EPSG:4326"}',
            ],
        })
        self.register({
            "name": "zoom_to_place",
            "description": (
                "Zoom the map canvas to a named place (e.g. a country, city, or region) "
                "by geocoding its name. The agent should ALWAYS use this for requests like "
                "'zoom to Ghana' or 'center on Accra' instead of hand-writing PyQGIS or "
                "guessing bounding-box coordinates. Resolves the place name to a bounding "
                "box via Nominatim and zooms the canvas to it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "place": {
                        "type": "string",
                        "description": "Place name to zoom to (e.g. 'Ghana', 'Accra', 'Lake Victoria')",
                    },
                    "crs": {
                        "type": "string",
                        "description": "CRS to zoom in (defaults to the project CRS). Usually leave default.",
                        "default": "",
                    },
                },
                "required": ["place"],
            },
            "execute": self._execute_zoom_to_place,
            "examples": [
                '{"place": "Ghana"}',
                '{"place": "Accra, Ghana"}',
            ],
        })
        known = ", ".join(sorted(BASEMAP_REGISTRY.keys()))
        self.register({
            "name": "load_basemap",
            "description": (
                "Add a basemap (XYZ tile layer) to the project. Accepts a known "
                f"name ({known}) or a full HTTPS "
                "XYZ tile URL template containing {z}/{x}/{y}. Do NOT hand-write "
                "PyQGIS for basemaps — use this tool. After loading, call "
                "refresh_canvas to verify visibility."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "basemap": {
                        "type": "string",
                        "description": (
                            "Known basemap name/id (e.g. 'osm', 'esri-imagery', "
                            "'carto-dark') or an HTTPS XYZ tile URL template with "
                            "{z}/{x}/{y} placeholders."
                        ),
                        "default": "osm",
                    },
                    "name": {
                        "type": "string",
                        "description": "Layer name (defaults to the basemap label).",
                        "default": "",
                    },
                },
                "required": [],
            },
            "execute": self._execute_load_basemap,
            "examples": [
                '{"basemap": "osm"}',
                '{"basemap": "esri-imagery"}',
                '{"basemap": "https://tile.openstreetmap.org/{z}/{x}/{y}.png"}',
            ],
        })
        self.register({
            "name": "refresh_canvas",
            "description": (
                "Refresh/redraw the QGIS map canvas. Use after toggling "
                "layer visibility or making style changes. Does NOT create "
                "or modify any layer."
            ),
            "parameters": {"type": "object", "properties": {}},
            "execute": self._execute_refresh_canvas,
            "examples": ["{}"],
        })
        self.register({
            "name": "toggle_layer_visibility",
            "description": (
                "Show or hide an EXISTING layer by name or ID. "
                "Does NOT create, load, or fetch data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "layer_name": {"type": "string", "description": "Name or ID of an existing layer"},
                    "visible": {"type": "boolean", "description": "True to show, false to hide"},
                },
                "required": ["layer_name", "visible"],
            },
            "execute": self._execute_toggle_layer_visibility,
            "examples": [
                '{"layer_name": "buildings", "visible": false}',
            ],
        })
        self.register({
            "name": "set_layer_style",
            "description": (
                "Apply a simple single-symbol style to an EXISTING vector "
                "layer. For advanced styling, use run_qgis_code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "layer_name": {"type": "string", "description": "Name or ID of an existing vector layer"},
                    "color": {
                        "type": "string",
                        "description": "Hex color string, e.g. '#ff0000'",
                        "default": "#ff0000",
                    },
                },
                "required": ["layer_name"],
            },
            "execute": self._execute_set_layer_style,
            "examples": [
                '{"layer_name": "roads", "color": "#3388ff"}',
            ],
        })
        self.register({
            "name": "export_layer",
            "description": (
                "Export an EXISTING layer to a geospatial file. "
                "Output path is relative to project_dir if not absolute."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "layer_name": {"type": "string", "description": "Name or ID of an existing layer"},
                    "output_path": {"type": "string", "description": "Output file path (absolute or relative to project_dir)"},
                    "driver": {
                        "type": "string",
                        "description": "Driver name (e.g. 'GeoPackage', 'ESRI Shapefile', 'GeoJSON')",
                        "default": "GeoPackage",
                    },
                },
                "required": ["layer_name", "output_path"],
            },
            "execute": self._execute_export_layer,
            "examples": [
                '{"layer_name": "buildings", "output_path": "buildings.gpkg"}',
            ],
        })
        self.register({
            "name": "remove_layer",
            "description": (
                "Remove an EXISTING layer from the project. "
                "Always confirm with the user before removing data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "layer_name": {"type": "string", "description": "Name or ID of an existing layer"},
                },
                "required": ["layer_name"],
            },
            "execute": self._execute_remove_layer,
            "examples": [
                '{"layer_name": "temp_layer"}',
            ],
        })
        self.register({
            "name": "run_processing_algorithm",
            "description": (
                "Run a QGIS processing algorithm by id (e.g. 'native:buffer'). "
                "Parameters may reference layers by name or id. "
                "Prefer this over run_qgis_code for standard geoprocessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "algorithm": {
                        "type": "string",
                        "description": "Algorithm id in '<provider>:<algorithm>' form",
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Algorithm parameters keyed by parameter name",
                        "default": {},
                    },
                    "out_of_process": {
                        "type": "boolean",
                        "description": "Whether to run out-of-process to avoid freezing QGIS",
                        "default": False,
                    },
                },
                "required": ["algorithm"],
            },
            "execute": self._execute_run_processing_algorithm,
            "examples": [
                '{"algorithm": "native:buffer", "parameters": {"INPUT": "roads", "DISTANCE": 100.0, "OUTPUT": "TEMPORARY_OUTPUT"}, "out_of_process": true}',
            ],
        })
    async def _execute_register_tool(self, params: dict) -> str:
        """Register a new custom tool."""
        name = params["name"]
        description = params["description"]
        code = params["code"]

        try:
            schema = json.loads(params["parameters_schema"])
        except json.JSONDecodeError as e:
            return f"Invalid parameters_schema JSON: {e}"

        if not name.replace("_", "").isalnum():
            return f"Tool name must be alphanumeric with underscores only (got: '{name}')"

        if name in self._tools and name not in self._custom_tools:
            return f"Cannot override core tool: '{name}'"

        # Risk validation: reject code containing dangerous patterns
        _dangerous = re.compile(
            r'\b(os\.remove)\b|'
            r'\b(os\.unlink)\b|'
            r'\b(shutil\.rmtree)\b|'
            r'\b(subprocess\.)\b|'
            r'\b(eval\s*)\('
        )
        if _dangerous.search(code):
            return f"Refusing to register tool '{name}': code contains potentially dangerous operations"

        self._custom_tools[name] = {
            "name": name,
            "description": description,
            "parameters": schema,
            "code": code,
        }

        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": schema,
            "execute": self._make_custom_executor(name, code),
        }

        self._save_custom_tools()
        return f"Tool '{name}' registered successfully. It is now available for use."

    async def _execute_unregister_tool(self, params: dict) -> str:
        """Remove a custom tool."""
        name = params["name"]

        if name not in self._custom_tools:
            if name in self._tools:
                return f"Cannot remove core tool: '{name}'"
            return f"Tool '{name}' not found."

        del self._custom_tools[name]
        if name in self._tools:
            del self._tools[name]

        self._save_custom_tools()
        return f"Tool '{name}' removed successfully."

    async def _execute_list_custom_tools(self, params: dict) -> str:
        """List all registered custom tools."""
        if not self._custom_tools:
            return "No custom tools registered."

        tools_list = []
        for name, tool in self._custom_tools.items():
            tools_list.append({
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"],
            })

        return json.dumps({"custom_tools": tools_list, "count": len(tools_list)}, indent=2)

    def _make_custom_executor(self, name: str, code: str):
        """Create an async executor for a custom tool that routes through the QGIS executor."""
        async def executor(params: dict) -> str:
            param_code = "\n".join(
                f"{k} = {json.dumps(v)}" for k, v in params.items()
            )
            full_code = f"{param_code}\n\n{code}"
            result = await asyncio.to_thread(self.executor.execute, full_code, 300)
            if result.get("success"):
                r = result.get("result")
                return json.dumps(r, indent=2) if isinstance(r, dict) else str(r)
            raise RuntimeError(result.get("error", "Execution failed"))
        return executor

    def _load_custom_tools(self):
        """Load custom tools from the project directory."""
        project_dir = self._get_project_dir()
        if not project_dir:
            return

        tools_path = os.path.join(project_dir, ".aery", "custom_tools.json")
        if not os.path.exists(tools_path):
            return

        try:
            with open(tools_path) as f:
                custom_tools = json.load(f)
        except (json.JSONDecodeError, IOError):
            return

        for tool_data in custom_tools:
            name = tool_data["name"]
            self._custom_tools[name] = tool_data
            self._tools[name] = {
                "name": name,
                "description": tool_data["description"],
                "parameters": tool_data["parameters"],
                "execute": self._make_custom_executor(name, tool_data["code"]),
            }

    def _save_custom_tools(self):
        """Persist custom tools to the project directory."""
        project_dir = self._get_project_dir()
        if not project_dir:
            return

        aery_dir = os.path.join(project_dir, ".aery")
        os.makedirs(aery_dir, exist_ok=True)

        tools_path = os.path.join(aery_dir, "custom_tools.json")
        tools_list = list(self._custom_tools.values())

        try:
            with open(tools_path, "w") as f:
                json.dump(tools_list, f, indent=2)
        except IOError as e:
            pass

    async def _execute_zoom_to_layer(self, params: dict) -> str:
        layer_name = params["layer_name"]
        code = f"""
def resolve_layer(name):
    from qgis.core import QgsProject
    for l in QgsProject.instance().mapLayers().values():
        if l.name() == name or l.id() == name:
            return l
    raise ValueError(f"Layer '{{name}}' not found.")

layer = resolve_layer({repr(layer_name)})
canvas = iface.mapCanvas()
canvas.setExtent(layer.extent())
canvas.refresh()
result = "Zoomed to layer"
"""
        return await self._execute_qgis_code({"code": code})

    async def _execute_set_map_extent(self, params: dict) -> str:
        xmin = params["xmin"]
        ymin = params["ymin"]
        xmax = params["xmax"]
        ymax = params["ymax"]
        crs = params.get("crs", "EPSG:4326")
        code = f"""
from qgis.core import QgsRectangle, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
rect = QgsRectangle({xmin}, {ymin}, {xmax}, {ymax})
crs_str = {repr(crs)}
src_crs = QgsCoordinateReferenceSystem(crs_str)
dest_crs = QgsProject.instance().crs()
if src_crs.isValid() and src_crs != dest_crs:
    transform = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())
    rect = transform.transformBoundingBox(rect)
canvas = iface.mapCanvas()
canvas.setExtent(rect)
canvas.refresh()
result = "Extent set"
"""
        return await self._execute_qgis_code({"code": code})

    async def _execute_pan_to(self, params: dict) -> str:
        x = params["x"]
        y = params["y"]
        crs = params.get("crs", "EPSG:4326")
        code = f"""
from qgis.core import QgsPointXY, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
pt = QgsPointXY({x}, {y})
crs_str = {repr(crs)}
src_crs = QgsCoordinateReferenceSystem(crs_str)
dest_crs = QgsProject.instance().crs()
if src_crs.isValid() and src_crs != dest_crs:
    transform = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())
    pt = transform.transform(pt)
canvas = iface.mapCanvas()
canvas.setCenter(pt)
canvas.refresh()
result = "Canvas centered"
"""
        return await self._execute_qgis_code({"code": code})

    async def _execute_zoom_to_place(self, params: dict) -> str:
        place = params["place"].strip()
        target_crs = params.get("crs", "") or ""
        code = f"""
import json, urllib.request, urllib.parse
from qgis.core import (
    QgsRectangle, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsProject, QgsGeometry, QgsPointXY,
)

place = {repr(place)}
target_crs_str = {repr(target_crs)}

url = ("https://nominatim.openstreetmap.org/search?format=json"
       "&limit=1&q=" + urllib.parse.quote(place))
req = urllib.request.Request(url, headers={{"User-Agent": "AeryQGISPlugin/1.0"}})
with urllib.request.urlopen(req, timeout=20) as resp:
    data = json.loads(resp.read().decode("utf-8"))
if not data:
    raise RuntimeError(f"Geocoding returned no results for place: '{{place}}'")
bb = data[0]["boundingbox"]
# boundingbox is [south, north, east, west] from Nominatim
south, north, east, west = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
# Build in EPSG:4326 then transform to the project CRS
src_crs = QgsCoordinateReferenceSystem("EPSG:4326")
dest_crs = QgsProject.instance().crs()
rect = QgsRectangle(west, south, east, north)
if src_crs.isValid() and dest_crs.isValid() and src_crs != dest_crs:
    xform = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())
    rect = xform.transformBoundingBox(rect)
canvas = iface.mapCanvas()
canvas.setExtent(rect)
canvas.refresh()
result = f"Zoomed to '{{place}}' ({{data[0].get('display_name', place)}})"
"""
        return await self._execute_qgis_code({"code": code})
    async def _execute_refresh_canvas(self, params: dict) -> str:
        code = """
iface.mapCanvas().refresh()
result = "Canvas refreshed"
"""
        return await self._execute_qgis_code({"code": code})

    async def _execute_load_basemap(self, params: dict) -> str:
        reference = params.get("basemap", "osm") or "osm"
        entry = resolve_basemap(reference)
        if entry is None:
            raise ValueError(
                f"Unknown basemap '{reference}'. Known: {', '.join(BASEMAP_REGISTRY.keys())} "
                "or pass an HTTPS XYZ tile URL with {z}/{x}/{y}."
            )
        url = entry["url"]
        name = (params.get("name") or "").strip() or entry["label"]
        # Build the tile source. QGIS 4.x removed the dedicated 'xyz' raster
        # provider, but the WMS provider still accepts a `type=xyz` URI. The
        # KEY detail the earlier attempt missed: the URI MUST include
        # zmin/zmax/crs so QGIS can build the tile-matrix set and actually
        # stream tiles. Without them the layer is "valid" but fetches 0 tiles
        # (the earlier "static image" bug). With them, this is QGIS's native,
        # fully zoomable XYZ basemap path (QgsSingleBandColorDataRenderer).
        import re as _re, tempfile as _tf, os as _os, time as _time
        _gdal_url = url.replace("{z}", "${z}").replace("{x}", "${x}").replace("{y}", "${y}")
        # GDAL WMS TMS XML is the ONLY path that streams real per-zoom tiles on
        # QGIS 4.2+. The native 'xyz' provider was removed and the WMS
        # 'type=xyz' URI is "valid" but fetches 0 tiles (verified dead on this
        # exact QGIS build). GDAL's WMS driver is independent of QGIS's removed
        # xyz provider and streams correctly during canvas renders.
        # A persistent (not per-load-unique) cache dir is required: a unique
        # dir broke tiling in the live GUI. Use one stable dir per basemap name.
        _safe_name = _re.sub("[^a-z0-9]", "_", name.lower())
        _xml_cache_dir = _os.path.join(_tf.gettempdir(), f"aery_gdal_cache_{_safe_name}")
        _xml = (
            "<GDAL_WMS>\n"
            '  <Service name="TMS">\n'
            f"    <ServerUrl>{_gdal_url}</ServerUrl>\n"
            "  </Service>\n"
            "  <DataWindow>\n"
            "    <UpperLeftX>-20037508.34</UpperLeftX>\n"
            "    <UpperLeftY>20037508.34</UpperLeftY>\n"
            "    <LowerRightX>20037508.34</LowerRightX>\n"
            "    <LowerRightY>-20037508.34</LowerRightY>\n"
            "    <TileLevel>19</TileLevel>\n"
            "    <TileCountX>1</TileCountX>\n"
            "    <TileCountY>1</TileCountY>\n"
            "    <YOrigin>top</YOrigin>\n"
            "  </DataWindow>\n"
            "  <Projection>EPSG:3857</Projection>\n"
            "  <BlockSizeX>256</BlockSizeX>\n"
            "  <BlockSizeY>256</BlockSizeY>\n"
            "  <BandsCount>3</BandsCount>\n"
            '  <DataType>byte</DataType>\n'
            "  <OverviewCount>-1</OverviewCount>\n"
            "  <MaxConnections>4</MaxConnections>\n"
            '  <UserAgent>Aery QGIS Plugin (contact: aery-user; purpose: basemap preview)</UserAgent>\n'
            "  <ZeroBlockHttpCodes>204,404</ZeroBlockHttpCodes>\n"
            "  <Cache><Path>" + _xml_cache_dir + "</Path></Cache>\n"
            "</GDAL_WMS>"
        )
        _xmlpath = _os.path.join(_tf.gettempdir(), f"aery_basemap_{_safe_name}.xml")
        code = f"""
from qgis.core import QgsRasterLayer, QgsProject, QgsCoordinateReferenceSystem, QgsRectangle, QgsCoordinateTransform, QgsLayerTreeLayer
from PyQt6.QtWidgets import QApplication
import time
# 1. Clean up existing basemap layers with the same name or XYZ source to prevent duplication
_existing = [l for l in QgsProject.instance().mapLayers().values() 
             if l.name() == {repr(name)} or "type=xyz" in l.publicSource()]
for _old in _existing:
    QgsProject.instance().removeMapLayer(_old)

# Choose the correct basemap loading path for the running QGIS version.
# Loading path for the running QGIS version.
# QGIS <= 4.1 ships a native 'xyz' raster provider that loads XYZ tiles directly.
# QGIS >= 4.2 REMOVED the 'xyz' provider and its WMS 'type=xyz' URI is "valid"
# but fetches 0 tiles, so we MUST use the GDAL WMS TMS XML path, which is the
# only method that actually streams per-zoom tiles on 4.2+.
_xml = {repr(_xml)}
_xmlpath = {repr(_xmlpath)}
_url = {repr(url)}
from qgis.core import Qgis as _Qgis
_qver = _Qgis.versionInt()
def _build_xyz_uri(u):
    return "type=xyz&url=" + u + "&zmin=0&zmax=19&crs=EPSG:3857"
layer = None
if _qver < 40200:
    # Native XYZ provider (QGIS <= 4.1): the correct, simplest path.
    layer = QgsRasterLayer(_build_xyz_uri(_url), {repr(name)}, 'xyz')
if layer is None or not layer.isValid():
    # QGIS >= 4.2 (no xyz provider) OR xyz path failed: GDAL WMS TMS XML.
    # Streams real tiles per zoom; exposes 3 RGB bands -> QgsMultiBandColorRenderer.
    with open(_xmlpath, "w") as _xf:
        _xf.write(_xml)
    layer = QgsRasterLayer(_xmlpath, {repr(name)}, 'gdal')
if not layer.isValid():
    raise RuntimeError(f"Basemap layer failed to load: {{layer.error().summary()}}")
# Force an RGB color renderer for the GDAL/WMS-XML path (3 real bands). For the
# native xyz path (single-band color-data renderer) we leave it alone.
if layer.providerType() == 'gdal' and layer.bandCount() >= 3:
    from qgis.core import QgsMultiBandColorRenderer
    layer.setRenderer(QgsMultiBandColorRenderer(layer.dataProvider(), 1, 2, 3))
    layer.triggerRepaint()
QgsProject.instance().addMapLayer(layer, False)

# 3. Add manually to the bottom (end of children list) of the root layer tree
_root = QgsProject.instance().layerTreeRoot()
_node = QgsLayerTreeLayer(layer)
_root.addChildNode(_node)
_node.setItemVisibilityChecked(True)

# 4. Canvas handling -- make the canvas actually render the new layer.
canvas = iface.mapCanvas()
# Register the layer in the canvas's active layer set (legend->canvas sync can
# lag), then force a full redraw so the basemap paints immediately.
_cur = list(canvas.layers())
if layer not in _cur:
    _cur.append(layer)
canvas.setLayers(_cur)
canvas.refresh()
layer.triggerRepaint()

# Wait briefly for async tile download so the first paint isn't blank, then
# refresh again. Keep this short -- GDAL fetches tiles in the GUI thread, so a
# long pump here is what made basemap loads take minutes.
for _ in range(10):
    QApplication.processEvents()
    time.sleep(0.1)
canvas.refresh()

# 5. Extent handling: do NOT change the user's view. Per project rules, loading
#    a basemap must only add the layer and leave the canvas exactly as it was.
#    We never zoom, pan, or adjust the extent on basemap load.
_zoomed = False
_extent_after_set = str(canvas.extent())

# 6. Network probe removed: it was only diagnostic and its exception binding
#    (_e) could raise NameError inside the sandbox executor. The basemap load
#    does not depend on it. Tile fetching is verified by the canvas render.
_net_error = "not checked"
_net_status = "not checked"
_net_size = "not checked"

# Diagnostics to log file for debugging
_extent = canvas.extent()
_diag = {{
    "provider": layer.providerType(),
    "crs": layer.crs().authid(),
    "extent": str(layer.extent()),
    "canvas_crs": canvas.mapSettings().destinationCrs().authid(),
    "extent_before_set": str(_extent),
    "zoomed_branch_fired": _zoomed,
    "extent_after_set": _extent_after_set,
    "canvas_layers": [l.name() for l in canvas.layers()],
    "node_visible": _node.itemVisibilityChecked(),
    "opacity": layer.renderer().opacity() if layer.renderer() else "N/A",
    "render_flag": canvas.renderFlag() if hasattr(canvas, "renderFlag") else "N/A",
    "layer_isValid": layer.isValid(),
    "network_error": _net_error,
    "network_status": _net_status,
    "network_size": _net_size,
    "metadata": layer.htmlMetadata() if hasattr(layer, "htmlMetadata") else "N/A",
}}
with open("/tmp/aery_basemap_diag.json", "w") as f:
    import json; json.dump(_diag, f, indent=2)

result = f"Added basemap '{{layer.name()}}' (provider={{layer.providerType()}}, crs={{layer.crs().authid()}})"
"""
        return await self._execute_qgis_code({"code": code})

    async def _execute_toggle_layer_visibility(self, params: dict) -> str:
        layer_name = params["layer_name"]
        visible = params["visible"]
        code = f"""
def resolve_layer(name):
    from qgis.core import QgsProject
    for l in QgsProject.instance().mapLayers().values():
        if l.name() == name or l.id() == name:
            return l
    raise ValueError(f"Layer '{{name}}' not found.")

layer = resolve_layer({repr(layer_name)})
from qgis.core import QgsProject
node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
if node:
    node.setItemVisibilityChecked({repr(visible)})
iface.mapCanvas().refresh()
result = "Visibility toggled"
"""
        return await self._execute_qgis_code({"code": code})

    async def _execute_set_layer_style(self, params: dict) -> str:
        layer_name = params["layer_name"]
        color = params.get("color", "#ff0000")
        code = f"""
def resolve_layer(name):
    from qgis.core import QgsProject
    for l in QgsProject.instance().mapLayers().values():
        if l.name() == name or l.id() == name:
            return l
    raise ValueError(f"Layer '{{name}}' not found.")

layer = resolve_layer({repr(layer_name)})
if layer.type() != 0:
    raise ValueError("Layer is not a vector layer")

from qgis.core import QgsSingleSymbolRenderer, QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol
from PyQt6.QtGui import QColor
geom_type = layer.geometryType()
color = QColor({repr(color)})
if geom_type == 0:
    symbol = QgsMarkerSymbol.createSimple({{"color": {repr(color)}}})
elif geom_type == 1:
    symbol = QgsLineSymbol.createSimple({{"color": {repr(color)}}})
else:
    symbol = QgsFillSymbol.createSimple({{"color": {repr(color)}}})

renderer = QgsSingleSymbolRenderer(symbol)
layer.setRenderer(renderer)
layer.triggerRepaint()
iface.mapCanvas().refresh()
result = "Style applied"
"""
        return await self._execute_qgis_code({"code": code})

    async def _execute_export_layer(self, params: dict) -> str:
        layer_name = params["layer_name"]
        output_path = params["output_path"]
        driver = params.get("driver", "GeoPackage")
        code = f"""
def resolve_layer(name):
    from qgis.core import QgsProject
    for l in QgsProject.instance().mapLayers().values():
        if l.name() == name or l.id() == name:
            return l
    raise ValueError(f"Layer '{{name}}' not found.")

layer = resolve_layer({repr(layer_name)})
import os
out_path = {repr(output_path)}
if not os.path.isabs(out_path) and project_dir:
    out_path = os.path.abspath(os.path.join(project_dir, out_path))

import processing
params = {{
    'INPUT': layer,
    'OUTPUT': out_path,
    'LAYER_NAME': layer.name(),
    'DATASOURCE_OPTIONS': f'driver={driver}'
}}
res = processing.run("native:savefeatures", params)
result = f"Exported to {{out_path}}"
"""
        return await self._execute_qgis_code({"code": code})

    async def _execute_remove_layer(self, params: dict) -> str:
        layer_name = params["layer_name"]
        code = f"""
def resolve_layer(name):
    from qgis.core import QgsProject
    for l in QgsProject.instance().mapLayers().values():
        if l.name() == name or l.id() == name:
            return l
    raise ValueError(f"Layer '{{name}}' not found.")

layer = resolve_layer({repr(layer_name)})
from qgis.core import QgsProject
QgsProject.instance().removeMapLayer(layer.id())
iface.mapCanvas().refresh()
result = "Layer removed"
"""
        return await self._execute_qgis_code({"code": code})

    async def _execute_run_processing_algorithm(self, params: dict) -> str:
        algorithm = params["algorithm"]
        alg_params = params.get("parameters", {})
        out_of_process = params.get("out_of_process", False)
        qgis_proc = None
        if out_of_process:
            # Try to find qgis_process
            import shutil
            qgis_proc = shutil.which("qgis_process")
            if not qgis_proc:
                import platform
                system = platform.system()
                if system == "Darwin":
                    p = "/Applications/QGIS.app/Contents/MacOS/bin/qgis_process"
                    if os.path.exists(p):
                        qgis_proc = p
                elif system == "Windows":
                    paths = [
                        r"C:\OSGeo4W\bin\qgis_process.bat",
                        r"C:\OSGeo4W64\bin\qgis_process.bat",
                        r"C:\Program Files\QGIS\bin\qgis_process.bat"
                    ]
                    for p in paths:
                        if os.path.exists(p):
                            qgis_proc = p
                            break
        if out_of_process and qgis_proc:
            # Query the main thread to resolve layer names/IDs to their file paths
            # and check if any is memory layer.
            resolve_code = f"""
from qgis.core import QgsProject
params = {repr(alg_params)}
resolved = {{}}
is_memory = False
for k, v in params.items():
    if isinstance(v, str):
        found = False
        for l in QgsProject.instance().mapLayers().values():
            if l.name() == v or l.id() == v:
                if l.providerType() == 'memory':
                    is_memory = True
                resolved[k] = l.source()
                found = True
                break
        if not found:
            resolved[k] = v
    else:
        resolved[k] = v
result = {{"resolved": resolved, "is_memory": is_memory}}
"""
            resolve_res = await self._execute_qgis_code({"code": resolve_code})
            try:
                resolve_data = json.loads(resolve_res)
                is_memory = resolve_data.get("is_memory", False)
                resolved_params = resolve_data.get("resolved", {})
            except Exception:
                is_memory = True
                resolved_params = alg_params
            if not is_memory:
                # We can run out-of-process!
                cmd = [qgis_proc, "run", algorithm]
                for k, v in resolved_params.items():
                    if v is not None:
                        cmd.append(f"--{k}={v}")
                # Execute subprocess asynchronously
                try:
                    import subprocess
                    process = await asyncio.to_thread(
                        subprocess.Popen,
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    stdout, stderr = await asyncio.to_thread(process.communicate)
                    if process.returncode == 0:
                        return f"Out-of-process algorithm execution complete:\n{stdout}"
                    else:
                        raise RuntimeError(f"qgis_process failed with code {process.returncode}:\n{stderr}\n{stdout}")
                except Exception as e:
                    logger.warning(f"Out-of-process execution failed: {e}. Falling back to in-process.")
            else:
                logger.info("Memory layer detected. Falling back to in-process execution.")
        # Fallback to standard in-process run
        code = f"""
import processing
from qgis.core import QgsProject
def layer_from_ref(name):
    for l in QgsProject.instance().mapLayers().values():
        if l.name() == name or l.id() == name:
            return l
    return name
alg_params = {repr(alg_params)}
resolved = {{}}
for k, v in alg_params.items():
    if isinstance(v, str):
        resolved[k] = layer_from_ref(v)
    else:
        resolved[k] = v
res = processing.run({repr(algorithm)}, resolved)
out = {{}}
for k, v in res.items():
    if hasattr(v, 'id'):
        out[k] = v.id()
    else:
        out[k] = str(v)
result = f"Algorithm execution complete: {{out}}"
"""
        return await self._execute_qgis_code({"code": code})
