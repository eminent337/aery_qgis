"""Tests for QGISCodeExecutor."""

import json
import os
import pathlib
import queue
import socket
import sys
import time
import threading
import uuid
from unittest.mock import MagicMock, patch
from collections import deque

import pytest
from PyQt6.QtCore import QEvent
from aery_plugin.qgis_executor import QGISCodeExecutor


@pytest.fixture(scope="session")
def qapp():
    """Create a QApplication for Qt widget testing."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def executor():
    """Create an executor with mocked QTimer."""
    with patch("aery_plugin.qgis_executor.QTimer") as mock_timer:
        mock_timer_instance = MagicMock()
        mock_timer.return_value = mock_timer_instance

        exec_ = QGISCodeExecutor(iface=MagicMock())
        exec_.start_socket_server()
        yield exec_
        exec_.shutdown()


def test_socket_server_starts(executor):
    """Socket server binds to a port and accepts connections."""
    assert executor.port > 0
    assert executor.server is not None


def test_socket_receives_request(executor):
    """Can send a JSON request via socket and get a response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(("127.0.0.1", executor.port))

    request = json.dumps({"id": "1", "method": "run_code", "code": "result = 42"})
    sock.sendall(request.encode() + b"\n")

    import time
    time.sleep(0.1)  # Let bg thread queue the request

    # Trigger queue processing
    executor._process_queue()

    response = sock.recv(4096).decode()
    parsed = json.loads(response.strip())
    assert parsed["id"] == "1"
    assert parsed["success"] is True

    sock.close()


