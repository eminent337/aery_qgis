"""Tests for geospatial algorithms ported from GeoAI (smoothing, regularization, city bboxes)."""

from shapely.geometry import Polygon
from aery_plugin.geospatial_tools import (
    smooth_geometry,
    regularize_polygon,
    get_city_bbox,
    MAJOR_CITIES_BBOX,
)


def test_get_city_bbox_fast_lookup():
    assert get_city_bbox("Paris") == [2.2241, 48.8156, 2.4698, 48.9022]
    assert get_city_bbox("new york city") == [-74.0479, 40.6829, -73.9067, 40.8820]
    assert get_city_bbox("Accra") == [-0.3100, 5.5000, -0.1000, 5.6700]
    assert get_city_bbox("unknown_place_xyz") is None


def test_smooth_geometry():
    poly = Polygon([(0, 0), (0.1, 0.5), (0, 1), (1, 1), (1, 0), (0, 0)])
    smoothed = smooth_geometry(poly, simplify_tolerance=0.2)
    assert hasattr(smoothed, "area")
    assert smoothed.area > 0


def test_regularize_polygon_orthogonal():
    # Slightly distorted rectangle
    poly = Polygon([(0.1, 0.05), (10.05, 0.02), (9.98, 5.03), (0.02, 4.97), (0.1, 0.05)])
    reg = regularize_polygon(poly, simplify_tolerance=0.1, orthogonalize=True)
    assert hasattr(reg, "area")
    assert reg.area > 0
    # Regularized bounds should be approximately 10x5 = 50 area
    assert 40 <= reg.area <= 55
