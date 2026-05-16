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
    """The entry.ts has QGIS tools registered directly, including direct Processing registry access."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = open(entry_path).read()

    # Must not import TypeBox
    assert "typebox" not in content

    tools = [
        "run_qgis_code", "get_project_context", "run_processing",
        "add_layer", "confirm_action", "register_tool",
        "validate_project", "get_audit_trail",
        "list_processing_algorithms", "describe_processing_algorithm", "run_processing_algorithm",
        "validate_processing_runtime",
    ]
    for tool in tools:
        assert f'name: "{tool}"' in content, f"Missing tool: {tool}"

    # Must have context injection
    assert 'aery.on("context"' in content

    # Must use plain JSON Schema (JS object notation)
    assert 'type: "object"' in content


def test_runner_exposes_qgis_processing_registry_access():
    """Runner should query QGIS's own Processing registry, not just hardcoded wrappers."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = open(entry_path).read()

    assert "QgsApplication.processingRegistry()" in content
    assert "algorithmById" in content
    assert "parameterDefinitions()" in content
    assert "provider().id()" in content


def test_runner_normalizes_processing_parameters_from_layer_names():
    """Processing execution should resolve layer names/ids and nested values before running."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = entry_path.read_text()

    assert "def resolve_processing_value" in content
    assert "mapLayersByName" in content
    assert "parameterDefinitions()" in content
    assert "resolve_processing_value(value, definition=None)" in content


def test_runner_returns_structured_processing_results():
    """Processing execution should return structured metadata, not a raw processing dict only."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = entry_path.read_text()

    assert '"success": True' in content
    assert '"algorithm": alg.id()' in content
    assert '"outputs": raw_result' in content
    assert '"output_summary"' in content


def test_runner_exposes_processing_runtime_validation_tool():
    """Runner should provide a live Processing runtime validation tool for real QGIS smoke checks."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = entry_path.read_text()

    assert 'name: "validate_processing_runtime"' in content
    assert 'registry.providers()' in content
    assert 'algorithmById(alg_id)' in content
    assert '"native:buffer"' in content
    assert 'provider_count' in content
    assert 'sample_algorithms' in content


def test_gee_tool_embeds_valid_python():
    """The GEE wrapper must not leak JavaScript syntax into Python code."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = open(entry_path).read()

    assert "getCloudCredentials()?.get" not in content



def test_runner_uses_qgsproject_instance_not_iface_project():
    """Embedded tool code should not use nonexistent iface.project()."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = entry_path.read_text()

    assert "iface.project()" not in content
    assert "QgsProject.instance()" in content



def test_capture_canvas_avoids_direct_mapsettings_render():
    """Canvas capture should avoid direct render() calls that can destabilize QGIS."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = entry_path.read_text()

    assert "settings.render(painter)" not in content



def test_add_layer_does_not_call_featurecount_on_raster():
    """Raster add-layer path should not assume featureCount() exists."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = entry_path.read_text()

    assert 'Loaded {l.name()} ({l.featureCount()} features)' not in content



def test_capture_canvas_uses_aery_image_content_shape():
    """Canvas capture tool results must use Aery ImageContent shape, not Anthropic source blocks."""
    entry_path = PLUGIN_DIR.parent / "runner" / "entry.ts"
    content = entry_path.read_text()

    assert '{ type: "image", data: b64, mimeType: "image/png" }' in content
    assert 'source: { type: "base64"' not in content
