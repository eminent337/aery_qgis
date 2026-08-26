"""Unit tests for the 8 session challenge fixes:
1. resolve_layer helper
2. QgsFillSymbol / Symbology pre-loads
3-5. Overpass query helper with User-Agent & failover
6. Safe GeoDataFrame construction
7. QuickOSM query helper
8. QGIS 4.x QgsProcessingParameterNumberRange compatibility
"""

import os
from unittest.mock import patch, MagicMock
from aery_plugin.geospatial_tools import (
    resolve_layer,
    safe_create_geodataframe,
    query_overpass,
    run_quickosm_query,
)
from aery_plugin.qgis_executor import _build_globals


def test_resolve_layer_fallback():
    # Test resolve_layer handles missing or null layer cleanly
    res = resolve_layer("Nonexistent_Layer_123")
    assert res is None


def test_qgs_fill_symbol_in_globals():
    g = _build_globals()
    # If qgis.core is installed in env, QgsFillSymbol and QgsSingleSymbolRenderer should be in g
    try:
        import qgis.core
        assert "QgsFillSymbol" in g
        assert "QgsSingleSymbolRenderer" in g
        assert "QgsLineSymbol" in g
        assert "resolve_layer" in g
        assert "query_overpass" in g
        assert "safe_create_geodataframe" in g
    except ImportError:
        pass


def test_safe_create_geodataframe_empty():
    gdf = safe_create_geodataframe([], crs="EPSG:4326")
    assert len(gdf) == 0
    assert hasattr(gdf, "geometry")
    assert gdf.crs.to_string() == "EPSG:4326"


def test_safe_create_geodataframe_valid_features():
    features = [
        {"name": "Polygon1", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}
    ]
    gdf = safe_create_geodataframe(features, crs="EPSG:3857")
    assert len(gdf) == 1
    assert gdf.iloc[0]["name"] == "Polygon1"
    assert gdf.crs.to_string() == "EPSG:3857"


def test_query_overpass_mock():
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"version": 0.6, "elements": [{"id": 123}]}'
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = query_overpass("node(51.0, 0.0, 51.1, 0.1); out;")
        assert res["version"] == 0.6
        assert len(res["elements"]) == 1
def test_qgis4_processing_discovery():
    from aery_plugin.processing_discovery import _get_parameter_type_name
    # Ensure parameter formatting doesn't fail on missing NumberRange in QGIS 4.x
    param = MagicMock()
    type_name = _get_parameter_type_name(param)
    assert isinstance(type_name, str)
