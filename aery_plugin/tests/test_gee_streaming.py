"""Unit tests for GEE live tile streaming and GeoLibre Earth Engine OAuth config."""

from aery_plugin.geospatial_tools import get_gee_tile_url
from aery_plugin.core.ai.auth import get_earth_engine_config, DEFAULT_GEE_OAUTH_CLIENT_ID


def test_get_gee_tile_url_from_dict():
    map_dict = {
        "tile_fetcher": {
            "url_format": "https://earthengine.googleapis.com/v1/projects/my-proj/maps/abc/tiles/{z}/{x}/{y}"
        }
    }
    url = get_gee_tile_url(map_dict)
    assert url == "https://earthengine.googleapis.com/v1/projects/my-proj/maps/abc/tiles/{z}/{x}/{y}"


def test_get_gee_tile_url_from_mapid_token():
    map_dict = {
        "mapid": "projects/earthengine-legacy/maps/testmap123",
        "token": "token456",
    }
    url = get_gee_tile_url(map_dict)
    assert "https://earthengine.googleapis.com/v1/projects/earthengine-legacy/maps/testmap123/tiles/{z}/{x}/{y}?token=token456" == url


def test_earth_engine_oauth_config_default():
    cfg = get_earth_engine_config()
    assert "client_id" in cfg
    assert cfg["client_id"] == DEFAULT_GEE_OAUTH_CLIENT_ID
