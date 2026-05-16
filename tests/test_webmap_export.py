"""Tests for export_webmap tool (qgis2web / Leaflet export)."""

import pathlib
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY_TS = ROOT / "runner" / "entry.ts"


def test_export_webmap_registers_tool():
    """export_webmap must appear in runner/entry.ts tool registrations."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    assert 'name: "export_webmap"' in src, (
        "export_webmap tool registration not found in runner/entry.ts. "
        'Expected: aery.registerTool({ name: "export_webmap", ... })'
    )


def test_export_webmap_has_required_parameters():
    """export_webmap must accept output_dir, basemap, format, title."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    # Find the export_webmap tool block (from name: to next });
    idx = src.index('name: "export_webmap"')
    block = src[idx:idx + 4000]
    assert 'output_dir' in block, "Missing output_dir parameter"
    assert 'basemap' in block, "Missing basemap parameter"
    assert 'leaflet' in block, "Missing leaflet format option"
    assert 'title' in block, "Missing title parameter"


def test_export_webmap_builds_leaflet_html(tmp_path):
    """_build_leaflet_html produces non-empty HTML with Leaflet tags."""
    from aery_plugin.qgis_executor import _build_leaflet_html

    layer_files = [
        {"name": "roads", "file": "data/roads.geojson", "count": 42},
        {"name": "buildings", "file": "data/buildings.geojson", "count": 100},
    ]
    html = _build_leaflet_html(layer_files, basemap="osm", title="Test Map")
    assert "<!DOCTYPE html>" in html
    assert "leaflet" in html.lower()
    assert 'id="map"' in html
    assert "roads.geojson" in html
    assert "buildings.geojson" in html


def test_export_webmap_has_search_box_option():
    """include_search=True adds nominatim geocoding UI."""
    from aery_plugin.qgis_executor import _build_leaflet_html

    html_off = _build_leaflet_html([], include_search=False)
    html_on = _build_leaflet_html([], include_search=True)
    assert "doSearch" not in html_off
    assert "doSearch" in html_on
    assert "nominatim" in html_on


def test_export_webmap_basemap_all_options():
    """All basemap options produce a tile layer URL."""
    from aery_plugin.qgis_executor import _build_leaflet_html

    for bm in ("osm", "satellite", "topo", "stamen_toner", "none"):
        html = _build_leaflet_html([], basemap=bm)
        assert "<!DOCTYPE html>" in html, f"basemap={bm} produced invalid HTML"
        if bm != "none":
            assert "L.tileLayer" in html, f"basemap={bm} missing tile layer"
