"""Unit tests for the QuickOSM/Overpass native-first fix.

Verifies that ``run_quickosm_query`` no longer depends on QuickOSM's fragile
processing algorithms or the erroneous ``QgsProject.processingRegistry()`` API,
and that the Overpass query builder / GeoJSON conversion are correct.
"""

from unittest.mock import patch, MagicMock

from aery_plugin.geospatial_tools import (
    _normalize_extent,
    _overpass_to_features,
    build_overpass_query,
    run_quickosm_query,
)


# --- extent normalization ---------------------------------------------------

def test_normalize_extent_tuple_order():
    # (xmin, ymin, xmax, ymax) -> (south, west, north, east)
    assert _normalize_extent((10.0, 20.0, 30.0, 40.0)) == (20.0, 10.0, 40.0, 30.0)


def test_normalize_extent_string():
    assert _normalize_extent("10, 20, 30, 40") == (20.0, 10.0, 40.0, 30.0)


def test_normalize_extent_none():
    assert _normalize_extent(None) is None


def test_normalize_extent_qgs_rectangle():
    try:
        from qgis.core import QgsRectangle
    except ImportError:
        return  # qgis unavailable in this env; skip
    rect = QgsRectangle(10.0, 20.0, 30.0, 40.0)  # xmin,ymin,xmax,ymax
    assert _normalize_extent(rect) == (20.0, 10.0, 40.0, 30.0)


# --- query builder ---------------------------------------------------------

def test_build_overpass_query_key_value_extent():
    q = build_overpass_query(key="amenity", value="cafe", extent=(-0.1, 51.4, 0.2, 51.6))
    assert "[\"amenity\"=\"cafe\"]" in q
    # bbox: south, west, north, east = 51.4, -0.1, 51.6, 0.2
    assert "(51.4;-0.1;51.6;0.2)" in q
    for t in ("node", "way", "relation"):
        assert t in q
    assert "out geom;" in q


def test_build_overpass_query_key_only():
    q = build_overpass_query(key="amenity", extent=(-1, 50, 1, 52))
    assert "[\"amenity\"]" in q


def test_build_overpass_query_raw_query_passthrough():
    raw = "  [out:json];node[aerialway=station](50,5,52,9);out;  "
    q = build_overpass_query(raw_query=raw)
    assert q == raw.strip()
    assert "[out:json]" in q


# --- feature conversion ----------------------------------------------------

def test_overpass_to_features_node():
    els = [{"type": "node", "id": 1, "lat": 51.5, "lon": -0.1, "tags": {"amenity": "cafe"}}]
    feats = _overpass_to_features(els)
    assert len(feats) == 1
    f = feats[0]
    assert f["geometry"]["type"] == "Point"
    assert f["geometry"]["coordinates"] == [-0.1, 51.5]
    assert f["properties"]["amenity"] == "cafe"
    assert f["properties"]["osm_id"] == 1


def test_to_overpass_features_empty():
    assert _overpass_to_features([]) == []


# --- run_quickosm_query ----------------------------------------------------

def test_run_quickosm_query_success():
    payload = {"version": 0.6, "elements": [
        {"type": "node", "id": 5, "lat": 10.0, "lon": 20.0, "tags": {"name": "X"}}
    ]}
    with patch("aery_plugin.geospatial_tools.query_overpass", return_value=payload):
        res = run_quickosm_query(key="amenity", value="cafe", extent=(-1, -1, 1, 1))
    assert res["success"] is True
    assert res["count"] == 1
    assert res["features"][0]["properties"]["osm_id"] == 5
    assert res["features"][0]["geometry"]["type"] == "Point"
    assert res["layer_name"] == "OSM Query"


def test_run_quickosm_query_raw_mode():
    payload = {"version": 0.6, "elements": []}
    with patch("aery_plugin.geospatial_tools.query_overpass", return_value=payload) as m:
        res = run_quickosm_query(raw_query="[out:json];node(10,10,11,11);out;")
    assert res["success"] is True
    assert res["count"] == 0
    assert "node(10,10,11,11)" in m.call_args[0][0]


def test_run_quickosm_query_error_returns_success_false():
    with patch("aery_plugin.geospatial_tools.query_overpass", side_effect=RuntimeError("boom")):
        res = run_quickosm_query(key="amenity")
    assert res["success"] is False
    assert "boom" in res["error"]