"""Unit tests for georeference_image tool."""

import os, tempfile, asyncio
from unittest.mock import MagicMock, patch
from aery_plugin.tools import ToolRegistry
from aery_plugin.geospatial_tools import georeference_image


def test_georeference_image_validation_min_gcps():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_img = f.name
    try:
        res = georeference_image(tmp_img, gcps=[{"pixel_x": 0, "pixel_y": 0, "map_x": 0, "map_y": 0}], load_to_qgis=False)
        assert res["success"] is False
        assert "at least 3" in res["error"]
    finally:
        if os.path.exists(tmp_img):
            os.remove(tmp_img)

def test_georeference_image_tool_code_generation():
    captured = []

    async def fake_exec(params):
        captured.append(params["code"])
        return "OK"

    reg = ToolRegistry(executor=MagicMock())
    reg._execute_qgis_code = fake_exec

    gcps = [
        {"pixel_x": 0, "pixel_y": 0, "map_x": -0.2, "map_y": 5.6},
        {"pixel_x": 1000, "pixel_y": 0, "map_x": -0.1, "map_y": 5.6},
        {"pixel_x": 1000, "pixel_y": 1000, "map_x": -0.1, "map_y": 5.5},
        {"pixel_x": 0, "pixel_y": 1000, "map_x": -0.2, "map_y": 5.5},
    ]

    asyncio.run(reg._execute_georeference_image({
        "input_image_path": "/tmp/drone.png",
        "gcps": gcps,
        "target_crs": "EPSG:4326"
    }))

    assert len(captured) == 1
    code = captured[0]
    assert "georeference_image" in code
    assert "gcp_count" in code
    compile(code, "<gen>", "exec")
