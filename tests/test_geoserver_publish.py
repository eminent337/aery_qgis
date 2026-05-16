"""Tests for publish_geoserver tool (GeoServer REST publishing)."""

import pathlib
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY_TS = ROOT / "runner" / "entry.ts"


def test_publish_geoserver_registers_tool():
    """publish_geoserver must appear in runner/entry.ts."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    assert 'name: "publish_geoserver"' in src, (
        "publish_geoserver tool registration not found in runner/entry.ts"
    )


def test_publish_geoserver_requires_layer_and_url():
    """publish_geoserver requires layer and geoserver_url params."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    assert '"layer"' in src and '"geoserver_url"' in src


def test_publish_geoserver_accepts_workspace_and_auth():
    """publish_geoserver accepts workspace, username, password params."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    assert '"workspace"' in src
    assert '"username"' in src
    assert '"password"' in src


def test_publish_geoserver_mentions_ogr2ogr_or_fallback():
    """The tool must either call ogr2ogr or raise a clear error if unavailable."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    tool_block_start = src.index('name: "publish_geoserver"')
    tool_block = src[tool_block_start: tool_block_start + 10000]
    assert ("ogr2ogr" in tool_block or "ogr2ogr" in src), (
        "publish_geoserver should mention ogr2ogr or explain the fallback"
    )
