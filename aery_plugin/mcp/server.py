#!/usr/bin/env python3
"""MCP Server for Aery QGIS Plugin.
Exposes typed tools via Model Context Protocol (stdio + SSE transports).
All tool execution is dispatched to QGIS main thread via QTimer.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import threading
import uuid
from typing import Any, Optional
from unittest.mock import MagicMock
try:
    from mcp.server import Server, ServerRequestContext
    from mcp.server.stdio import stdio_server
    from mcp.server.sse import SseServerTransport
    from mcp.types import (
        CallToolResult,
        ListToolsResult,
        ListResourcesResult,
        ReadResourceResult,
        TextResourceContents,
        BlobResourceContents,
        Resource,
        Tool as MCPTool,
        TextContent,
        CallToolRequestParams,
        PaginatedRequestParams,
    )
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    class _MockCallToolResult:
        def __init__(self, content, isError=False):
            self.content = content
            self.isError = isError
    class _MockListToolsResult:
        def __init__(self, tools):
            self.tools = tools
    class _MockListResourcesResult:
        def __init__(self, resources):
            self.resources = resources
    class _MockReadResourceResult:
        def __init__(self, contents):
            self.contents = contents
    class _MockMCPTool:
        def __init__(self, name, description, inputSchema):
            self.name = name
            self.description = description
            self.inputSchema = inputSchema
    class _MockTextContent:
        def __init__(self, type, text):
            self.type = type
            self.text = text
    class _MockResource:
        def __init__(self, uri, name, description, mimeType):
            self.uri = uri
            self.name = name
            self.description = description
            self.mimeType = mimeType
    class _MockTextResourceContents:
        def __init__(self, uri, mimeType, text):
            self.uri = uri
            self.mimeType = mimeType
            self.text = text
    class _MockBlobResourceContents:
        def __init__(self, uri, mimeType, blob):
            self.uri = uri
            self.mimeType = mimeType
            self.blob = blob
    class _MockServer:
        def __init__(self, name: str = "", version: str = "", on_list_tools=None, on_call_tool=None, on_list_resources=None, on_read_resource=None, **kwargs):
            self.name = name
            self.version = version
            self.on_list_tools = on_list_tools
            self.on_call_tool = on_call_tool
            self.on_list_resources = on_list_resources
            self.on_read_resource = on_read_resource
        async def list_tools(self, context, params=None):
            if self.on_list_tools:
                return await self.on_list_tools(context, params)
            return _MockListToolsResult(tools=[])
        async def call_tool(self, context, params):
            if self.on_call_tool:
                return await self.on_call_tool(context, params)
            return _MockCallToolResult(
                content=[_MockTextContent(type="text", text="Not implemented")],
                isError=True
            )
        async def list_resources(self, context, params=None):
            if self.on_list_resources:
                return await self.on_list_resources(context, params)
            return _MockListResourcesResult(resources=[])
        async def read_resource(self, context, params):
            if self.on_read_resource:
                return await self.on_read_resource(context, params)
            return _MockReadResourceResult(contents=[])
    class _MockContext:
        pass
    Server = _MockServer
    ServerRequestContext = _MockContext
    stdio_server = None
    SseServerTransport = None
    CallToolResult = _MockCallToolResult
    ListToolsResult = _MockListToolsResult
    ListResourcesResult = _MockListResourcesResult
    ReadResourceResult = _MockReadResourceResult
    TextResourceContents = _MockTextResourceContents
    BlobResourceContents = _MockBlobResourceContents
    Resource = _MockResource
    MCPTool = _MockMCPTool
    TextContent = _MockTextContent
    CallToolRequestParams = _MockServer
    PaginatedRequestParams = _MockServer
class QGISThreadDispatcher:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._results: dict[str, asyncio.Future] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._timer = None

    def start(self):
        """Start the dispatcher (must be called from QGIS main thread)."""
        if self._running:
            return
        self._running = True
        # Import here to avoid issues when MCP runs standalone
        try:
            from PyQt6.QtCore import QTimer
            self._timer = QTimer()
            self._timer.timeout.connect(self._process_queue)
            self._timer.start(10)  # 10ms polling
        except Exception:
            # Running outside QGIS (e.g., tests)
            self._timer = None

    def _process_queue(self):
        """Process queued tool calls (runs on QGIS main thread)."""
        try:
            from aery_plugin.tools_new import TypedToolBridge, create_tools
            from aery_plugin.qgis_executor import QGISCodeExecutor

            executor = QGISCodeExecutor.instance()
            if not executor:
                return

            bridge = TypedToolBridge(executor=executor)

            while not self._queue.empty():
                task_id, name, params, future = self._queue.get_nowait()
                try:
                    # Check if tool exists
                    tool = next((t for t in create_tools() if t.name == name), None)
                    if not tool:
                        future.set_result({"error": f"Unknown tool: {name}"})
                        continue

                    # Execute on main thread
                    import asyncio
                    loop = asyncio.new_event_loop()
                    result = loop.run_until_complete(bridge.execute(name, params))
                    loop.close()
                    future.set_result(result)
                except Exception as e:
                    future.set_result({"error": str(e)})
        except Exception:
            pass  # Silently ignore if QGIS not available

    def dispatch(self, name: str, params: dict) -> asyncio.Future:
        """Queue a tool call for main thread execution."""
        future = asyncio.Future()
        self._queue.put_nowait((str(uuid.uuid4()), name, params, future))
        return future


_dispatcher = QGISThreadDispatcher()


def get_dispatcher() -> QGISThreadDispatcher:
    return _dispatcher


def _create_server() -> Server:
    """Create MCP server with tool handlers."""

    # Get auth token from environment or shared config
    auth_token = os.environ.get("AERY_MCP_AUTH_TOKEN")
    if not auth_token:
        # Try to load from qgis_executor's shared auth_token
        try:
            from aery_plugin.qgis_executor import QGISCodeExecutor
            executor = QGISCodeExecutor.instance()
            if executor and hasattr(executor, "auth_token"):
                auth_token = executor.auth_token
        except Exception:
            pass

    def _validate_auth(context: ServerRequestContext) -> bool:
        """Validate auth token from request headers/meta."""
        if not auth_token:
            return True  # No auth configured, allow all
        # Check request meta for auth token
        meta = getattr(context, "request", None)
        if meta and hasattr(meta, "meta"):
            provided = meta.meta.get("authorization") or meta.meta.get("x-auth-token")
            if provided:
                # Remove "Bearer " prefix if present
                provided = provided.replace("Bearer ", "")
                return provided == auth_token
        return False

    async def list_tools(
        context: ServerRequestContext[Any],
        params: Optional[PaginatedRequestParams] = None,
    ) -> ListToolsResult:
        if not _validate_auth(context):
            from mcp.types import ErrorData
            raise Exception("Unauthorized")
        from aery_plugin.tools_new import create_tools
        tools = create_tools()
        return ListToolsResult(
            tools=[
                MCPTool(
                    name=t.name,
                    description=t.description,
                    inputSchema=t.input_schema,
                )
                for t in tools
            ]
        )
    async def call_tool(
        context: ServerRequestContext[Any],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        if not _validate_auth(context):
            from mcp.types import ErrorData
            raise Exception("Unauthorized")
        from aery_plugin.tools_new import create_tools
        name = params.name
        arguments = params.arguments or {}
        # Verify tool exists
        tool = next((t for t in create_tools() if t.name == name), None)
        if not tool:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )
        # Dispatch to QGIS main thread
        try:
            future = _dispatcher.dispatch(name, arguments)
            # Wait with timeout
            result = await asyncio.wait_for(future, timeout=120.0)
            if isinstance(result, dict) and "error" in result:
                return CallToolResult(
                    content=[TextContent(type="text", text=result["error"])],
                    isError=True,
                )
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(result, indent=2))],
            )
        except asyncio.TimeoutError:
            return CallToolResult(
                content=[TextContent(type="text", text="Tool execution timed out")],
                isError=True,
            )
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Tool execution failed: {e}")],
                isError=True,
            )
    # Resource handlers for project, layers, canvas
    async def list_resources(
        context: ServerRequestContext[Any],
        params: Optional[PaginatedRequestParams] = None,
    ) -> Any:
        if not _validate_auth(context):
            from mcp.types import ErrorData
            raise Exception("Unauthorized")
        from mcp.types import Resource
        resources = [
            Resource(
                uri="aery://project",
                name="Current Project",
                description="Current QGIS project metadata (title, CRS, layer count)",
                mimeType="application/json",
            ),
            Resource(
                uri="aery://layers",
                name="Project Layers",
                description="List of all layers in the project with basic info",
                mimeType="application/json",
            ),
            Resource(
                uri="aery://canvas",
                name="Map Canvas",
                description="Current map canvas as PNG image",
                mimeType="image/png",
            ),
        ]
        return ListResourcesResult(resources=resources)
    async def read_resource(
        context: ServerRequestContext[Any],
        params: Any,
    ) -> Any:
        if not _validate_auth(context):
            from mcp.types import ErrorData
            raise Exception("Unauthorized")
        uri = params.uri if hasattr(params, "uri") else str(params)
        if uri == "aery://project":
            # Get project info via typed tool
            from aery_plugin.tools_new import TypedToolBridge
            from aery_plugin.qgis_executor import QGISCodeExecutor
            executor = QGISCodeExecutor.instance()
            if executor:
                bridge = TypedToolBridge(executor=executor)
                result = await bridge.execute("list_layers", {})
                return ReadResourceResult(
                    contents=[TextResourceContents(uri=uri, mimeType="application/json", text=result)]
                )
            return ReadResourceResult(
                contents=[TextResourceContents(uri=uri, mimeType="application/json", text='{"error": "No QGIS executor"}')]
            )
        elif uri == "aery://layers":
            from aery_plugin.tools_new import TypedToolBridge
            from aery_plugin.qgis_executor import QGISCodeExecutor
            executor = QGISCodeExecutor.instance()
            if executor:
                bridge = TypedToolBridge(executor=executor)
                result = await bridge.execute("list_layers", {})
                return ReadResourceResult(
                    contents=[TextResourceContents(uri=uri, mimeType="application/json", text=result)]
                )
            return ReadResourceResult(
                contents=[TextResourceContents(uri=uri, mimeType="application/json", text='{"error": "No QGIS executor"}')]
            )
        elif uri == "aery://canvas":
            from aery_plugin.tools_new import TypedToolBridge
            from aery_plugin.qgis_executor import QGISCodeExecutor
            executor = QGISCodeExecutor.instance()
            if executor:
                bridge = TypedToolBridge(executor=executor)
                result = await bridge.execute("capture_canvas", {"max_dim": 1200})
                # Result is JSON with image data URL
                import json as _json
                data = _json.loads(result)
                if "image" in data:
                    # Return as binary resource
                    return ReadResourceResult(
                        contents=[BlobResourceContents(uri=uri, mimeType="image/png", blob=data["image"].split(",")[1])]
                    )
            return ReadResourceResult(
                contents=[TextResourceContents(uri=uri, mimeType="text/plain", text="Canvas capture failed")]
            )
        return ReadResourceResult(
            contents=[TextResourceContents(uri=uri, mimeType="text/plain", text=f"Unknown resource: {uri}")]
        )
    server = Server(
        name="aery-qgis",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_read_resource=read_resource,
    )
    return server


async def run_stdio_server():
    """Run MCP server over stdio transport."""
    if not HAS_MCP:
        print("MCP not installed. Run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    server = _create_server()

    # Start dispatcher on QGIS main thread
    _dispatcher.start()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


async def run_sse_server(host: str = "127.0.0.1", port: int = 9876):
    """Run MCP server over SSE transport (HTTP)."""
    if not HAS_MCP:
        print("MCP not installed. Run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    server = _create_server()

    # Start dispatcher on QGIS main thread
    _dispatcher.start()

    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope,
            request.receive,
            request._send,
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )

    starlette_app = Starlette(
        routes=[
            Route("/sse", handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ]
    )

    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
    server_uvicorn = uvicorn.Server(config)
    await server_uvicorn.serve()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Aery QGIS MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9876)
    args = parser.parse_args()

    if args.transport == "stdio":
        asyncio.run(run_stdio_server())
    else:
        asyncio.run(run_sse_server(args.host, args.port))


if __name__ == "__main__":
    main()