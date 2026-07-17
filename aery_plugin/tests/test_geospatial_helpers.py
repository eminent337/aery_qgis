"""Unit tests for aery_plugin.geospatial_tools helpers."""

import os
import sys
import tempfile
import json
import unittest
from unittest.mock import MagicMock, patch

# Ensure the plugin package is importable when running pytest from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aery_plugin.geospatial_tools import (
    create_scratch_layer,
    resolve_layer,
    load_layer,
    get_active_layer,
    reproject_layer,
    set_layer_style_simple,
)


class TestCreateScratchLayer(unittest.TestCase):
    def setUp(self):
        from qgis.core import QgsProject
        self._project = QgsProject.instance()
        self._project.clear()

    def tearDown(self):
        self._project.clear()

    def test_default_fields_adds_name(self):
        lyr = create_scratch_layer("Point", "test_point")
        self.assertTrue(lyr.isValid())
        self.assertEqual(lyr.name(), "test_point")
        field_names = [f.name() for f in lyr.fields()]
        self.assertIn("name", field_names)
        self.assertEqual(lyr.fields().field(0).name(), "name")

    def test_custom_fields(self):
        lyr = create_scratch_layer(
            "Polygon",
            name="custom",
            fields=[("pop", "int"), ("area", "double"), ("label", "string")],
        )
        field_names = [f.name() for f in lyr.fields()]
        self.assertEqual(field_names, ["pop", "area", "label"])
        # Qt QVariant type IDs: Int=2, Double=6, String=10
        self.assertEqual(lyr.fields().field(0).type(), 2)
        self.assertEqual(lyr.fields().field(1).type(), 6)
        self.assertEqual(lyr.fields().field(2).type(), 10)

    def test_invalid_geometry_type_raises(self):
        with self.assertRaises(RuntimeError):
            create_scratch_layer("NotARealType", "bad")

    def test_added_to_project(self):
        lyr = create_scratch_layer("LineString", "added")
        self.assertIn(lyr, list(self._project.mapLayers().values()))


class TestResolveLayer(unittest.TestCase):
    def setUp(self):
        from qgis.core import QgsProject
        self._project = QgsProject.instance()
        self._project.clear()

    def tearDown(self):
        self._project.clear()

    def test_resolve_by_object(self):
        lyr = create_scratch_layer("Point", "direct")
        self.assertIs(resolve_layer(lyr), lyr)

    def test_resolve_by_id(self):
        lyr = create_scratch_layer("Point", "by_id")
        self.assertIs(resolve_layer(lyr.id()), lyr)

    def test_resolve_by_name(self):
        create_scratch_layer("Point", "find_me")
        resolved = resolve_layer("find_me")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name(), "find_me")

    def test_resolve_missing_returns_none(self):
        self.assertIsNone(resolve_layer("does_not_exist_xyz"))


class TestLoadLayer(unittest.TestCase):
    def test_load_geojson_from_string(self):
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [0, 0]},
                    "properties": {"name": "Origin"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "points.geojson")
            with open(path, "w") as f:
                json.dump(geojson, f)
            lyr = load_layer(path)
            self.assertIsNotNone(lyr)
            self.assertTrue(lyr.isValid())


class TestGetActiveLayer(unittest.TestCase):
    def test_returns_none_when_no_view(self):
        # No iface/mock view -> returns None
        with patch("aery_plugin.geospatial_tools.iface", None, create=True):
            self.assertIsNone(get_active_layer())


class TestReprojectLayer(unittest.TestCase):
    def test_vector_reproject(self):
        lyr = create_scratch_layer("Point", "reproject_me")
        mock_proc = MagicMock()
        mock_proc.run.return_value = {"OUTPUT": lyr}  # return valid layer to pass isValid()
        with patch.dict("sys.modules", {"processing": mock_proc}):
            out = reproject_layer(lyr, "EPSG:3857")
            self.assertIsNotNone(out)
            mock_proc.run.assert_called_once()
            args, kwargs = mock_proc.run.call_args
            self.assertEqual(args[0], "native:reprojectlayer")

    def test_raster_reproject(self):
        from qgis.core import QgsMapLayer
        fake_raster = MagicMock(spec=QgsMapLayer)
        # type 1 is Raster
        fake_raster.type.return_value = 1
        fake_raster.name.return_value = "fake_raster"
        
        mock_proc = MagicMock()
        mock_proc.run.return_value = {"OUTPUT": "/tmp/fake_reprojected.tif"}
        
        # Mock QgsRasterLayer to return a valid layer
        mock_raster_layer = MagicMock()
        mock_raster_layer.isValid.return_value = True
        
        from qgis.core import QgsProject
        with patch.dict("sys.modules", {"processing": mock_proc}):
            with patch("qgis.core.QgsRasterLayer", return_value=mock_raster_layer):
                with patch.object(QgsProject.instance(), "addMapLayer") as mock_add:
                    out = reproject_layer(fake_raster, "EPSG:3857")
                    self.assertIsNotNone(out)
                mock_proc.run.assert_called_once()
                args, kwargs = mock_proc.run.call_args
                self.assertEqual(args[0], "gdal:warpreproject")


class TestSetLayerStyleSimple(unittest.TestCase):
    def test_sets_vector_style(self):
        lyr = create_scratch_layer("Point", "styled")
        # Mock setRenderer, triggerRepaint to avoid crash on empty canvas/project context
        lyr.setRenderer = MagicMock()
        lyr.triggerRepaint = MagicMock()
        res = set_layer_style_simple(lyr, "single", color_or_ramp="#ff0000")
        self.assertTrue(res)
        lyr.setRenderer.assert_called_once()
        lyr.triggerRepaint.assert_called_once()
