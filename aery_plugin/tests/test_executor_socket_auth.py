"""Tests for SocketServer per-instance auth token (P2 Step 2).

Without authentication, any local process can connect to 127.0.0.1:<port>
and submit Python code for execution inside the QGIS process. This test
verifies that the server rejects requests with missing or invalid tokens
and accepts only the token generated at startup.
"""

import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from aery_plugin.executor_socket import SocketServer


class _MockExecutor:
    """Minimal executor stand-in for SocketServer."""

    def __init__(self):
        self._result_queues = {}
        self._normal_queue = None
        self._priority_queue = []
        self.run_id = "test-run-001"

    def _get_project_context(self):
        return {"mock": True}


def _send_request(port: int, payload: dict, timeout: float = 5.0) -> dict:
    """Connect to the socket, send one JSON line, and return the response."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(("127.0.0.1", port))
        s.sendall((json.dumps(payload) + "\n").encode())
        # Read until newline
        data = b""
        deadline = time.monotonic() + timeout
        while b"\n" not in data and time.monotonic() < deadline:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
        return json.loads(data.decode().strip())


@pytest.fixture
def running_server():
    executor = _MockExecutor()
    server = SocketServer(executor)
    server.start()
    assert server.port is not None
    yield server
    server.shutdown()


def test_socket_server_generates_auth_token(running_server):
    assert running_server.auth_token
    assert isinstance(running_server.auth_token, str)
    assert len(running_server.auth_token) >= 32


def test_request_without_token_is_rejected(running_server):
    response = _send_request(running_server.port, {"id": "req-1", "method": "get_project_context"})
    assert response.get("success") is False
    assert "Unauthorized" in response.get("error", "")


def test_request_with_wrong_token_is_rejected(running_server):
    response = _send_request(
        running_server.port,
        {"id": "req-2", "method": "get_project_context", "auth_token": "not-the-token"},
    )
    assert response.get("success") is False
    assert "Unauthorized" in response.get("error", "")


def test_request_with_valid_token_is_accepted(running_server):
    response = _send_request(
        running_server.port,
        {"id": "req-3", "method": "get_project_context", "auth_token": running_server.auth_token},
    )
    assert response.get("success") is True
    assert response.get("result") == {"mock": True}


def test_executor_exposes_auth_token_property():
    from aery_plugin.qgis_executor import QGISCodeExecutor

    # QGISCodeExecutor requires an iface; we only test that the property exists.
    assert hasattr(QGISCodeExecutor, "auth_token")
    # The property is a descriptor wrapping socket_server.auth_token; we cannot
    # instantiate without QGIS, but the attribute path is verified statically.
