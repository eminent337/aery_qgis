"""QGIS Project Layer Metadata Extractor for LLM Context.

Extracts rich spatial metadata from all layers in the current QGIS project
and formats it into structured LLM prompt context blocks.
"""

import json
from datetime import datetime
from typing import Any, Optional

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRectangle,
    QgsFieldConstraints,
)


class FieldInspector:
    """Extracts field/attribute schema from a QgsVectorLayer."""

    TYPE_MAP = {
        2: "integer", 4: "integer", 6: "double", 8: "double",
        10: "string", 14: "date", 15: "time", 16: "datetime", 1: "boolean",
    }

    @classmethod
    def extract(cls, layer: QgsVectorLayer) -> list[dict]:
        fields = []
        for field in layer.fields():
            fields.append({
                "name": field.name(),
                "type": cls.TYPE_MAP.get(field.type(), f"type_{field.type()}"),
                "length": field.length(),
                "nullable": not bool(
                    field.constraints().constraints()
                    & QgsFieldConstraints.ConstraintNotNull
                ),
                "alias": field.alias() or None,
                "comment": field.comment() or None,
            })
        return fields

    @classmethod
    def summarise(cls, layer: QgsVectorLayer) -> str:
        fields = cls.extract(layer)
        parts = [f"{f['name']} ({f['type']})" for f in fields[:12]]
        suffix = f" … +{len(fields)-12} more" if len(fields) > 12 else ""
        return ", ".join(parts) + suffix


class ExtentHelper:
    WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

    @classmethod
    def to_wgs84(cls, extent: QgsRectangle, source_crs: QgsCoordinateReferenceSystem) -> dict:
        try:
            if source_crs == cls.WGS84 or not source_crs.isValid():
                rect = extent
            else:
                xform = QgsCoordinateTransform(
                    source_crs, cls.WGS84, QgsProject.instance()
                )
                rect = xform.transformBoundingBox(extent)
            return {
                "xmin": round(rect.xMinimum(), 6),
                "ymin": round(rect.yMinimum(), 6),
                "xmax": round(rect.xMaximum(), 6),
                "ymax": round(rect.yMaximum(), 6),
            }
        except Exception:
            return {"error": "Could not reproject extent to WGS84"}

    @classmethod
    def native(cls, extent: QgsRectangle) -> dict:
        return {
            "xmin": round(extent.xMinimum(), 4),
            "ymin": round(extent.yMinimum(), 4),
            "xmax": round(extent.xMaximum(), 4),
            "ymax": round(extent.yMaximum(), 4),
        }


class VectorLayerMeta:
    """Extracts all relevant metadata from a QgsVectorLayer."""

    GEOM_TYPE_MAP = {
        QgsWkbTypes.PointGeometry: "Point",
        QgsWkbTypes.LineGeometry: "LineString",
        QgsWkbTypes.PolygonGeometry: "Polygon",
        QgsWkbTypes.NullGeometry: "No geometry (table)",
        QgsWkbTypes.UnknownGeometry: "Unknown",
    }

    @classmethod
    def extract(cls, layer: QgsVectorLayer) -> dict:
        crs = layer.crs()
        extent = layer.extent()
        geom_type = cls.GEOM_TYPE_MAP.get(layer.geometryType(), "Unknown")
        wkb = layer.wkbType()
        is_multi = QgsWkbTypes.isMultiType(wkb)
        has_z = QgsWkbTypes.hasZ(wkb)
        has_m = QgsWkbTypes.hasM(wkb)

        meta: dict[str, Any] = {
            "layer_type": "vector",
            "name": layer.name(),
            "id": layer.id(),
            "source": layer.source(),
            "provider": layer.providerType(),
            "geometry_type": geom_type,
            "is_multi": is_multi,
            "has_z": has_z,
            "has_m": has_m,
            "feature_count": layer.featureCount(),
            "crs": {
                "auth_id": crs.authid(),
                "name": crs.description(),
                "is_geographic": crs.isGeographic(),
                "units": str(crs.mapUnits()),
            },
            "extent_native": ExtentHelper.native(extent),
            "extent_wgs84": ExtentHelper.to_wgs84(extent, crs),
            "fields": FieldInspector.extract(layer),
            "field_count": layer.fields().count(),
            "is_editable": layer.isEditable(),
            "is_spatial_index": layer.hasSpatialIndex() == layer.SpatialIndexPresent,
            "encoding": layer.dataProvider().encoding() if layer.dataProvider() else None,
            "renderer_type": layer.renderer().type() if layer.renderer() else None,
        }
        tree_lyr = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if tree_lyr:
            meta["visible"] = tree_lyr.isVisible()

        if layer.labelsEnabled():
            meta["labels_field"] = layer.customProperty("labeling/fieldName", None)

        selected = layer.selectedFeatureCount()
        if selected > 0:
            meta["selected_feature_count"] = selected

        return meta


