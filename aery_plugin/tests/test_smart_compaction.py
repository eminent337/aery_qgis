"""Tests for the smart compaction summary helpers."""

from aery_plugin.agent import _extract_identifiers, _smart_tool_summary


def test_extract_identifiers_layer_names():
    content = 'Created layer "roads" with 1500 features and layer "buildings"'
    ids = _extract_identifiers(content)
    assert "roads" in ids["layer_names"]
    assert "buildings" in ids["layer_names"]


def test_extract_identifiers_paths():
    content = "Saved to /path/to/output.gpkg and /tmp/result.geojson"
    ids = _extract_identifiers(content)
    assert "/path/to/output.gpkg" in ids["paths"]
    assert "/tmp/result.geojson" in ids["paths"]


def test_extract_identifiers_metrics():
    content = "Processed 1500 features, area: 25.5 km, count: 100"
    ids = _extract_identifiers(content)
    assert any("1500 features" in m for m in ids["metrics"])
    assert any("25.5 km" in m for m in ids["metrics"])
    assert any("count: 100" in m for m in ids["metrics"])


def test_smart_tool_summary_preserves_identifiers():
    content = 'Created layer "roads" with 1500 features. Saved to /path/to/output.gpkg'
    summary = _smart_tool_summary("run_qgis_code", content)
    assert "roads" in summary  # layer name preserved
    assert "output.gpkg" in summary  # path preserved
    assert "1500 features" in summary  # metric preserved


def test_smart_tool_summary_empty_content():
    summary = _smart_tool_summary("run_qgis_code", "")
    assert "run_qgis_code" in summary
    assert "[Compacted]" in summary


def test_smart_tool_summary_respects_max_chars():
    content = "x" * 1000
    summary = _smart_tool_summary("tool", content, max_chars=100)
    assert len(summary) <= 110  # close to max with some slack for suffix
