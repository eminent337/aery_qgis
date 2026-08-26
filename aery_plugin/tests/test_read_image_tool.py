"""Tests for read_image tool."""

import os, tempfile, asyncio
from unittest.mock import MagicMock
from PIL import Image
from aery_plugin.tools import ToolRegistry


def test_read_image_png_execution():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(f, format="PNG")
        tmp_path = f.name

    try:
        reg = ToolRegistry(executor=MagicMock())
        res = asyncio.run(reg._execute_read_image({"path": tmp_path}))
        assert isinstance(res, str)
        assert res.startswith("data:image/png;base64,")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
