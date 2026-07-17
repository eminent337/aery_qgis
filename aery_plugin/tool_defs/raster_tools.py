TOOLS = [
    {
        "name": "raster_calculator",
        "description": "NDVI/NDWI/band math on raster layers",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Raster calculator expression"},
                "layers": {"type": "array", "items": {"type": "string"}, "description": "Input raster layer names"},
                "output_name": {"type": "string", "description": "Output raster name"}
            },
            "required": ["expression", "layers", "output_name"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterCalculator, QgsRasterCalculatorEntry, QgsRasterLayer
import processing

layers = {layers}
output_name = {output_name}

entries = []
for i, name in enumerate(layers):
    rl = QgsProject.instance().mapLayersByName(name)
    if not rl:
        raise RuntimeError(f"Raster layer '{name}' not found")
    rl = rl[0]
    if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
        raise RuntimeError(f"Invalid raster layer '{name}'")
    entry = QgsRasterCalculatorEntry()
    entry.ref = f'{name}@1'
    entry.raster = rl
    entry.bandNumber = 1
    entries.append(entry)

output_path = f'/tmp/{output_name}.tif'
calc = QgsRasterCalculator({expression}, output_path, 'GTiff', rl.extent(), rl.width(), rl.height(), entries)
result_code = calc.processCalculation()
if result_code != 0:
    raise RuntimeError(f"Raster calculation failed with code {result_code}")

result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load calculated raster")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Raster calculation completed: {output_name}"""
    },
    {
        "name": "terrain_analysis",
        "description": "Generate slope, aspect, or hillshade from a DEM",
        "parameters": {
            "type": "object",
            "properties": {
                "dem_layer": {"type": "string", "description": "DEM raster layer name"},
                "type": {"type": "string", "enum": ["slope", "aspect", "hillshade"], "description": "Terrain analysis type"}
            },
            "required": ["dem_layer", "type"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer
import processing

dem_layer = {dem_layer}
analysis_type = {type}

rl = QgsProject.instance().mapLayersByName(dem_layer)
if not rl:
    raise RuntimeError(f"DEM layer '{dem_layer}' not found")
rl = rl[0]
if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
    raise RuntimeError(f"Invalid DEM layer '{dem_layer}'")

output_name = f"{analysis_type}_{dem_layer}"
output_path = f'/tmp/{output_name}.tif'

alg_map = {{
    "slope": "native:slope",
    "aspect": "native:aspect",
    "hillshade": "native:hillshade"
}}
params = {{
    'INPUT': rl,
    'OUTPUT': output_path
}}
if analysis_type == "hillshade":
    params['AZIMUTH'] = 315.0
    params['ALTITUDE'] = 45.0

processing.run(alg_map[analysis_type], params)
result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError(f"Failed to load {analysis_type} layer")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"{analysis_type.capitalize()} generated from '{dem_layer}'"""
    },
    {
        "name": "contour_generation",
        "description": "Generate contour lines from a DEM",
        "parameters": {
            "type": "object",
            "properties": {
                "dem_layer": {"type": "string", "description": "DEM raster layer name"},
                "interval": {"type": "number", "description": "Contour interval in map units"}
            },
            "required": ["dem_layer", "interval"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer
import processing

dem_layer = {dem_layer}
interval = {interval}

rl = QgsProject.instance().mapLayersByName(dem_layer)
if not rl:
    raise RuntimeError(f"DEM layer '{dem_layer}' not found")
rl = rl[0]
if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
    raise RuntimeError(f"Invalid DEM layer '{dem_layer}'")

output_name = f"contours_{dem_layer}_{interval}m"
output_path = f'/tmp/{output_name}.gpkg'

params = {{
    'INPUT': rl,
    'INTERVAL': interval,
    'OUTPUT': output_path
}}
processing.run("native:contour", params)
result_layer = QgsVectorLayer(output_path, output_name, 'ogr')
if not result_layer.isValid():
    raise RuntimeError("Failed to load contour layer")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Contours generated at {interval} interval from '{dem_layer}'"""
    },
    {
        "name": "raster_classify",
        "description": "Reclassify raster values into classes",
        "parameters": {
            "type": "object",
            "properties": {
                "raster_layer": {"type": "string", "description": "Input raster layer name"},
                "classification": {"type": "string", "description": "Classification JSON string e.g. '[[0,100,1],[100,200,2]]'"}
            },
            "required": ["raster_layer", "classification"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer
import processing

raster_layer = {raster_layer}
classification = {classification}

rl = QgsProject.instance().mapLayersByName(raster_layer)
if not rl:
    raise RuntimeError(f"Raster layer '{raster_layer}' not found")
rl = rl[0]
if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
    raise RuntimeError(f"Invalid raster layer '{raster_layer}'")

output_name = f"classified_{raster_layer}"
output_path = f'/tmp/{output_name}.tif'

params = {{
    'INPUT_RASTER': rl,
    'RASTER_BAND': 1,
    'TABLE': classification,
    'OUTPUT': output_path
}}
processing.run("native:reclassifybytable", params)
result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load classified raster")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Raster '{raster_layer}' reclassified"""
    },
    {
        "name": "raster_reproject",
        "description": "Reproject raster to a new CRS",
        "parameters": {
            "type": "object",
            "properties": {
                "raster_layer": {"type": "string", "description": "Input raster layer name"},
                "crs": {"type": "string", "description": "Target CRS e.g. 'EPSG:4326'"}
            },
            "required": ["raster_layer", "crs"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer, QgsCoordinateReferenceSystem
import processing

raster_layer = {raster_layer}
crs = {crs}

rl = QgsProject.instance().mapLayersByName(raster_layer)
if not rl:
    raise RuntimeError(f"Raster layer '{raster_layer}' not found")
rl = rl[0]
if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
    raise RuntimeError(f"Invalid raster layer '{raster_layer}'")

output_name = f"reprojected_{raster_layer}"
output_path = f'/tmp/{output_name}.tif'

params = {{
    'INPUT': rl,
    'TARGET_CRS': QgsCoordinateReferenceSystem(crs),
    'OUTPUT': output_path
}}
processing.run("native:warpreproject", params)
result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load reprojected raster")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Raster reprojected to {crs}"""
    },
    {
        "name": "raster_clip",
        "description": "Clip raster by extent or mask layer",
        "parameters": {
            "type": "object",
            "properties": {
                "raster_layer": {"type": "string", "description": "Input raster layer name"},
                "extent": {"type": "object", "description": "Optional: extent dict with xmin,ymin,xmax,ymax"},
                "mask_layer": {"type": "string", "description": "Optional: mask vector layer name"}
            },
            "required": ["raster_layer"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer, QgsRectangle
import processing

raster_layer = {raster_layer}
extent = {extent}
mask_layer = {mask_layer}

rl = QgsProject.instance().mapLayersByName(raster_layer)
if not rl:
    raise RuntimeError(f"Raster layer '{raster_layer}' not found")
rl = rl[0]
if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
    raise RuntimeError(f"Invalid raster layer '{raster_layer}'")

output_name = f"clipped_{raster_layer}"
output_path = f'/tmp/{output_name}.tif'

if mask_layer:
    ml = QgsProject.instance().mapLayersByName(mask_layer)
    if not ml:
        raise RuntimeError(f"Mask layer '{mask_layer}' not found")
    ml = ml[0]
    if not isinstance(ml, QgsVectorLayer) or not ml.isValid():
        raise RuntimeError(f"Invalid mask layer '{mask_layer}'")
    params = {{'INPUT': rl, 'MASK': ml, 'OUTPUT': output_path}}
    processing.run("native:cliprasterbymasklayer", params)
elif extent:
    rect = QgsRectangle(extent['xmin'], extent['ymin'], extent['xmax'], extent['ymax'])
    params = {{'INPUT': rl, 'EXTENT': rect, 'OUTPUT': output_path}}
    processing.run("native:cliprasterbyextent", params)
else:
    raise RuntimeError("Provide either 'extent' or 'mask_layer'")

result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load clipped raster")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Raster clipped to {'mask' if mask_layer else 'extent'}"""
    },
    {
        "name": "raster_statistics",
        "description": "Compute band statistics (min, max, mean, std)",
        "parameters": {
            "type": "object",
            "properties": {
                "raster_layer": {"type": "string", "description": "Input raster layer name"}
            },
            "required": ["raster_layer"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer, QgsRasterBandStats

raster_layer = {raster_layer}

rl = QgsProject.instance().mapLayersByName(raster_layer)
if not rl:
    raise RuntimeError(f"Raster layer '{raster_layer}' not found")
rl = rl[0]
if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
    raise RuntimeError(f"Invalid raster layer '{raster_layer}'")

stats = rl.dataProvider().bandStatistics(1, QgsRasterBandStats.All)
if stats is None:
    raise RuntimeError("Failed to compute raster statistics")

result = (
    f"Statistics for '{raster_layer}': "
    f"min={stats.minimumValue:.4f}, "
    f"max={stats.maximumValue:.4f}, "
    f"mean={stats.mean:.4f}, "
    f"std={stats.stdDev:.4f}"
)"""
    },
    {
        "name": "raster_to_vector",
        "description": "Polygonize a classified raster layer",
        "parameters": {
            "type": "object",
            "properties": {
                "raster_layer": {"type": "string", "description": "Input raster layer name"},
                "output_name": {"type": "string", "description": "Output vector layer name"}
            },
            "required": ["raster_layer", "output_name"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer
import processing

raster_layer = {raster_layer}
output_name = {output_name}

rl = QgsProject.instance().mapLayersByName(raster_layer)
if not rl:
    raise RuntimeError(f"Raster layer '{raster_layer}' not found")
rl = rl[0]
if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
    raise RuntimeError(f"Invalid raster layer '{raster_layer}'")

output_path = f'/tmp/{output_name}.gpkg'
params = {{
    'INPUT': rl,
    'BAND': 1,
    'FIELD': 'DN',
    'OUTPUT': output_path
}}
processing.run("native:polygonize", params)
result_layer = QgsVectorLayer(output_path, output_name, 'ogr')
if not result_layer.isValid():
    raise RuntimeError("Failed to load polygonized layer")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Raster '{raster_layer}' polygonized as '{output_name}'"""
    },
    {
        "name": "vector_to_raster",
        "description": "Rasterize a vector layer",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_name": {"type": "string", "description": "Input vector layer name"},
                "output_name": {"type": "string", "description": "Output raster name"},
                "pixel_size": {"type": "number", "description": "Pixel size in map units"}
            },
            "required": ["layer_name", "output_name", "pixel_size"]
        },
        "code": """from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer
import processing

layer_name = {layer_name}
output_name = {output_name}
pixel_size = {pixel_size}

vl = QgsProject.instance().mapLayersByName(layer_name)
if not vl:
    raise RuntimeError(f"Vector layer '{layer_name}' not found")
vl = vl[0]
if not isinstance(vl, QgsVectorLayer) or not vl.isValid():
    raise RuntimeError(f"Invalid vector layer '{layer_name}'")

output_path = f'/tmp/{output_name}.tif'
extent = vl.extent()
params = {{
    'INPUT': vl,
    'FIELD': '',
    'BURN': 1,
    'UNITS': 1,
    'WIDTH': int((extent.xMaximum() - extent.xMinimum()) / pixel_size),
    'HEIGHT': int((extent.yMaximum() - extent.yMinimum()) / pixel_size),
    'EXTENT': extent,
    'OUTPUT': output_path
}}
processing.run("native:rasterize", params)
result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load rasterized layer")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Vector '{layer_name}' rasterized as '{output_name}'"""
    }
]
