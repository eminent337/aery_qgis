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
    """abort() sends abort + abort_bash + abort_retry to stdin."""
    with patch("aery_plugin.rpc_bridge.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_popen.return_value = mock_process

        bridge.spawn()
        bridge.abort()

        # Check that all three abort types were written
        calls = [c[0][0] for c in mock_process.stdin.write.call_args_list]
        types = [json.loads(c.strip())["type"] for c in calls]
        assert "abort" in types
        assert "abort_bash" in types
        assert "abort_retry" in types


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
    """shutdown() closes streams and calls terminate() on the process."""
    with patch("aery_plugin.rpc_bridge.subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_process.poll.return_value = None   # process is still running
        mock_popen.return_value = mock_process

        bridge.spawn()
        bridge.shutdown()

        # shutdown() must close all process streams and terminate the process
        mock_process.stdin.close.assert_called_once()
        mock_process.stdout.close.assert_called_once()
        mock_process.stderr.close.assert_called_once()
        mock_process.terminate.assert_called_once()


def test_load_qgis_system_prompt_reads_from_json(tmp_path):
    """_load_qgis_system_prompt must load from geospatial_rules.json, not hardcoded literals."""
    import aery_plugin.rpc_bridge as rb

    # Point resources dir at a temp dir containing a minimal rules file
    rules = {"identity": {"role": "test_role", "capabilities": "test_caps", "workflow": "test_wf"}}
    resources_dir = tmp_path / "resources"
    resources_dir.mkdir()
    (resources_dir / "geospatial_rules.json").write_text(json.dumps(rules))

    # Build with patched resources dir
    class PatchedBridge(RPCBridge):
        def _get_resources_dir(self):
            return str(resources_dir)

    b = PatchedBridge("/dev/null", 0)
    prompt = b._load_qgis_system_prompt()
    assert "test_role" in prompt
    assert "test_caps" in prompt
    assert "test_wf" in prompt
    # The old hardcoded string must not appear when absent from test JSON
    assert "elite geospatial AI" not in prompt


def test_geospatial_rules_json_is_valid_and_complete():
    """geospatial_rules.json must be valid JSON with all required top-level keys."""
    from pathlib import Path

    rules_path = (
        Path(__file__).parent.parent / "aery_plugin" / "resources" / "geospatial_rules.json"
    )
    data = json.loads(rules_path.read_text())

    required = [
        "identity", "workflow_steps", "crs_rules", "safety_rules",
        "processing_patterns", "error_recovery",
    ]
    for key in required:
        assert key in data, f"missing required key: {key}"
    assert len(data["crs_rules"]) > 0, "crs_rules must not be empty"
    assert len(data["processing_patterns"]) > 0, "processing_patterns must not be empty"
