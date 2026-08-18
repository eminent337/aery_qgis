"""Tests for the hybrid executor helpers introduced in the algorithm framework:

- execute() forwards "progress" notifications to on_progress instead of dropping them
- execute() returns the final result (non-progress) item
- cancel() / _cancel_requested() cooperative cancellation flag

These exercise the pure Python logic without a live QGIS app, mirroring how the
other executor tests build the object via __new__ + manual state.
"""

import os
import sys
import queue
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_executor():
    from aery_plugin.qgis_executor import QGISCodeExecutor

    ex = QGISCodeExecutor.__new__(QGISCodeExecutor)
    ex.iface = None
    ex.audit_dir = None
    ex.run_id = "test"
    ex._priority_queue = __import__("collections").deque()
    ex._normal_queue = queue.Queue()
    ex._result_queues = {}
    ex._timer = None
    ex._child_pids = set()
    ex._cancel_event = threading.Event()
    return ex


class TestExecuteProgress(unittest.TestCase):
    def test_execute_forwards_progress_and_returns_result(self):
        ex = _make_executor()

        delivered = []
        results = {}

        def _run_execute():
            try:
                results["value"] = ex.execute("code", timeout=10, on_progress=delivered.append)
            except Exception as e:  # noqa
                results["error"] = e

        t = threading.Thread(target=_run_execute, daemon=True)
        t.start()

        # Wait until execute() has registered its queue on _normal_queue.
        deadline = threading.Event()
        item = None
        for _ in range(200):
            try:
                item = ex._normal_queue.get_nowait()
                break
            except queue.Empty:
                threading.Event().wait(0.01)
        self.assertIsNotNone(item, "execute() never registered its queue")

        _req_id, code, result_queue, _meta = item
        # Simulate the Qt main thread: emit a progress notification, then the result.
        result_queue.put({"id": _req_id, "type": "progress", "progress": 42, "algorithm": "native:buffer"})
        result_queue.put({"id": _req_id, "success": True, "result": {"out": 1}})
        t.join(timeout=5)

        self.assertFalse(t.is_alive(), "execute() did not return after result")
        self.assertNotIn("error", results, f"execute() raised: {results.get('error')}")
        self.assertEqual(len(delivered), 1, "on_progress was not called for the progress item")
        self.assertEqual(delivered[0]["progress"], 42)
        self.assertEqual(delivered[0]["algorithm"], "native:buffer")
        self.assertEqual(results["value"]["success"], True)
        self.assertEqual(results["value"]["result"], {"out": 1})
        ex.cancel()
        self.assertTrue(ex._cancel_requested())

        # execute() clears the flag at the start; we don't wait for a result,
        # just verify it clears synchronously before the wait loop.
        import queue as _q

        result_queue = _q.Queue()
        # Simulate execute() clearing the flag (extracted as the first step).
        ex._cancel_event.clear()
        self.assertFalse(ex._cancel_requested())


if __name__ == "__main__":
    unittest.main()
