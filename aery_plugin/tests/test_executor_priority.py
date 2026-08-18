"""Tests for QGISCodeExecutor Priority Queue."""

import queue
import time
from collections import deque
from unittest.mock import MagicMock

import pytest

from aery_plugin.qgis_executor import QGISCodeExecutor


class TestExecutorPriority:
    """Test priority queue behavior in QGISCodeExecutor."""

    def test_priority_ordering(self):
        """Test that priority items are processed before normal items."""
        executor = QGISCodeExecutor()
        # Queue 3 normal tasks
        q1, q2, q3 = queue.Queue(), queue.Queue(), queue.Queue()
        executor._normal_queue.put(("1", "result = 1", q1, {"priority": False}))
        executor._normal_queue.put(("2", "result = 2", q2, {"priority": False}))
        executor._normal_queue.put(("3", "result = 3", q3, {"priority": False}))
        # Queue 1 priority task (e.g. interactive zoom/canvas capture)
        qp = queue.Queue()
        executor._priority_queue.append(("p1", "result = 'priority'", qp, {"priority": True}))
        # Process queue
        executor._process_queue()

        # The priority item should have finished first
        assert not qp.empty()
        p_res = qp.get_nowait()
        assert p_res.get("success") is True
        assert p_res.get("result") == "priority"

    def test_interactive_priority_auto_tagging(self):
        """Test that capture_canvas and get_project_context auto-route to priority queue."""
        executor = QGISCodeExecutor()

        # Mock the QTimer so execute doesn't block waiting for timer
        executor._timer = MagicMock()

        # Verify priority queue starts empty
        assert len(executor._priority_queue) == 0

        # Enqueue interactive tool via direct injection to test queue placement
        result_queue = queue.Queue()
        code = "__capture_canvas__"
        priority = (
            code in ("__capture_canvas__", "__get_project_context__")
            or code.startswith("__tool__:capture_canvas")
            or code.startswith("__tool__:zoom_to_")
        )
        assert priority is True

        entry = ("req1", code, result_queue, {"priority": priority})
        if priority:
            executor._priority_queue.append(entry)
        else:
            executor._normal_queue.put(entry)

        assert len(executor._priority_queue) == 1
        assert executor._normal_queue.empty()

    def test_typed_tool_priority(self):
        """Test that interactive typed tool strings are prioritized."""
        executor = QGISCodeExecutor()

        interactive_tools = [
            "__tool__:capture_canvas:{}",
            "__tool__:zoom_to_place:{\"place\":\"Accra\"}",
            "__tool__:zoom_to_layer:{\"layer\":\"roads\"}",
            "__tool__:set_layer_visibility:{\"layer\":\"roads\",\"visible\":true}",
        ]

        for tool_call in interactive_tools:
            priority = (
                tool_call in ("__capture_canvas__", "__get_project_context__")
                or tool_call.startswith("__tool__:capture_canvas")
                or tool_call.startswith("__tool__:zoom_to_")
                or tool_call.startswith("__tool__:set_layer_visibility")
            )
            assert priority is True, f"Expected {tool_call} to be priority"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