class RasterLayerMeta:
    """Extracts all relevant metadata from a QgsRasterLayer."""

    @classmethod
    def extract(cls, layer: QgsRasterLayer) -> dict:
        crs = layer.crs()
        extent = layer.extent()
        dp = layer.dataProvider()

        meta: dict[str, Any] = {
            "layer_type": "raster",
            "name": layer.name(),
            "id": layer.id(),
            "source": layer.source(),
            "provider": layer.providerType(),
            "width_px": layer.width(),
            "height_px": layer.height(),
            "band_count": layer.bandCount(),
            "pixel_size_x": round(layer.rasterUnitsPerPixelX(), 6),
            "pixel_size_y": round(layer.rasterUnitsPerPixelY(), 6),
            "crs": {
                "auth_id": crs.authid(),
                "name": crs.description(),
                "is_geographic": crs.isGeographic(),
            },
            "extent_native": ExtentHelper.native(extent),
            "extent_wgs84": ExtentHelper.to_wgs84(extent, crs),
        }
        tree_lyr = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if tree_lyr:
            meta["visible"] = tree_lyr.isVisible()

        if dp:
            bands = []
            for b in range(1, layer.bandCount() + 1):
                try:
                    stats = dp.bandStatistics(b)
                    bands.append({
                        "band": b,
                        "name": layer.bandName(b),
                        "min": round(stats.minimumValue, 4),
                        "max": round(stats.maximumValue, 4),
                        "mean": round(stats.mean, 4),
                        "stddev": round(stats.stdDev, 4),
                        "data_type": str(dp.dataType(b)),
                    })
                except Exception:
                    bands.append({"band": b, "name": layer.bandName(b), "stats": "unavailable"})
            meta["bands"] = bands

        return meta


