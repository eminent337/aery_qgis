"""Integration test: binary + RPC communication.

Tests the bundled Aery binary with embedded QGIS tools:
1. Binary starts with a TCP port number as argument
2. Tools are embedded in the binary (no external extension file)
3. RPC commands work correctly
4. Abort doesn't crash
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent / "aery_plugin"
BINARY_PATH = PLUGIN_DIR / "bin" / "aery-qgis-runner"


def test_binary_exists():
    """The bundled binary exists and is executable."""
    assert BINARY_PATH.is_file(), f"Binary not found: {BINARY_PATH}"
    assert os.access(BINARY_PATH, os.X_OK), "Binary not executable"


def test_binary_rejects_bad_port():
    """Binary rejects invalid port arguments."""
    proc = subprocess.Popen(
        [str(BINARY_PATH), "0"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _, stderr = proc.communicate(timeout=5)
    assert proc.returncode == 1
    assert "port" in stderr.lower()


def test_binary_rejects_missing_port():
    """Binary rejects missing port argument."""
    proc = subprocess.Popen(
        [str(BINARY_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _, stderr = proc.communicate(timeout=5)
    assert proc.returncode == 1
    assert "port" in stderr.lower()


def test_binary_responds_to_get_state():
    """Binary starts with port and responds to get_state."""
    proc = subprocess.Popen(
        [str(BINARY_PATH), "9999"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    cmd = json.dumps({"type": "get_state", "id": "test-1"}) + "\n"
    stdout, stderr = proc.communicate(input=cmd, timeout=15)

    assert proc.returncode == 0, f"Binary exited with {proc.returncode}: {stderr}"

    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) > 0, "No response lines"

    # Find the get_state response (may be interleaved with startup messages)
    found = None
    for line in lines:
        try:
            obj = json.loads(line)
            if obj.get("type") == "response" and obj.get("command") == "get_state":
                found = obj
                break
        except json.JSONDecodeError:
            continue

    assert found is not None, f"No get_state response in {len(lines)} lines"
    assert found["success"] is True, f"get_state failed: {found.get('error')}"
    assert "model" in found["data"], f"No model data: {found}"


def test_binary_handles_abort():
    """Binary responds to abort without crashing."""
    proc = subprocess.Popen(
        [str(BINARY_PATH), "9999"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    cmd = json.dumps({"type": "abort"}) + "\n"
    stdout, stderr = proc.communicate(input=cmd, timeout=10)

    assert proc.returncode in (0, -15), f"Binary crashed: {stderr}"


def test_entry_has_embedded_tools():
    """The entry.ts has all 6 QGIS tools registered directly (no TypeBox)."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = open(entry_path).read()

    # Must not import TypeBox
    assert "typebox" not in content

    # Must have all 6 tools
    tools = ["run_qgis_code", "get_project_context", "run_processing",
             "add_layer", "confirm_action", "register_tool"]
    for tool in tools:
        assert f'name: "{tool}"' in content, f"Missing tool: {tool}"

    # Must have context injection
    assert 'aery.on("context"' in content

    # Must use plain JSON Schema (JS object notation)
    assert 'type: "object"' in content
