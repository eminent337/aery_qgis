"""Unit test for load_layer tool in ToolRegistry."""

import asyncio
from unittest.mock import MagicMock
from aery_plugin.tools import ToolRegistry


def test_load_layer_tool_execution():
    captured = []

    async def fake_exec(params):
        captured.append(params["code"])
        return "OK"

    reg = ToolRegistry(executor=MagicMock())
    reg._execute_qgis_code = fake_exec

    asyncio.run(reg._execute_load_layer_tool({"path": "/tmp/test.tif", "layer_name": "My_Raster"}))
    assert len(captured) == 1
    code = captured[0]
    assert "QgsRasterLayer" in code
    assert "QgsProject.instance().addMapLayer(layer)" in code
    compile(code, "<gen>", "exec")