class QGISProjectContext:
    """Full metadata for all layers in the current QGIS project."""

    def __init__(self, include_invisible: bool = True):
        self.project = QgsProject.instance()
        self.include_invisible = include_invisible
        self.layers: list[dict] = []
        self.project_crs = self.project.crs()
        self._extract_all()

    def _extract_all(self):
        for layer in self.project.mapLayers().values():
            try:
                if isinstance(layer, QgsVectorLayer):
                    meta = VectorLayerMeta.extract(layer)
                elif isinstance(layer, QgsRasterLayer):
                    meta = RasterLayerMeta.extract(layer)
                else:
                    meta = {"layer_type": "other", "name": layer.name(), "id": layer.id()}
                if not self.include_invisible and not meta.get("visible", True):
                    continue
                self.layers.append(meta)
            except Exception as e:
                self.layers.append({
                    "layer_type": "error", "name": layer.name(), "error": str(e),
                })

    def to_json(self, indent: int = 2) -> str:
        return json.dumps({
            "project_file": self.project.fileName(),
            "project_crs": self.project_crs.authid(),
            "layer_count": len(self.layers),
            "extracted_at": datetime.now().isoformat(),
            "layers": self.layers,
        }, indent=indent, default=str)

    def to_llm_context(self, compact: bool = False) -> str:
        """Formats layer metadata as a clean text block for LLM prompts."""
        lines = [
            "=== QGIS ENVIRONMENT ===",
            f"Project CRS: {self.project_crs.authid()} — {self.project_crs.description()}",
            f"Total layers: {len(self.layers)}",
            "=== LOADED LAYERS ===",
        ]

        for i, layer in enumerate(self.layers, 1):
            lines.append(f"\n--- Layer {i}: {layer['name']} ---")
            lines.append(f"  Type:     {layer['layer_type'].upper()}")
            lines.append(f"  ID:       {layer['id']}")
            lines.append(f"  Source:   {layer.get('source', 'N/A')}")
            lines.append(f"  Provider: {layer.get('provider', 'N/A')}")
            lines.append(f"  Visible:  {layer.get('visible', True)}")

            if layer["layer_type"] == "vector":
                lines.append(f"  Geometry: {layer['geometry_type']}"
                             + (" (Multi)" if layer.get("is_multi") else "")
                             + (" Z" if layer.get("has_z") else ""))
                lines.append(f"  Features: {layer['feature_count']:,}")
                lines.append(f"  CRS:      {layer['crs']['auth_id']} — {layer['crs']['name']}")
                lines.append(f"  Units:    {'degrees' if layer['crs']['is_geographic'] else 'metres'}")
                bbox = layer.get("extent_wgs84", {})
                lines.append(f"  Extent (WGS84): [{bbox.get('xmin')}, {bbox.get('ymin')}, {bbox.get('xmax')}, {bbox.get('ymax')}]")

                if not compact:
                    lines.append(f"  Fields ({layer['field_count']}):")
                    for f in layer["fields"][:10]:
                        alias = f" alias='{f['alias']}'" if f["alias"] else ""
                        lines.append(f"    - {f['name']} ({f['type']}){alias}")
                    if layer["field_count"] > 10:
                        lines.append(f"    … and {layer['field_count'] - 10} more fields")
                else:
                    names = [f["name"] for f in layer["fields"][:8]]
                    suffix = f" +{layer['field_count']-8} more" if layer["field_count"] > 8 else ""
                    lines.append(f"  Fields: {', '.join(names)}{suffix}")

                if layer.get("is_editable"):
                    lines.append(f"  *** Layer is editable ***")
                if "selected_feature_count" in layer:
                    lines.append(f"  *** {layer['selected_feature_count']} features selected ***")
                if layer.get("labels_field"):
                    lines.append(f"  Labels: {layer['labels_field']}")

            elif layer["layer_type"] == "raster":
                lines.append(f"  Size:     {layer['width_px']} x {layer['height_px']} px")
                lines.append(f"  Bands:    {layer['band_count']}")
                lines.append(f"  Pixel:    {layer['pixel_size_x']} x {layer['pixel_size_y']} map units")
                lines.append(f"  CRS:      {layer['crs']['auth_id']} — {layer['crs']['name']}")
                bbox = layer.get("extent_wgs84", {})
                lines.append(f"  Extent (WGS84): [{bbox.get('xmin')}, {bbox.get('ymin')}, {bbox.get('xmax')}, {bbox.get('ymax')}]")
                for band in layer.get("bands", []):
                    if "min" in band:
                        lines.append(
                            f"  Band {band['band']} ({band['name']}): "
                            f"min={band['min']}, max={band['max']}, mean={band['mean']}"
                        )

        # CRS mismatch warning
        crs_ids = set()
        for layer in self.layers:
            c = layer.get("crs", {})
            if c.get("auth_id"):
                crs_ids.add(c["auth_id"])
        if len(crs_ids) > 1:
            lines.append("\n--- WARNING ---")
            lines.append(f"  Layers have {len(crs_ids)} different CRS: {', '.join(sorted(crs_ids))}")
            lines.append("  Reproject layers to a common CRS before spatial analysis.")

        return "\n".join(lines)