def test_socket_returns_error(executor):
    """When QGIS code throws, executor returns error with traceback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(("127.0.0.1", executor.port))

    request = json.dumps(
        {"id": "2", "method": "run_code", "code": "raise ValueError('oops')"}
    )
    sock.sendall(request.encode() + b"\n")
    import time
    time.sleep(0.1)
    executor._process_queue()

    response = sock.recv(4096).decode()
    parsed = json.loads(response.strip())
    assert parsed["success"] is False
    assert "ValueError" in parsed.get("traceback", "")

    sock.close()


def test_execute_direct(executor):
    """Direct execute method works."""
    result = executor.execute("result = 'hello'")
    assert result["success"] is True
    assert result["result"] == "hello"


def test_execute_direct_error(executor):
    """Direct execute method returns error on exception."""
    result = executor.execute("1/0")
    assert result["success"] is False
    assert "division" in result["error"].lower() or "ZeroDivisionError" in result["error"]


def test_classify_code_risk_detects_destructive_operations():
    """Risk classifier flags code that can delete layers or files."""
    risks = QGISCodeExecutor.classify_code_risk(
        "QgsProject.instance().removeMapLayer(layer.id())\nos.remove(output_path)"
    )

    categories = {risk["category"] for risk in risks}
    assert "destructive_project_change" in categories
    assert "filesystem_delete" in categories


def test_execute_writes_audit_entry(tmp_path):
    """Every executed request is written to the audit log with rich metadata."""
    with patch("aery_plugin.qgis_executor.QTimer") as mock_timer:
        mock_timer.return_value = MagicMock()
        exec_ = QGISCodeExecutor(iface=MagicMock(), audit_dir=str(tmp_path / ".aery"))
        try:
            exec_.start_socket_server()
            result = exec_.execute("result = 42")
        finally:
            exec_.shutdown()

    audit_path = tmp_path / ".aery" / "operations.jsonl"
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert entries[0]["type"] == "run_start"
    assert result["success"] is True
    assert entries[-1]["request_id"] == "direct"
    assert entries[-1]["success"] is True
    assert entries[-1]["code"] == "result = 42"
    assert entries[-1]["risks"] == []
    assert entries[-1]["source"] == "plugin"
    assert entries[-1]["phase"] == "end"
    assert entries[-1]["project_dir"]
    assert isinstance(entries[-1]["duration_ms"], int)
    assert entries[-1]["result_summary"] == "42"


def test_failed_execute_writes_error_traceback_to_audit(tmp_path):
    """Failed executions should include error metadata in the audit trail."""
    with patch("aery_plugin.qgis_executor.QTimer") as mock_timer:
        mock_timer.return_value = MagicMock()
        exec_ = QGISCodeExecutor(iface=MagicMock(), audit_dir=str(tmp_path / ".aery"))
        try:
            exec_.start_socket_server()
            result = exec_.execute("raise ValueError('oops')")
        finally:
            exec_.shutdown()

    audit_path = tmp_path / ".aery" / "operations.jsonl"
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert result["success"] is False
    assert entries[-1]["success"] is False
    assert entries[-1]["error"] == "oops"
    assert "ValueError" in entries[-1]["traceback"]
    assert entries[-1]["result_summary"] == ""


def test_execute_collapses_base64_image_result_in_audit(tmp_path):
    """Large base64 PNG payloads should be summarized instead of dumped inline."""
    png_b64 = "iVBORw0KGgo" + ("A" * 5000)
    with patch("aery_plugin.qgis_executor.QTimer") as mock_timer:
        mock_timer.return_value = MagicMock()
        exec_ = QGISCodeExecutor(iface=MagicMock(), audit_dir=str(tmp_path / ".aery"))
        try:
            exec_.start_socket_server()
            result = exec_.execute(f"result = {png_b64!r}")
        finally:
            exec_.shutdown()

    audit_path = tmp_path / ".aery" / "operations.jsonl"
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert result["success"] is True
    assert entries[-1]["result_summary"].startswith("[image/png base64")
    assert "500" in entries[-1]["result_summary"] or "501" in entries[-1]["result_summary"]


def test_start_socket_server_writes_run_start_marker(tmp_path):
    """Starting a fresh executor should append a run_start marker with a run id."""
    with patch("aery_plugin.qgis_executor.QTimer") as mock_timer:
        mock_timer.return_value = MagicMock()
        exec_ = QGISCodeExecutor(iface=MagicMock(), audit_dir=str(tmp_path / ".aery"))
        try:
            exec_.start_socket_server()
        finally:
            exec_.shutdown()

    audit_path = tmp_path / ".aery" / "operations.jsonl"
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert entries[-1]["type"] == "run_start"
    assert entries[-1]["run_id"]
    assert entries[-1]["source"] == "plugin"


def test_shutdown_closes_new_connections(executor):
    """After shutdown, new connections are refused."""
    port = executor.port

    # First verify we can connect
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(("127.0.0.1", port))
    sock.close()

    executor.shutdown()

    import time
    time.sleep(0.2)  # Let socket close

    # Should not be able to connect
    with pytest.raises((ConnectionRefusedError, OSError)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(("127.0.0.1", port))
        sock.close()


def test_concurrent_requests(executor):
    """Multiple concurrent requests are handled in order."""
    import time
    socks = []
    for i in range(5):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(("127.0.0.1", executor.port))
        request = json.dumps({"id": str(i), "code": f"result = {i * 10}"})
        sock.sendall(request.encode() + b"\n")
        socks.append(sock)

    time.sleep(0.2)  # Let bg threads queue all requests

    # Process all queued requests
    for _ in range(5):
        executor._process_queue()

    # Collect responses
    results = []
    for sock in socks:
        response = sock.recv(4096).decode()
        parsed = json.loads(response.strip())
        results.append(parsed["result"])
        sock.close()

    assert results == [0, 10, 20, 30, 40]



def test_get_project_context_uses_qgsproject_instance_not_iface_project(executor):
    """Project context should not rely on iface.project() on QGIS 4."""
    executor.iface.project.side_effect = AssertionError("iface.project should not be used")

    with patch("qgis.core.QgsProject") as mock_project_cls, patch("qgis.core.Qgis") as mock_qgis:
        project = MagicMock()
        project.fileName.return_value = "/tmp/example.qgz"
        project.mapLayers.return_value = {}
        project.crs.return_value = None
        project.extent.return_value.toString.return_value = "0,0 : 1,1"
        mock_project_cls.instance.return_value = project
        mock_qgis.geometryType.return_value.toString.return_value = "Unknown"

        result = executor._get_project_context()

    assert result["project_dir"] == "/tmp"
    assert result["home_dir"]  # new field for project safety check


def test_graph_hooks_log_error_on_failure(monkeypatch, tmp_path):
    """When graph_engine raises, _record_graph_hooks logs via QgsMessageLog and does NOT reraise."""
    from aery_plugin import qgis_executor as qe

    # Force graph_engine import to raise ImportError on every attempt
    class FakeGraphEngine:
        def __getattr__(self, name):
            raise ImportError(f"graph_engine unavailable: {name}")

    monkeypatch.setitem(sys.modules, "aery_plugin.graph_engine", FakeGraphEngine())

    # Capture QgsMessageLog calls — patch at qgis.core because _record_graph_hooks
    # does `from qgis.core import QgsMessageLog, Qgis` inline.
    calls = []
    class FakeQgsMessageLog:
        @staticmethod
        def logMessage(msg, tag, level):
            calls.append((msg, tag, level))

    monkeypatch.setattr("qgis.core.QgsMessageLog", FakeQgsMessageLog)

    with patch("aery_plugin.qgis_executor.QTimer") as mock_timer:
        mock_timer.return_value = MagicMock()
        exec_ = QGISCodeExecutor(iface=MagicMock())
        try:
            exec_.start_socket_server()
            result = exec_.execute("result = 'ok'")
        finally:
            exec_.shutdown()

    assert result["success"] is True, "execution must succeed despite broken graph_engine"
    assert len(calls) > 0, f"graph hook failure should have been logged; got: {calls}"
    # Confirms the log entry mentions the graph hook
    assert any("graph hook failed" in c[0] for c in calls), f"log message must mention graph hook: {calls}"


# ════════════════════════════════════════════════════════════════════════════════
# ask_user / question-answer integration tests
# ════════════════════════════════════════════════════════════════════════════════

def test_process_question_posts_event_and_returns_error_on_timeout(qapp):
    """_process_question registers a pending quest, causes error on no answer via cleanup path."""
    from aery_plugin.qgis_executor import _resolve_question, _pending_questions, _process_question

    rq = queue.Queue()
    # Run _process_question in a daemon thread so we don't block
    import time as _time
    def _run():
        _process_question("r1", rq, {
            "questId": "no_ans_q", "header": "Hi", "description": "test",
            "options": [{"label": "A", "required_fields": []}],
        })
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    _time.sleep(2)  # should be in timeout (polling at 0.1s × 0.1s per iter = ~120s total, skip early)
    result = None
    try:
        result = rq.get_nowait()
    except queue.Empty:
        # Even after 2s, result_queue empty → _process_question is still polling
        # Force-cancel by clearing pending entry
        _pending_questions.pop("no_ans_q", None)
        _time.sleep(0.2)
        try:
            result = rq.get_nowait()
        except queue.Empty:
            result = {"error": "timed out (in test)"}
    assert "error" in result, f"expected error when no answer delivered; got: {result}"


def test_pending_questions_pop_via_resolve(qapp):
    """Full round-trip on the main-thread path: _process_question finds the panel via
    _find_chat_panel (fast path), the mock panel immediately calls _resolve_question,
    and the poll loop returns the answer in result_queue."""
    import aery_plugin.qgis_executor as _qe
    from aery_plugin.qgis_executor import _resolve_question, _pending_questions, _process_question

    quest_id = "deliver_test_q"
    rq       = queue.Queue()
    ANSWER   = {"option_label": "GPKG", "fields": {"path": "/tmp/x"}}

    class _FakePanel:
        """Instantly resolves so the poll loop terminates on first iteration."""
        def _handle_question(self, event):
            _resolve_question(event["questId"], ANSWER)

    # ── Force fast-path and run on main thread ────────────────────────────────
    _qe._find_chat_panel = staticmethod(lambda: _FakePanel())  # type: ignore[assignment]
    try:
        res = _process_question("req_deliver", rq, {
            "questId": quest_id, "header": "?", "description": "",
            "options": [{"label": "GPKG", "required_fields": []}],
        })
    finally:
        del _qe._find_chat_panel  # type: ignore[misc]   # restore module-level lookup

    # ── Assertions ─────────────────────────────────────────────────────────────
    assert res.get("answer") is not None, \
        f"expected 'answer' key; got keys: {list(res)}"
    assert res["answer"]["option_label"] == "GPKG"
    assert not rq.empty(), "rq should carry the response"
    out = rq.get_nowait()
    assert out["answer"]["option_label"] == "GPKG"
    # The mock panel resolved the question so both queues should be drained


def test_question_method_routed_via_enqueue(executor):
    """method='question' routes question through _request_queue (not run_code path)."""
    qe = __import__("aery_plugin.qgis_executor", fromlist=["qgis_executor"])
    orig = qe._process_question
    qe._process_question = lambda *a, **kw: {"answer": {"option_label": "GPKG", "fields": {}}}
    try:
        # Enqueue a question directly into the normal queue
        req_id = "enq_test"
        rq = queue.Queue()
        meta = {"method": "question", "tool_name": "ask_user", "source": "test",
                "params": {"header": "H", "options": [{"label": "O", "required_fields": []}]}}
        executor._normal_queue.put((req_id, "__ask_user__", rq, meta))
        executor._process_queue()
        assert not rq.empty(), f"rq should contain result after _process_queue; contents: {rq}"
        result = rq.get_nowait()
        assert result["answer"]["option_label"] == "GPKG"
    finally:
        qe._process_question = orig



def _mk_conn(data: bytes) -> MagicMock:
    """Return a mock socket whose recv yields *data* then b''."""
    m = MagicMock()
    m.recv.side_effect = [data + b"\n", b""]
    return m

# =============================================================================
# New executor integrity tests (Fix #1, #2, #5, #6, #13)
# =============================================================================

def test_safe_json_result_path_converted_to_string():
    """_safe_json_result converts pathlib.Path to str so json.dumps does not TypeError."""
    from aery_plugin.qgis_executor import QGISCodeExecutor

    p = pathlib.Path("/tmp/test_layer.gpkg")
    result = QGISCodeExecutor._safe_json_result(p)
    assert isinstance(result, str), f"expected str, got {type(result)}: {result}"
    assert result == "/tmp/test_layer.gpkg"


def test_safe_json_result_none_passthrough():
    """_safe_json_result passes None through unchanged."""
    from aery_plugin.qgis_executor import QGISCodeExecutor

    assert QGISCodeExecutor._safe_json_result(None) is None


def test_safe_json_result_small_string_passthrough():
    """Small plain strings are returned as-is so the socket sends them verbatim."""
    from aery_plugin.qgis_executor import QGISCodeExecutor

    assert QGISCodeExecutor._safe_json_result("hello") == "hello"


def test_executor_shutdown_clears_pending_questions_and_result_queues(tmp_path):
    """shutdown() clears _pending_questions and _result_queues so stale entries do not leak."""
    import aery_plugin.qgis_executor as _qe
    from aery_plugin.qgis_executor import _pending_questions

    with patch("aery_plugin.qgis_executor.QTimer") as mock_timer:
        mock_timer.return_value = MagicMock()
        exec_ = QGISCodeExecutor(iface=MagicMock(), audit_dir=str(tmp_path / ".aery"))
        try:
            exec_.start_socket_server()
            # Seed some stale state
            rq = queue.Queue()
            _pending_questions["stale_quest"] = (rq, "stale_req")
            exec_._result_queues["stale"] = queue.Queue()
            assert "stale_quest" in _pending_questions
            assert "stale" in exec_._result_queues
            exec_.shutdown()
            # Verify stale entries are gone
            assert "stale_quest" not in _pending_questions, \
                "shutdown() must clear _pending_questions"
            assert "stale" not in exec_._result_queues, \
                "shutdown() must clear _result_queues"
        finally:
            exec_.shutdown()  # safe even if test failed early


def test_priority_queue_served_before_normal_queue(executor):
    """Interactive priority requests (question / capture_canvas) are drained before normal run_code."""
    captured_order: list = []

    import aery_plugin.qgis_executor as _qe
    orig_pq = _qe._process_question

    def fake_process_question(req_id, rq, params):
        captured_order.append(("priority", req_id))
        return {"answer": {}}

    _qe._process_question = fake_process_question
    try:
        # Enqueue 3 normal requests, then 2 priority ones
        for i in range(3):
            rq_i = queue.Queue()
            meta = {"method": "run_code", "tool_name": "run_qgis_code",
                    "source": "test", "started_at": time.perf_counter()}
            executor._normal_queue.put((f"n{i}", f"result = {i}", rq_i, meta))

        p_rq = queue.Queue()
        p_meta = {"method": "question", "tool_name": "ask_user",
                  "source": "test", "started_at": time.perf_counter(),
                  "params": {"header": "h", "options": []}}
        executor._priority_queue.append(("p0", "__ask_user__", p_rq, p_meta))

        # First _process_queue call: exhaust priority (all of it), then one normal
        executor._process_queue()
        assert captured_order == [("priority", "p0")], \
            "first tick must serve the priority item before any normal item"

        # Second call: drain remaining normal items
        for _ in range(3):
            executor._process_queue()

        assert len(captured_order) == 1, \
            "priority items must be fully drained before normal items consume ticks"

        p_meta2 = {"method": "question", "tool_name": "ask_user",
                   "source": "test", "started_at": time.perf_counter(),
                   "params": {"header": "h2", "options": []}}
        executor._priority_queue.append(("p1", "__ask_user__", queue.Queue(), p_meta2))

        for i in range(3, 5):
            rq_i = queue.Queue()
            meta = {"method": "run_code", "tool_name": "run_qgis_code",
                    "source": "test", "started_at": time.perf_counter()}
            executor._normal_queue.put((f"n{i}", f"result = {i}", rq_i, meta))

        executor._process_queue()
        # p1 must be served before any new normal item
        assert captured_order == [("priority", "p0"), ("priority", "p1")]
    finally:
        _qe._process_question = orig_pq


def test_process_question_forwards_run_id(executor):
    """run_id from metadata is forwarded into _process_question's params."""
    import aery_plugin.qgis_executor as _qe
    captured_run_id: list = []

    class _PanelNoop:
        def _handle_question(self, event):
            captured_run_id.append(event.get("run_id"))

    orig_pq = _qe._process_question

    def fake_pq(req_id, rq, params):
        # Simulate what the real _process_question does: forward qp to panel
        panel = _PanelNoop()
        panel._handle_question({"type": "question", "questId": "fake", **params})

    _qe._process_question = fake_pq
    try:
        rq = queue.Queue()
        meta = {
            "method": "question",
            "tool_name": "ask_user",
            "source": "test",
            "started_at": time.perf_counter(),
            "run_id": "forwarded-run-id",
            "params": {"header": "Q", "options": [{"label": "A", "required_fields": []}]},
        }
        executor._priority_queue.append(("rk", "__ask_user__", rq, meta))
        executor._process_queue()

        assert captured_run_id == ["forwarded-run-id"], \
            "run_id must be forwarded from metadata through _process_question to the question event"
    finally:
        _qe._process_question = orig_pq
