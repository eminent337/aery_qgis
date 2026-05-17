"""Tool definitions for the Aery QGIS agent.

Each tool is a dict with name, description, parameters (JSON Schema),
and an execute function that takes params and returns a result.
"""

import asyncio
import json
import os
from typing import Any, Callable, Optional


class ToolRegistry:
    """Registry of tools available to the agent."""

    def __init__(self, executor, iface=None):
        self.executor = executor
        self.iface = iface
        self._tools: dict[str, dict] = {}
        self._register_core_tools()

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
        import urllib.request
        import urllib.parse
        query = urllib.parse.quote(params["query"])
        url = f"https://html.duckduckgo.com/html/?q={query}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode()
            import re
            snippets = re.findall(r'<a[^>]*class="result[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html)
            if snippets:
                return json.dumps([{"url": s[0], "title": re.sub(r"<[^>]+>", "", s[1])} for s in snippets[:10]], indent=2)
            return "No results found."
        except Exception as e:
            return f"Search failed: {e}"

    async def _execute_web_fetch(self, params: dict) -> str:
        import urllib.request
        url = params["url"]
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode()[:10000]
        except Exception as e:
            return f"Fetch failed: {e}"
