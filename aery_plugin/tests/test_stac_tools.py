"""Unit tests for STAC search and COG layer loading tools."""

import json
from unittest.mock import patch, MagicMock
from aery_plugin.geospatial_tools import search_stac, load_cog_layer


def test_search_stac_mock_request():
    mock_payload = {
        "features": [
            {
                "id": "S2A_MSIL2A_20230501",
                "properties": {"datetime": "2023-05-01T10:00:00Z"},
                "bbox": [10.0, 50.0, 11.0, 51.0],
                "assets": {
                    "visual": {
                        "href": "https://example.com/visual.tif",
                        "title": "True color image",
                        "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                    }
                },
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = search_stac(collection="sentinel-2-l2a", bbox=[10.0, 50.0, 11.0, 51.0], max_items=1)
        assert res["count"] == 1
        assert res["items"][0]["id"] == "S2A_MSIL2A_20230501"
        assert "visual" in res["items"][0]["assets"]
        assert res["items"][0]["assets"]["visual"]["href"] == "https://example.com/visual.tif"


def test_search_stac_error_handling():
    with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
        res = search_stac(collection="sentinel-2-l2a")
        assert res["count"] == 0
        assert "error" in res
