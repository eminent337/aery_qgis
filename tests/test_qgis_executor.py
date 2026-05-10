"""Tests for QGISCodeExecutor."""

import json
import socket
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
