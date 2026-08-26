"""Test zoom_to_place generated code structure and variables."""

import asyncio
from unittest.mock import MagicMock
from aery_plugin.tools import ToolRegistry


def test_zoom_to_place_generated_code():
    captured_code = []

    async def fake_execute(code_dict):
        captured_code.append(code_dict["code"])
        return "OK"

    reg = ToolRegistry(executor=MagicMock())
    reg._execute_qgis_code = fake_execute

    # Test uncached place (triggers the urlopen / resp block)
    asyncio.run(reg._execute_zoom_to_place({"place": "Koforidua, Ghana"}))
    assert len(captured_code) == 1
    code = captured_code[0]

    # Verify 'with urllib.request.urlopen(req, timeout=20) as resp:' is present
    assert "with urllib.request.urlopen(req, timeout=20) as resp:" in code
    assert "_c = resp.read(64 * 1024)" in code
    compile(code, "<gen>", "exec")
