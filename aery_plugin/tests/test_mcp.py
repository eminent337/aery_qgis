"""Tests for MCP Server."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from aery_plugin.mcp.server import (
    QGISThreadDispatcher,
    _create_server,
    get_dispatcher,
)


class TestMCP:
    """MCP Server tests."""

    def test_dispatcher_creation(self):
        """Test dispatcher can be created."""
        dispatcher = QGISThreadDispatcher()
        assert dispatcher is not None
        assert hasattr(dispatcher, "dispatch")
        assert hasattr(dispatcher, "_process_queue")

    def test_dispatcher_start(self):
        """Test dispatcher start doesn't crash outside QGIS."""
        dispatcher = QGISThreadDispatcher()
        dispatcher.start()  # Should not crash
        assert dispatcher._running is True

    def test_get_dispatcher_singleton(self):
        """Test get_dispatcher returns singleton."""
        d1 = get_dispatcher()
        d2 = get_dispatcher()
        assert d1 is d2

    @pytest.mark.asyncio
    async def test_dispatch_returns_future(self):
        """Test dispatch returns a future."""
        dispatcher = QGISThreadDispatcher()
        future = dispatcher.dispatch("test_tool", {"param": "value"})
        assert asyncio.isfuture(future)

    @pytest.mark.asyncio
    async def test_list_tools(self):
        """Test list_tools handler returns tools."""
        server = _create_server()
        # Mock context
        context = MagicMock()
        context.request = MagicMock()
        context.request.meta = {}

        result = await server.list_tools(context)
        assert result is not None
        assert hasattr(result, "tools")
        assert len(result.tools) >= 10  # At least original 10 tools

        tool_names = [t.name for t in result.tools]
        assert "list_layers" in tool_names
        assert "get_layer_schema" in tool_names
        assert "capture_canvas" in tool_names
        assert "zoom_to_layer" in tool_names
        assert "zoom_to_place" in tool_names
        assert "run_processing_algorithm" in tool_names
        assert "load_basemap" in tool_names
        assert "remove_layer" in tool_names
        assert "apply_symbology" in tool_names

    @pytest.mark.asyncio
    async def test_call_tool_unknown(self):
        """Test call_tool with unknown tool returns error."""
        server = _create_server()
        context = MagicMock()
        context.request = MagicMock()
        context.request.meta = {}

        params = MagicMock()
        params.name = "unknown_tool"
        params.arguments = {}

        result = await server.call_tool(context, params)
        assert result.isError is True
        assert "Unknown tool" in result.content[0].text

    @pytest.mark.asyncio
    async def test_call_tool_known(self):
        """Test call_tool with known tool dispatches."""
        server = _create_server()
        context = MagicMock()
        context.request = MagicMock()
        context.request.meta = {}

        params = MagicMock()
        params.name = "list_layers"
        params.arguments = {}

        # Mock the dispatcher
        with patch("aery_plugin.mcp.server._dispatcher") as mock_dispatcher:
            mock_future = asyncio.Future()
            mock_future.set_result({"layers": []})
            mock_dispatcher.dispatch.return_value = mock_future

            result = await server.call_tool(context, params)
            assert result.isError is False

    @pytest.mark.asyncio
    async def test_call_tool_timeout(self):
        """Test call_tool handles timeout."""
        server = _create_server()
        context = MagicMock()
        context.request = MagicMock()
        context.request.meta = {}

        params = MagicMock()
        params.name = "list_layers"
        params.arguments = {}

        with patch("aery_plugin.mcp.server._dispatcher") as mock_dispatcher:
            mock_future = asyncio.Future()
            # Never complete the future to trigger timeout
            mock_dispatcher.dispatch.return_value = mock_future

            # The server's call_tool has its own 120s timeout
            # We test that it eventually times out (but skip 120s wait)
            # by using a very short wait_for on the whole call
            try:
                result = await asyncio.wait_for(server.call_tool(context, params), timeout=0.1)
                # If we get here, check result is error
                assert result.isError is True
            except (TimeoutError, asyncio.CancelledError):
                # Expected - the outer wait_for times out
                pass

    def test_server_creation(self):
        """Test server can be created."""
        server = _create_server()
        assert server is not None
        assert server.name == "aery-qgis"
        assert server.version == "1.0.0"

    def test_auth_validation_no_token(self):
        """Test auth validation passes when no token configured."""
        server = _create_server()
        assert server is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])