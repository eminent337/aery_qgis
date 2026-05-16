"""Tests for QGISCodeExecutor."""

import json
import os
import socket
import sys
from unittest.mock import MagicMock, patch

import pytest
from aery_plugin.qgis_executor import QGISCodeExecutor


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
