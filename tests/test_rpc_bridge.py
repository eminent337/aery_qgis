"""Tests for RPCBridge."""

import json
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from aery_plugin.rpc_bridge import RPCBridge, _find_aery_binary


@pytest.fixture
def bridge():
    """Create an RPCBridge with mocked subprocess."""
    with (
        patch("aery_plugin.rpc_bridge.subprocess.Popen") as mock_popen,
        patch("aery_plugin.rpc_bridge.print"),  # Suppress stderr noise
    ):
        # Mock process with stdin/stdout
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        # stdout.readline returns empty (no messages)
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr.readline.return_value = ""
        mock_popen.return_value = mock_process

        b = RPCBridge(cwd="/tmp", port=9999)
        yield b
        b.shutdown()


def test_find_aery_binary_bundled():
    """_find_aery_binary returns the bundled binary path when it exists."""
    binary = _find_aery_binary()
    assert "aery-qgis-runner" in binary
    assert os.path.isfile(binary), f"Binary not found at: {binary}"


def test_find_aery_binary_fallback():
    """When bundled binary doesn't exist, falls back to 'aery'."""
    with patch("aery_plugin.rpc_bridge.os.path.isfile", return_value=False):
        with patch("aery_plugin.rpc_bridge.os.access", return_value=False):
            assert _find_aery_binary() == "aery"


def test_spawn_bundled_binary(bridge):
    """spawn() uses the bundled binary with port as positional arg."""
    with (
        patch("aery_plugin.rpc_bridge.subprocess.Popen") as mock_popen,
        patch("aery_plugin.rpc_bridge._find_aery_binary", return_value="/mock/bin/aery-qgis-runner"),
    ):
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_popen.return_value = mock_process

        bridge.spawn()

        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[0] == "/mock/bin/aery-qgis-runner"
        assert args[1] == "9999"


def test_spawn_system_aery_fallback(bridge):
    """When no bundled binary, spawns system aery with --mode rpc."""
    with (
        patch("aery_plugin.rpc_bridge.subprocess.Popen") as mock_popen,
        patch("aery_plugin.rpc_bridge._find_aery_binary", return_value="aery"),
    ):
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_popen.return_value = mock_process

        bridge.spawn()

        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[0] == "aery"
        assert "--mode" in args
        assert "rpc" in args


def test_spawn_file_not_found(bridge):
    """When aery is not found, error_occurred is emitted."""
    with patch("aery_plugin.rpc_bridge.subprocess.Popen") as mock_popen:
        mock_popen.side_effect = FileNotFoundError()

        errors = []
        bridge.error_occurred.connect(errors.append)

        bridge.spawn()
        assert len(errors) == 1
        assert "not found" in errors[0].lower()


def test_prompt_sends_command(bridge):
    """prompt() sends a prompt command via stdin."""
    with patch("aery_plugin.rpc_bridge.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_popen.return_value = mock_process

        bridge.spawn()
        bridge.prompt("hello world")

        # Check what was written to stdin
        written = mock_process.stdin.write.call_args[0][0]
        data = json.loads(written.strip())
        assert data["type"] == "prompt"
        assert data["message"] == "hello world"


def test_abort_sends_command(bridge):
    """abort() sends an abort command."""
    with patch("aery_plugin.rpc_bridge.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_popen.return_value = mock_process

        bridge.spawn()
        bridge.abort()

        written = mock_process.stdin.write.call_args[0][0]
        data = json.loads(written.strip())
        assert data["type"] == "abort"


def test_dispatch_event_received(bridge):
    """Streaming events are emitted via event_received signal."""
    events = []
    bridge.event_received.connect(events.append)

    test_event = {"type": "thinking", "content": "thinking..."}
    bridge._dispatch_event(test_event)

    assert len(events) == 1
    assert events[0]["type"] == "thinking"


def test_dispatch_response_received(bridge):
    """Response events are emitted via response_received signal."""
    responses = []

    def on_response(cmd, data):
        responses.append((cmd, data))

    bridge.response_received.connect(on_response)

    test_response = {"type": "response", "command": "prompt", "success": True}
    bridge._dispatch_event(test_response)

    assert len(responses) == 1
    assert responses[0][1]["command"] == "prompt"


def test_shutdown_terminates_process(bridge):
    """shutdown() sends SIGTERM to the process."""
    with patch("aery_plugin.rpc_bridge.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_popen.return_value = mock_process

        bridge.spawn()
        bridge.shutdown()

        mock_process.send_signal.assert_called_once()
