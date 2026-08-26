"""Tests for PROJ-safe file writing helpers."""

import os
import threading
from unittest.mock import MagicMock
from aery_plugin.geospatial_tools import clean_proj_env, safe_to_file


def test_clean_proj_env_removes_and_restores():
    os.environ["PROJ_DATA"] = "/fake/proj/data"
    os.environ["PROJ_LIB"] = "/fake/proj/lib"
    try:
        with clean_proj_env():
            assert "PROJ_DATA" not in os.environ
            assert "PROJ_LIB" not in os.environ
        assert os.environ.get("PROJ_DATA") == "/fake/proj/data"
        assert os.environ.get("PROJ_LIB") == "/fake/proj/lib"
    finally:
        os.environ.pop("PROJ_DATA", None)
        os.environ.pop("PROJ_LIB", None)


def test_safe_to_file_calls_gdf_to_file():
    mock_gdf = MagicMock()
    safe_to_file(mock_gdf, "/tmp/test.gpkg", driver="GPKG")
    mock_gdf.to_file.assert_called_once_with("/tmp/test.gpkg", driver="GPKG")
