"""Integration test: runner entry.ts tool definitions.

Tests the runner/entry.ts source code to verify tool definitions
are correct. The binary itself is no longer used — the plugin calls
LLM APIs directly via Python.
"""

import json
import os
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent / "aery_plugin"
RUNNER_DIR = PLUGIN_DIR.parent / "runner"


def test_entry_has_embedded_tools():
    """The entry.ts has QGIS tools registered directly, including direct Processing registry access."""
    entry_path = RUNNER_DIR / "entry.ts"
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
    entry_path = RUNNER_DIR / "entry.ts"
    content = open(entry_path).read()

    assert "QgsApplication.processingRegistry()" in content
    assert "algorithmById" in content
    assert "parameterDefinitions()" in content
    assert "provider().id()" in content


def test_runner_normalizes_processing_parameters_from_layer_names():
    """Processing execution should resolve layer names/ids and nested values before running."""
    entry_path = RUNNER_DIR / "entry.ts"
    content = entry_path.read_text()

    assert "def resolve_processing_value" in content
    assert "mapLayersByName" in content
    assert "parameterDefinitions()" in content
    assert "resolve_processing_value(value, definition=None)" in content


def test_runner_returns_structured_processing_results():
    """Processing execution should return structured metadata, not a raw processing dict only."""
    entry_path = RUNNER_DIR / "entry.ts"
    content = entry_path.read_text()

    assert '"success": True' in content
    assert '"algorithm": alg.id()' in content
    assert '"outputs": raw_result' in content
    assert '"output_summary"' in content


def test_runner_exposes_processing_runtime_validation_tool():
    """Runner should provide a live Processing runtime validation tool for real QGIS smoke checks."""
    entry_path = RUNNER_DIR / "entry.ts"
    content = entry_path.read_text()

    assert 'name: "validate_processing_runtime"' in content
    assert 'registry.providers()' in content
    assert 'algorithmById(alg_id)' in content
    assert '"native:buffer"' in content
    assert 'provider_count' in content
    assert 'sample_algorithms' in content


def test_gee_tool_embeds_valid_python():
    """The GEE wrapper must not leak JavaScript syntax into Python code."""
    entry_path = RUNNER_DIR / "entry.ts"
    content = open(entry_path).read()

    assert "getCloudCredentials()?.get" not in content



def test_runner_uses_qgsproject_instance_not_iface_project():
    """Embedded tool code should not use nonexistent iface.project()."""
    entry_path = RUNNER_DIR / "entry.ts"
    content = entry_path.read_text()

    assert "iface.project()" not in content
    assert "QgsProject.instance()" in content



def test_capture_canvas_avoids_direct_mapsettings_render():
    """Canvas capture should avoid direct render() calls that can destabilize QGIS."""
    entry_path = RUNNER_DIR / "entry.ts"
    content = entry_path.read_text()

    assert "settings.render(painter)" not in content



def test_add_layer_does_not_call_featurecount_on_raster():
    """Raster add-layer path should not assume featureCount() exists."""
    entry_path = RUNNER_DIR / "entry.ts"
    content = entry_path.read_text()

    assert 'Loaded {l.name()} ({l.featureCount()} features)' not in content



def test_capture_canvas_uses_aery_image_content_shape():
    """Canvas capture tool results must use Aery ImageContent shape, not Anthropic source blocks."""
    entry_path = RUNNER_DIR / "entry.ts"
    content = entry_path.read_text()

    assert '{ type: "image", data: b64, mimeType: "image/png" }' in content
    assert 'source: { type: "base64"' not in content
