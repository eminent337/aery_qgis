TOOLS = [
    {
        "name": "run_gee_code",
        "description": "Execute Google Earth Engine JavaScript code via geemap",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "GEE JavaScript code string"}
            },
            "required": ["code"]
        },
        "code": """import geemap
import ee
from qgis.core import QgsProject, QgsRasterLayer

code = {code}

ee.Initialize()
result = geemap.evaluate(code)
if result is None:
    raise RuntimeError("GEE code evaluation returned no result")

result = f"GEE code executed successfully: {str(result)[:200]}"
iface.mapCanvas().refresh()"""
    },
    {
        "name": "gee_export_image",
        "description": "Export a GEE image to GeoTIFF",
        "parameters": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "GEE image asset ID or expression"},
                "name": {"type": "string", "description": "Export file name"},
                "scale": {"type": "number", "description": "Resolution in meters"}
            },
            "required": ["image", "name", "scale"]
        },
        "code": """import ee
import geemap
from qgis.core import QgsProject, QgsRasterLayer

image_id = {image}
name = {name}
scale = {scale}

ee.Initialize()
img = ee.Image(image_id)
if img is None:
    raise RuntimeError(f"GEE image '{image_id}' not found")

output_path = f'/tmp/{name}.tif'
geemap.ee_export_image(img, filename=output_path, scale=scale, region=img.geometry())
result_layer = QgsRasterLayer(output_path, name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load exported GEE image")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"GEE image exported as '{name}' at {scale}m"""
    },
    {
        "name": "gee_time_series",
        "description": "Extract GEE time series for a region",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "GEE image collection ID"},
                "band": {"type": "string", "description": "Band name to extract"},
                "geometry": {"type": "object", "description": "Region geometry as GeoJSON-like dict"}
            },
            "required": ["collection", "band", "geometry"]
        },
        "code": """import ee
from qgis.core import QgsProject

collection_id = {collection}
band = {band}
geometry = {geometry}

ee.Initialize()
geom = ee.Geometry(geometry)
col = ee.ImageCollection(collection_id).filterBounds(geom)
if col.size().getInfo() == 0:
    raise RuntimeError(f"No images found in collection '{collection_id}' for given region")

def extract_ts(img):
    mean = img.select(band).reduceRegion(ee.Reducer.mean(), geom, scale=1000).get(band)
    return ee.Feature(None, {{"date": img.date().format("YYYY-MM-dd"), band: mean}})

fc = ee.FeatureCollection(col.map(extract_ts))
data = fc.getInfo()
result = f"Time series extracted from '{collection_id}', band '{band}': {len(data.get('features', []))} records"""
    },
    {
        "name": "gee_sentinel1",
        "description": "Access Sentinel-1 SAR data via GEE",
        "parameters": {
            "type": "object",
            "properties": {
                "bbox": {"type": "array", "items": {"type": "number"}, "description": "Bounding box [xmin, ymin, xmax, ymax]"},
                "date_range": {"type": "array", "items": {"type": "string"}, "description": "Date range [start, end] as strings"}
            },
            "required": ["bbox", "date_range"]
        },
        "code": """import ee
import geemap
from qgis.core import QgsProject, QgsRasterLayer

bbox = {bbox}
date_range = {date_range}

ee.Initialize()
roi = ee.Geometry.Rectangle(bbox)
col = (ee.ImageCollection('COPERNICUS/S1_GRD')
    .filterBounds(roi)
    .filterDate(date_range[0], date_range[1])
    .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
    .select('VV'))

if col.size().getInfo() == 0:
    raise RuntimeError("No Sentinel-1 images found for given parameters")

median = col.median().clip(roi)
output_path = '/tmp/sentinel1_median.tif'
geemap.ee_export_image(median, filename=output_path, scale=10, region=roi)
result_layer = QgsRasterLayer(output_path, "Sentinel1_VV_median")
if not result_layer.isValid():
    raise RuntimeError("Failed to load Sentinel-1 image")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Sentinel-1 VV median composite loaded ({col.size().getInfo()} scenes)"""
    },
    {
        "name": "gee_sentinel2",
        "description": "Access Sentinel-2 with cloud masking via GEE",
        "parameters": {
            "type": "object",
            "properties": {
                "bbox": {"type": "array", "items": {"type": "number"}, "description": "Bounding box [xmin, ymin, xmax, ymax]"},
                "date_range": {"type": "array", "items": {"type": "string"}, "description": "Date range [start, end]"},
                "max_cloud": {"type": "number", "description": "Maximum cloud cover percentage"}
            },
            "required": ["bbox", "date_range", "max_cloud"]
        },
        "code": """import ee
import geemap
from qgis.core import QgsProject, QgsRasterLayer

bbox = {bbox}
date_range = {date_range}
max_cloud = {max_cloud}

ee.Initialize()
roi = ee.Geometry.Rectangle(bbox)

def mask_s2clouds(image):
    qa = image.select('QA60')
    cloudBitMask = 1 << 10
    cirrusBitMask = 1 << 11
    mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(qa.bitwiseAnd(cirrusBitMask).eq(0))
    return image.updateMask(mask).divide(10000)

col = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
    .filterBounds(roi)
    .filterDate(date_range[0], date_range[1])
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', max_cloud))
    .map(mask_s2clouds))

if col.size().getInfo() == 0:
    raise RuntimeError("No Sentinel-2 images found for given parameters")

median = col.median().clip(roi)
output_path = '/tmp/sentinel2_median.tif'
geemap.ee_export_image(median, filename=output_path, scale=10, region=roi)
result_layer = QgsRasterLayer(output_path, "Sentinel2_median")
if not result_layer.isValid():
    raise RuntimeError("Failed to load Sentinel-2 image")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Sentinel-2 median composite loaded ({col.size().getInfo()} scenes, cloud<{max_cloud}%)"""
    },
    {
        "name": "gee_landsat",
        "description": "Access Landsat data via GEE",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Landsat collection e.g. 'LANDSAT/LC08/C02/T1_L2'"},
                "bbox": {"type": "array", "items": {"type": "number"}, "description": "Bounding box [xmin, ymin, xmax, ymax]"},
                "date_range": {"type": "array", "items": {"type": "string"}, "description": "Date range [start, end]"}
            },
            "required": ["collection", "bbox", "date_range"]
        },
        "code": """import ee
import geemap
from qgis.core import QgsProject, QgsRasterLayer

collection_id = {collection}
bbox = {bbox}
date_range = {date_range}

ee.Initialize()
roi = ee.Geometry.Rectangle(bbox)
col = (ee.ImageCollection(collection_id)
    .filterBounds(roi)
    .filterDate(date_range[0], date_range[1])
    .filter(ee.Filter.lt('CLOUD_COVER', 20)))

if col.size().getInfo() == 0:
    raise RuntimeError("No Landsat images found for given parameters")

median = col.median().clip(roi)
output_path = '/tmp/landsat_median.tif'
geemap.ee_export_image(median, filename=output_path, scale=30, region=roi)
result_layer = QgsRasterLayer(output_path, "Landsat_median")
if not result_layer.isValid():
    raise RuntimeError("Failed to load Landsat image")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Landsat '{collection_id}' median composite loaded ({col.size().getInfo()} scenes)"""
    },
    {
        "name": "gee_modis",
        "description": "Access MODIS data via GEE",
        "parameters": {
            "type": "object",
            "properties": {
                "product": {"type": "string", "description": "MODIS product e.g. 'MODIS/006/MOD13Q1'"},
                "bbox": {"type": "array", "items": {"type": "number"}, "description": "Bounding box [xmin, ymin, xmax, ymax]"},
                "date_range": {"type": "array", "items": {"type": "string"}, "description": "Date range [start, end]"}
            },
            "required": ["product", "bbox", "date_range"]
        },
        "code": """import ee
import geemap
from qgis.core import QgsProject, QgsRasterLayer

product = {product}
bbox = {bbox}
date_range = {date_range}

ee.Initialize()
roi = ee.Geometry.Rectangle(bbox)
col = (ee.ImageCollection(product)
    .filterBounds(roi)
    .filterDate(date_range[0], date_range[1]))

if col.size().getInfo() == 0:
    raise RuntimeError("No MODIS images found for given parameters")

median = col.median().clip(roi)
output_path = '/tmp/modis_median.tif'
geemap.ee_export_image(median, filename=output_path, scale=250, region=roi)
result_layer = QgsRasterLayer(output_path, "MODIS_median")
if not result_layer.isValid():
    raise RuntimeError("Failed to load MODIS image")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"MODIS '{product}' median composite loaded ({col.size().getInfo()} scenes)"""
    },
    {
        "name": "gee_climate_data",
        "description": "Access ERA5/CHIRPS climate data via GEE",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string", "description": "Climate dataset e.g. 'ECMWF/ERA5/MONTHLY' or 'UCSB-CHG/CHIRPS/DAILY'"},
                "bbox": {"type": "array", "items": {"type": "number"}, "description": "Bounding box [xmin, ymin, xmax, ymax]"},
                "date_range": {"type": "array", "items": {"type": "string"}, "description": "Date range [start, end]"}
            },
            "required": ["dataset", "bbox", "date_range"]
        },
        "code": """import ee
import geemap
from qgis.core import QgsProject, QgsRasterLayer

dataset_id = {dataset}
bbox = {bbox}
date_range = {date_range}

ee.Initialize()
roi = ee.Geometry.Rectangle(bbox)
col = (ee.ImageCollection(dataset_id)
    .filterBounds(roi)
    .filterDate(date_range[0], date_range[1]))

if col.size().getInfo() == 0:
    raise RuntimeError(f"No climate data found for dataset '{dataset_id}'")

mean = col.mean().clip(roi)
output_path = '/tmp/climate_mean.tif'
geemap.ee_export_image(mean, filename=output_path, scale=1000, region=roi)
result_layer = QgsRasterLayer(output_path, f"climate_{dataset_id.split('/')[-1]}")
if not result_layer.isValid():
    raise RuntimeError("Failed to load climate data")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Climate dataset '{dataset_id}' loaded ({col.size().getInfo()} scenes)"""
    },
    {
        "name": "download_sentinel2",
        "description": "Download Sentinel-2 imagery from Copernicus Data Space",
        "parameters": {
            "type": "object",
            "properties": {
                "bbox": {"type": "array", "items": {"type": "number"}, "description": "Bounding box [xmin, ymin, xmax, ymax]"},
                "date": {"type": "string", "description": "Acquisition date YYYY-MM-DD"},
                "max_cloud": {"type": "number", "description": "Maximum cloud cover percentage"}
            },
            "required": ["bbox", "date", "max_cloud"]
        },
        "code": """import requests
from qgis.core import QgsProject, QgsRasterLayer
import processing

bbox = {bbox}
date = {date}
max_cloud = {max_cloud}

# Copernicus Data Space API query
url = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
filter_expr = (
    f"Collection/Name eq 'SENTINEL-2' and "
    f"OData.CSC.Intersects(area=geography'SRID=4326;POLYGON(({bbox[0]} {bbox[1]},{bbox[2]} {bbox[1]},{bbox[2]} {bbox[3]},{bbox[0]} {bbox[3]},{bbox[0]} {bbox[1]}))') and "
    f"ContentDate/Start gt {date}T00:00:00Z and ContentDate/Start lt {date}T23:59:59Z and "
    f"Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCoverage' and att/OData.CSC.DoubleAttribute/Value le {max_cloud})"
)
params = {{'$filter': filter_expr, '$top': 1, '$orderby': 'ContentDate/Start desc'}}
resp = requests.get(url, params=params, timeout=30)
resp.raise_for_status()
data = resp.json()
products = data.get('value', [])
if not products:
    raise RuntimeError("No Sentinel-2 products found matching criteria")

result = f"Sentinel-2 product found: {products[0].get('Name', 'unknown')} (ID: {products[0].get('Id', '')})" """
    },
    {
        "name": "download_landsat",
        "description": "Download Landsat imagery from USGS",
        "parameters": {
            "type": "object",
            "properties": {
                "bbox": {"type": "array", "items": {"type": "number"}, "description": "Bounding box [xmin, ymin, xmax, ymax]"},
                "date": {"type": "string", "description": "Acquisition date YYYY-MM-DD"},
                "max_cloud": {"type": "number", "description": "Maximum cloud cover percentage"}
            },
            "required": ["bbox", "date", "max_cloud"]
        },
        "code": """import requests
from qgis.core import QgsProject, QgsRasterLayer
import processing

bbox = {bbox}
date = {date}
max_cloud = {max_cloud}

# USGS M2M API login
m2m_url = "https://m2m.cr.usgs.gov/api/api/json/stable/"
payload = {{"username": "", "password": ""}}
resp = requests.post(f"{m2m_url}login", json=payload, timeout=30)
token = resp.json().get("data")
if not token:
    raise RuntimeError("USGS login failed - credentials required")

scene_search = {{
    "datasetName": "landsat_ot_c2_l2",
    "spatialFilter": {{
        "filterType": "mbr",
        "lowerLeft": {{"latitude": bbox[1], "longitude": bbox[0]}},
        "upperRight": {{"latitude": bbox[3], "longitude": bbox[2]}}
    }},
    "acquisitionFilter": {{
        "start": f"{date}",
        "end": f"{date}"
    }},
    "maxResults": 1
}}
headers = {{"X-Auth-Token": token}}
resp = requests.post(f"{m2m_url}scene-search", json=scene_search, headers=headers, timeout=30)
resp.raise_for_status()
scenes = resp.json().get("data", {{}}).get("results", [])
if not scenes:
    raise RuntimeError("No Landsat scenes found matching criteria")

result = f"Landsat scene found: {scenes[0].get('entityId', 'unknown')}" """
    },
    {
        "name": "compute_ndvi",
        "description": "Compute NDVI from red and NIR bands",
        "parameters": {
            "type": "object",
            "properties": {
                "red_layer": {"type": "string", "description": "Red band raster layer name"},
                "nir_layer": {"type": "string", "description": "NIR band raster layer name"},
                "output_name": {"type": "string", "description": "Output NDVI raster name"}
            },
            "required": ["red_layer", "nir_layer", "output_name"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer, QgsRasterCalculator, QgsRasterCalculatorEntry

red_layer = {red_layer}
nir_layer = {nir_layer}
output_name = {output_name}

red = QgsProject.instance().mapLayersByName(red_layer)
nir = QgsProject.instance().mapLayersByName(nir_layer)
if not red or not nir:
    raise RuntimeError("One or both input raster layers not found")
red = red[0]; nir = nir[0]
if not isinstance(red, QgsRasterLayer) or not red.isValid():
    raise RuntimeError(f"Invalid red band layer '{red_layer}'")
if not isinstance(nir, QgsRasterLayer) or not nir.isValid():
    raise RuntimeError(f"Invalid NIR band layer '{nir_layer}'")

red_entry = QgsRasterCalculatorEntry()
red_entry.ref = 'red@1'; red_entry.raster = red; red_entry.bandNumber = 1
nir_entry = QgsRasterCalculatorEntry()
nir_entry.ref = 'nir@1'; nir_entry.raster = nir; nir_entry.bandNumber = 1

expr = '(("nir@1" - "red@1") / ("nir@1" + "red@1" + 1e-10))'
output_path = f'/tmp/{output_name}.tif'
calc = QgsRasterCalculator(expr, output_path, 'GTiff', red.extent(), red.width(), red.height(), [red_entry, nir_entry])
if calc.processCalculation() != 0:
    raise RuntimeError("NDVI calculation failed")

result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load NDVI result")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"NDVI computed as '{output_name}'"""
    },
    {
        "name": "compute_ndwi",
        "description": "Compute NDWI (water index) from green and NIR bands",
        "parameters": {
            "type": "object",
            "properties": {
                "green_layer": {"type": "string", "description": "Green band raster layer name"},
                "nir_layer": {"type": "string", "description": "NIR band raster layer name"},
                "output_name": {"type": "string", "description": "Output NDWI raster name"}
            },
            "required": ["green_layer", "nir_layer", "output_name"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer, QgsRasterCalculator, QgsRasterCalculatorEntry

green_layer = {green_layer}
nir_layer = {nir_layer}
output_name = {output_name}

green = QgsProject.instance().mapLayersByName(green_layer)
nir = QgsProject.instance().mapLayersByName(nir_layer)
if not green or not nir:
    raise RuntimeError("One or both input raster layers not found")
green = green[0]; nir = nir[0]
if not isinstance(green, QgsRasterLayer) or not green.isValid():
    raise RuntimeError(f"Invalid green band layer '{green_layer}'")
if not isinstance(nir, QgsRasterLayer) or not nir.isValid():
    raise RuntimeError(f"Invalid NIR band layer '{nir_layer}'")

green_entry = QgsRasterCalculatorEntry()
green_entry.ref = 'green@1'; green_entry.raster = green; green_entry.bandNumber = 1
nir_entry = QgsRasterCalculatorEntry()
nir_entry.ref = 'nir@1'; nir_entry.raster = nir; nir_entry.bandNumber = 1

expr = '(("green@1" - "nir@1") / ("green@1" + "nir@1" + 1e-10))'
output_path = f'/tmp/{output_name}.tif'
calc = QgsRasterCalculator(expr, output_path, 'GTiff', green.extent(), green.width(), green.height(), [green_entry, nir_entry])
if calc.processCalculation() != 0:
    raise RuntimeError("NDWI calculation failed")

result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load NDWI result")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"NDWI computed as '{output_name}'"""
    },
    {
        "name": "compute_evi",
        "description": "Compute Enhanced Vegetation Index from red, NIR, and blue bands",
        "parameters": {
            "type": "object",
            "properties": {
                "red_layer": {"type": "string", "description": "Red band raster layer name"},
                "nir_layer": {"type": "string", "description": "NIR band raster layer name"},
                "blue_layer": {"type": "string", "description": "Blue band raster layer name"},
                "output_name": {"type": "string", "description": "Output EVI raster name"}
            },
            "required": ["red_layer", "nir_layer", "blue_layer", "output_name"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer, QgsRasterCalculator, QgsRasterCalculatorEntry

red_layer = {red_layer}
nir_layer = {nir_layer}
blue_layer = {blue_layer}
output_name = {output_name}

red = QgsProject.instance().mapLayersByName(red_layer)
nir = QgsProject.instance().mapLayersByName(nir_layer)
blue = QgsProject.instance().mapLayersByName(blue_layer)
if not red or not nir or not blue:
    raise RuntimeError("One or more input raster layers not found")
red = red[0]; nir = nir[0]; blue = blue[0]
for name, rl in [("red", red), ("NIR", nir), ("blue", blue)]:
    if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
        raise RuntimeError(f"Invalid {name} band layer")

r_entry = QgsRasterCalculatorEntry(); r_entry.ref = 'red@1'; r_entry.raster = red; r_entry.bandNumber = 1
n_entry = QgsRasterCalculatorEntry(); n_entry.ref = 'nir@1'; n_entry.raster = nir; n_entry.bandNumber = 1
b_entry = QgsRasterCalculatorEntry(); b_entry.ref = 'blue@1'; b_entry.raster = blue; b_entry.bandNumber = 1

expr = '2.5 * (("nir@1" - "red@1") / ("nir@1" + 6 * "red@1" - 7.5 * "blue@1" + 1e-10))'
output_path = f'/tmp/{output_name}.tif'
calc = QgsRasterCalculator(expr, output_path, 'GTiff', red.extent(), red.width(), red.height(), [r_entry, n_entry, b_entry])
if calc.processCalculation() != 0:
    raise RuntimeError("EVI calculation failed")

result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load EVI result")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"EVI computed as '{output_name}'"""
    },
    {
        "name": "land_cover_classification",
        "description": "Perform ML-based land cover classification using K-means clustering",
        "parameters": {
            "type": "object",
            "properties": {
                "raster_layer": {"type": "string", "description": "Input raster layer name"},
                "n_clusters": {"type": "integer", "description": "Number of land cover classes"}
            },
            "required": ["raster_layer", "n_clusters"]
        },
        "code": """import numpy as np
from osgeo import gdal
from sklearn.cluster import KMeans
from qgis.core import QgsProject, QgsRasterLayer

raster_layer = {raster_layer}
n_clusters = {n_clusters}

rl = QgsProject.instance().mapLayersByName(raster_layer)
if not rl:
    raise RuntimeError(f"Raster layer '{raster_layer}' not found")
rl = rl[0]
if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
    raise RuntimeError(f"Invalid raster layer '{raster_layer}'")

src_path = rl.source()
src_ds = gdal.Open(src_path, gdal.GA_ReadOnly)
if src_ds is None:
    raise RuntimeError("Failed to open raster source")

bands = [src_ds.GetRasterBand(i + 1).ReadAsArray().flatten() for i in range(src_ds.RasterCount)]
X = np.column_stack(bands)
mask = ~np.isnan(X).any(axis=1)
valid_pixels = X[mask]

km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
labels = km.fit_predict(valid_pixels)

classified = np.empty(X.shape[0], dtype=np.uint8)
classified.fill(255)
classified[mask] = labels.astype(np.uint8)

rows, cols = src_ds.RasterYSize, src_ds.RasterXSize
classified_2d = classified.reshape(rows, cols)

output_name = f"classified_{raster_layer}_{n_clusters}classes"
output_path = f'/tmp/{output_name}.tif'
driver = gdal.GetDriverByName('GTiff')
out_ds = driver.Create(output_path, cols, rows, 1, gdal.GDT_Byte)
out_ds.SetGeoTransform(src_ds.GetGeoTransform())
out_ds.SetProjection(src_ds.GetProjection())
out_ds.GetRasterBand(1).WriteArray(classified_2d)
out_ds.GetRasterBand(1).SetNoDataValue(255)
out_ds.FlushCache()
src_ds = out_ds = None

result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load classification result")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"K-means classification ({n_clusters} classes) completed as '{output_name}'"""
    },
    {
        "name": "change_detection",
        "description": "Detect changes between two raster layers",
        "parameters": {
            "type": "object",
            "properties": {
                "earlier_raster": {"type": "string", "description": "Earlier raster layer name"},
                "later_raster": {"type": "string", "description": "Later raster layer name"},
                "output_name": {"type": "string", "description": "Output change detection raster name"}
            },
            "required": ["earlier_raster", "later_raster", "output_name"]
        },
        "code": """from qgis.core import QgsProject, QgsRasterLayer, QgsRasterCalculator, QgsRasterCalculatorEntry

earlier_raster = {earlier_raster}
later_raster = {later_raster}
output_name = {output_name}

earlier = QgsProject.instance().mapLayersByName(earlier_raster)
later = QgsProject.instance().mapLayersByName(later_raster)
if not earlier or not later:
    raise RuntimeError("One or both input raster layers not found")
earlier = earlier[0]; later = later[0]
for name, rl in [("earlier", earlier), ("later", later)]:
    if not isinstance(rl, QgsRasterLayer) or not rl.isValid():
        raise RuntimeError(f"Invalid {name} raster layer")

e_entry = QgsRasterCalculatorEntry()
e_entry.ref = 'earlier@1'; e_entry.raster = earlier; e_entry.bandNumber = 1
l_entry = QgsRasterCalculatorEntry()
l_entry.ref = 'later@1'; l_entry.raster = later; l_entry.bandNumber = 1

expr = '"later@1" - "earlier@1"'
output_path = f'/tmp/{output_name}.tif'
calc = QgsRasterCalculator(expr, output_path, 'GTiff', earlier.extent(), earlier.width(), earlier.height(), [e_entry, l_entry])
if calc.processCalculation() != 0:
    raise RuntimeError("Change detection calculation failed")

result_layer = QgsRasterLayer(output_path, output_name)
if not result_layer.isValid():
    raise RuntimeError("Failed to load change detection result")
QgsProject.instance().addMapLayer(result_layer)
iface.mapCanvas().setExtent(result_layer.extent())
iface.mapCanvas().refresh()
result = f"Change detection completed: '{output_name}' (later - earlier)"""
    },
    {
        "name": "time_series_analysis",
        "description": "Analyze NDVI/spectral time series for a vector field",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_name": {"type": "string", "description": "Vector layer name with time series data"},
                "field": {"type": "string", "description": "Numeric field to analyze"},
                "dates": {"type": "array", "items": {"type": "string"}, "description": "Date labels for each time step"}
            },
            "required": ["layer_name", "field", "dates"]
        },
        "code": """import numpy as np
from qgis.core import QgsProject, QgsVectorLayer

layer_name = {layer_name}
field = {field}
dates = {dates}

vl = QgsProject.instance().mapLayersByName(layer_name)
if not vl:
    raise RuntimeError(f"Vector layer '{layer_name}' not found")
vl = vl[0]
if not isinstance(vl, QgsVectorLayer) or not vl.isValid():
    raise RuntimeError(f"Invalid vector layer '{layer_name}'")

field_idx = vl.fields().indexFromName(field)
if field_idx == -1:
    raise RuntimeError(f"Field '{field}' not found in layer '{layer_name}'")

values = []
for feat in vl.getFeatures():
    val = feat.attributes()[field_idx]
    if val is not None:
        values.append(float(val))

if not values:
    raise RuntimeError(f"No valid values found in field '{field}'")

arr = np.array(values)
trend = "increasing" if np.polyfit(range(len(arr)), arr, 1)[0] > 0 else "decreasing"

result = (
    f"Time series analysis for '{layer_name}.{field}': "
    f"{len(arr)} observations, "
    f"mean={arr.mean():.4f}, "
    f"trend={trend}, "
    f"dates={dates[:3]}..."
)
iface.mapCanvas().refresh()"""
    }
]
