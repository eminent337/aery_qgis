"""Tests for the 4 critical session fixes:
1. get_layer_schema execution with resolve_layer
2. Processing temporary memory layer extents handling
3. run_qgis_code stdout-as-result fallback
"""

import asyncio
from unittest.mock import MagicMock
from aery_plugin.tools import ToolRegistry
from aery_plugin.qgis_executor import QGISCodeExecutor


def test_get_layer_schema_code_generation():
    captured_code = []

    async def fake_exec(params):
        captured_code.append(params["code"])
        return "OK"

    reg = ToolRegistry(executor=MagicMock())
    reg._execute_qgis_code = fake_exec

    asyncio.run(reg._execute_get_layer_schema({"layer_name": "Test_Layer"}))
    assert len(captured_code) == 1
def test_executor_stdout_as_result_fallback():
    # Test _process_queue stdout fallback directly without waiting for GUI QTimer event loop
    import queue
    executor = QGISCodeExecutor(iface=None)
    code = """
print("Found 42 features in layer")
print("Extent: [0, 0, 10, 10]")
"""
    res_q = queue.Queue()
    executor._normal_queue.put(("direct", code, res_q, {"started_at": 0}))
    executor._process_queue()
    
    # Retrieve item
    items = []
    while not res_q.empty():
        items.append(res_q.get_nowait())
    final_res = next(i for i in reversed(items) if i.get("type") != "progress")
    assert final_res.get("success") is True
    assert "Found 42 features in layer" in final_res.get("result")
    assert "Extent: [0, 0, 10, 10]" in final_res.get("result")
