"""Geospatial analysis tools for the Aery QGIS agent.

Each tool entry is a dict with name, description, parameters (JSON Schema),
and code (a Python string with {param_name} placeholders replaced at runtime).
"""

TOOLS = [
    {
        "name": "buffer_analysis",
        "description": (
            "Buffer features by a given distance with optional dissolve. "
            "Use for proximity analysis, radius queries, or zone creation. "
            "distance is in layer CRS units. dissolve=True merges overlapping buffers. "
            "Always call capture_canvas after to verify."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                    "description": "Name of the layer to buffer",
                },
                "distance": {
                    "type": "number",
                    "description": "Buffer distance in layer CRS units",
                },
                "dissolve": {
                    "type": "boolean",
                    "description": "Merge overlapping buffers into single features",
                },
            },
            "required": ["layer_name", "distance", "dissolve"],
        },
        "code": """import processing
from qgis.core import QgsProject
layer = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {layer_name}), None)
if layer is None:
    raise ValueError("Layer not found: " + str({layer_name}))
params = {
    "INPUT": layer,
    "DISTANCE": {distance},
    "SEGMENTS": 10,
    "DISSOLVE": {dissolve},
    "OUTPUT": "memory:",
}
res = processing.run("native:buffer", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Buffer output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
result = {"output_layer": out.name(), "features": out.featureCount(), "distance": {distance}, "dissolve": {dissolve}}""",
    },
    {
        "name": "clip_analysis",
        "description": (
            "Clip a vector layer to the boundary of a polygon overlay layer. "
            "Use to extract features within a study area or region of interest."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "input_layer": {
                    "type": "string",
                    "description": "Name of the layer to clip",
                },
                "overlay_layer": {
                    "type": "string",
                    "description": "Name of the polygon boundary layer",
                },
            },
            "required": ["input_layer", "overlay_layer"],
        },
        "code": """import processing
from qgis.core import QgsProject
input_lyr = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {input_layer}), None)
overlay_lyr = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {overlay_layer}), None)
if input_lyr is None:
    raise ValueError("Input layer not found: " + str({input_layer}))
if overlay_lyr is None:
    raise ValueError("Overlay layer not found: " + str({overlay_layer}))
params = {"INPUT": input_lyr, "OVERLAY": overlay_lyr, "OUTPUT": "memory:"}
res = processing.run("native:clip", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Clip output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
result = {"output_layer": out.name(), "features": out.featureCount(), "input": {input_layer}, "overlay": {overlay_layer}}""",
    },
    {
        "name": "intersect_analysis",
        "description": (
            "Compute the geometric intersection of two vector layers, "
            "preserving only features that overlap spatially. "
            "Both layers must have compatible CRS — reproject first if needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "input_layer": {
                    "type": "string",
                    "description": "Name of the first layer",
                },
                "overlay_layer": {
                    "type": "string",
                    "description": "Name of the second layer",
                },
            },
            "required": ["input_layer", "overlay_layer"],
        },
        "code": """import processing
from qgis.core import QgsProject
input_lyr = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {input_layer}), None)
overlay_lyr = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {overlay_layer}), None)
if input_lyr is None:
    raise ValueError("Input layer not found: " + str({input_layer}))
if overlay_lyr is None:
    raise ValueError("Overlay layer not found: " + str({overlay_layer}))
params = {"INPUT": input_lyr, "OVERLAY": overlay_lyr, "OUTPUT": "memory:"}
res = processing.run("native:intersection", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Intersection output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
result = {"output_layer": out.name(), "features": out.featureCount(), "input": {input_layer}, "overlay": {overlay_layer}}""",
    },
    {
        "name": "union_analysis",
        "description": (
            "Merge two vector layers into one, preserving all features and "
            "attributes from both inputs. Use for combining complementary datasets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "input_layer": {
                    "type": "string",
                    "description": "Name of the first layer",
                },
                "overlay_layer": {
                    "type": "string",
                    "description": "Name of the second layer",
                },
            },
            "required": ["input_layer", "overlay_layer"],
        },
        "code": """import processing
from qgis.core import QgsProject
input_lyr = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {input_layer}), None)
overlay_lyr = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {overlay_layer}), None)
if input_lyr is None:
    raise ValueError("Input layer not found: " + str({input_layer}))
if overlay_lyr is None:
    raise ValueError("Overlay layer not found: " + str({overlay_layer}))
params = {"INPUT": input_lyr, "OVERLAY": overlay_lyr, "OUTPUT": "memory:"}
res = processing.run("native:union", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Union output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
result = {"output_layer": out.name(), "features": out.featureCount(), "input": {input_layer}, "overlay": {overlay_layer}}""",
    },
    {
        "name": "dissolve_analysis",
        "description": (
            "Dissolve features by an optional attribute field, merging geometries "
            "that share the same field value. Use to aggregate and simplify data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                    "description": "Name of the layer to dissolve",
                },
                "field": {
                    "type": "string",
                    "description": "Attribute field to dissolve by (optional)",
                },
            },
            "required": ["layer_name"],
        },
        "code": """import processing
from qgis.core import QgsProject
layer = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {layer_name}), None)
if layer is None:
    raise ValueError("Layer not found: " + str({layer_name}))
params = {"INPUT": layer, "OUTPUT": "memory:"}
try:
    dissolve_field = {field}
    if dissolve_field:
        params["FIELD"] = [dissolve_field]
except (NameError, KeyError):
    pass
res = processing.run("native:dissolve", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Dissolve output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
out_features = out.featureCount()
field_used = params.get("FIELD", None)
result = {"output_layer": out.name(), "features": out_features, "field": field_used[0] if field_used else None}""",
    },
    {
        "name": "spatial_join",
        "description": (
            "Join attributes from one layer to another based on a spatial "
            "relationship (intersects, within, contains, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_layer": {
                    "type": "string",
                    "description": "Layer to receive the joined attributes",
                },
                "join_layer": {
                    "type": "string",
                    "description": "Layer whose attributes will be joined",
                },
                "predicate": {
                    "type": "string",
                    "enum": ["intersects", "within", "contains", "equals", "touches", "overlaps", "crosses"],
                    "description": "Spatial predicate for the join",
                },
            },
            "required": ["target_layer", "join_layer", "predicate"],
        },
        "code": """import processing
from qgis.core import QgsProject
target = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {target_layer}), None)
join = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {join_layer}), None)
if target is None:
    raise ValueError("Target layer not found: " + str({target_layer}))
if join is None:
    raise ValueError("Join layer not found: " + str({join_layer}))
predicate_map = {"intersects": 0, "within": 1, "contains": 2, "equals": 3, "touches": 4, "overlaps": 5, "crosses": 6}
params = {
    "INPUT": target,
    "JOIN": join,
    "PREDICATE": predicate_map.get({predicate}, 0),
    "METHOD": 0,
    "OUTPUT": "memory:",
}
res = processing.run("native:joinattributesbylocation", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Spatial join output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
result = {"output_layer": out.name(), "features": out.featureCount(), "predicate": {predicate}, "joined_fields": len(out.fields())}""",
    },
    {
        "name": "zonal_statistics",
        "description": (
            "Compute raster statistics (mean, sum, min, max, etc.) within each "
            "polygon zone. Use to summarise raster values by area."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "zones_layer": {
                    "type": "string",
                    "description": "Name of the polygon zones layer",
                },
                "raster_layer": {
                    "type": "string",
                    "description": "Name of the raster layer",
                },
                "band": {
                    "type": "integer",
                    "description": "Raster band number (1-based, default: 1)",
                },
            },
            "required": ["zones_layer", "raster_layer", "band"],
        },
        "code": """import processing
from qgis.core import QgsProject
zones = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {zones_layer}), None)
raster = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {raster_layer}), None)
if zones is None:
    raise ValueError("Zones layer not found: " + str({zones_layer}))
if raster is None:
    raise ValueError("Raster layer not found: " + str({raster_layer}))
params = {
    "INPUT_RASTER": raster,
    "RASTER_BAND": {band},
    "INPUT_VECTOR": zones,
    "COLUMN_PREFIX": "z_",
    "STATISTICS": [0, 1, 2, 3, 4, 5, 6],
    "OUTPUT": "memory:",
}
res = processing.run("native:zonalstatisticsfb", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Zonal statistics output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
result = {"output_layer": out.name(), "features": out.featureCount(), "band": {band}, "prefix": "z_"}""",
    },
    {
        "name": "proximity_analysis",
        "description": (
            "Find features from one layer that are within a given distance of "
            "features in another layer. Uses buffering and location extraction."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_layer": {
                    "type": "string",
                    "description": "Layer to select features from",
                },
                "reference_layer": {
                    "type": "string",
                    "description": "Layer to measure distance from",
                },
                "distance": {
                    "type": "number",
                    "description": "Search distance in CRS units",
                },
            },
            "required": ["source_layer", "reference_layer", "distance"],
        },
        "code": """import processing
from qgis.core import QgsProject
source = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {source_layer}), None)
ref = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {reference_layer}), None)
if source is None:
    raise ValueError("Source layer not found: " + str({source_layer}))
if ref is None:
    raise ValueError("Reference layer not found: " + str({reference_layer}))
buf_params = {
    "INPUT": ref,
    "DISTANCE": {distance},
    "SEGMENTS": 10,
    "DISSOLVE": True,
    "OUTPUT": "memory:",
}
buf = processing.run("native:buffer", buf_params)["OUTPUT"]
extract_params = {
    "INPUT": source,
    "PREDICATE": [5],
    "INTERSECT": buf,
    "OUTPUT": "memory:",
}
res = processing.run("native:extractbylocation", extract_params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Proximity output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
result = {"output_layer": out.name(), "features_within_distance": out.featureCount(), "distance": {distance}}""",
    },
    {
        "name": "network_analysis",
        "description": (
            "Compute the shortest path between two points on a road or path "
            "network. Start and end points are given as comma-separated coordinates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "network_layer": {
                    "type": "string",
                    "description": "Name of the line network layer",
                },
                "start_point": {
                    "type": "string",
                    "description": "Start point as 'x,y' coordinates in layer CRS",
                },
                "end_point": {
                    "type": "string",
                    "description": "End point as 'x,y' coordinates in layer CRS",
                },
            },
            "required": ["network_layer", "start_point", "end_point"],
        },
        "code": """import processing
from qgis.core import QgsProject, QgsPointXY
network = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {network_layer}), None)
if network is None:
    raise ValueError("Network layer not found: " + str({network_layer}))
start_parts = {start_point}.split(",")
end_parts = {end_point}.split(",")
if len(start_parts) < 2 or len(end_parts) < 2:
    raise ValueError("Coordinates must be in 'x,y' format")
start_pt = QgsPointXY(float(start_parts[0].strip()), float(start_parts[1].strip()))
end_pt = QgsPointXY(float(end_parts[0].strip()), float(end_parts[1].strip()))
params = {
    "INPUT": network,
    "STRATEGY": 0,
    "DIRECTION": 2,
    "START_POINT": start_pt,
    "END_POINT": end_pt,
    "OUTPUT": "memory:",
}
res = processing.run("native:shortestpathpointtopoint", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Shortest path output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
total_length = sum(f.geometry().length() for f in out.getFeatures()) if out.featureCount() else 0
result = {"output_layer": out.name(), "features": out.featureCount(), "total_length": round(total_length, 4), "start": {start_point}, "end": {end_point}}""",
    },
    {
        "name": "voronoi_diagram",
        "description": (
            "Generate Voronoi polygon diagrams from a point layer. "
            "Each polygon encloses the area closest to its source point."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                    "description": "Name of the point layer",
                },
            },
            "required": ["layer_name"],
        },
        "code": """import processing
from qgis.core import QgsProject
layer = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {layer_name}), None)
if layer is None:
    raise ValueError("Layer not found: " + str({layer_name}))
params = {
    "INPUT": layer,
    "BUFFER": 0,
    "OUTPUT": "memory:",
}
res = processing.run("qgis:voronoipolygons", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Voronoi output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
result = {"output_layer": out.name(), "features": out.featureCount(), "source_layer": {layer_name}}""",
    },
    {
        "name": "density_analysis",
        "description": (
            "Generate a kernel density (heatmap) raster from a point layer. "
            "Use to visualise point concentration and spatial intensity patterns."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                    "description": "Name of the point layer",
                },
                "radius": {
                    "type": "number",
                    "description": "Kernel radius in CRS units",
                },
            },
            "required": ["layer_name", "radius"],
        },
        "code": """import processing
from qgis.core import QgsProject
layer = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {layer_name}), None)
if layer is None:
    raise ValueError("Layer not found: " + str({layer_name}))
ext = layer.extent()
pixel_size = {radius} / 20
params = {
    "INPUT": layer,
    "RADIUS": {radius},
    "PIXEL_SIZE": pixel_size,
    "OUTPUT": "memory:",
}
res = processing.run("native:heatmapkerneldensityestimation", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Density output raster is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
result = {"output_layer": out.name(), "radius": {radius}, "pixel_size": round(pixel_size, 4), "source_layer": {layer_name}}""",
    },
    {
        "name": "hotspot_analysis",
        "description": (
            "Run Getis-Ord Gi* spatial autocorrelation on a field to identify "
            "statistically significant hot and cold spots in the data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                    "description": "Name of the vector layer",
                },
                "field": {
                    "type": "string",
                    "description": "Numeric attribute field to analyse",
                },
            },
            "required": ["layer_name", "field"],
        },
        "code": """import processing
from qgis.core import QgsProject
layer = next((l for l in QgsProject.instance().mapLayers().values() if l.name() == {layer_name}), None)
if layer is None:
    raise ValueError("Layer not found: " + str({layer_name}))
params = {
    "INPUT": layer,
    "FIELD": {field},
    "OUTPUT": "memory:",
}
res = processing.run("native:getisordgistar", params)
out = res["OUTPUT"]
if not out.isValid():
    raise RuntimeError("Hotspot output layer is not valid")
QgsProject.instance().addMapLayer(out)
iface.mapCanvas().refresh()
result = {"output_layer": out.name(), "features": out.featureCount(), "field_analysed": {field}, "source_layer": {layer_name}}""",
    },
]
