"""Tool definitions for the Aery QGIS agent.

Each tool is a dict with name, description, parameters (JSON Schema),
and an execute function that takes params and returns a result.
"""

import asyncio
import json
import os
import re
from typing import Any, Callable, Optional


DESTRUCTIVE_TOOLS = {
    "run_qgis_code": ["removeMapLayer", "deleteFeatures", "os.remove", "shutil.rmtree"],
}

PERMISSION_MODES = {"default", "acceptEdits", "bypassPermissions", "plan", "dontAsk"}


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

    def __init__(self, executor, iface=None):
        self.executor = executor
        self.iface = iface
        self._tools: dict[str, dict] = {}
        self._custom_tools: dict[str, dict] = {}
        self.hooks = HookRegistry()
        self._permission_mode = "default"
        self._register_core_tools()
        self._register_default_hooks()
        self._load_custom_tools()

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
            "description": "Get a full snapshot of the current QGIS project: layers, CRS, feature counts, fields.",
            "parameters": {"type": "object", "properties": {}},
            "execute": self._execute_get_project_context,
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

        # Register geospatial tools (export_webmap, publish_geoserver, set_layer_style,
        # multi_map_layout, save_map_theme, load_map_theme, list_map_themes, refresh_canvas)
        self._register_geospatial_tools()

        # Register graph query tools
        self._register_graph_tools()

        # Register self-extension tools (AERY can register its own tools)
        self._register_self_extension_tools()

    def register(self, tool: dict):
        """Register a tool definition."""
        self._tools[tool["name"]] = tool

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

        # Check property types
        properties = schema.get("properties", {})
        for key, value in params.items():
            prop_schema = properties.get(key, {})
            expected_type = prop_schema.get("type")
            if expected_type and value is not None:
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
                    errors.append(
                        f"Parameter '{key}' expected type '{expected_type}', got '{type(value).__name__}'"
                    )

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

    async def execute(self, name: str, params: dict) -> Any:
        """Execute a tool by name with the given parameters."""
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        return await tool["execute"](params)

    async def _execute_qgis_code(self, params: dict) -> str:
        code = params["code"]
        # Run sync executor in thread pool to avoid blocking the event loop
        result = await asyncio.to_thread(self.executor.execute, code, 300)
        if result.get("success"):
            r = result.get("result")
            return json.dumps(r, indent=2) if isinstance(r, dict) else str(r)
        raise RuntimeError(result.get("error", "Execution failed"))

    async def _execute_get_project_context(self, params: dict) -> str:
        result = await asyncio.to_thread(self.executor.execute, "__get_project_context__", 30)
        if result.get("success"):
            return json.dumps(result["result"], indent=2)
        raise RuntimeError(result.get("error", "Failed to get project context"))

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
