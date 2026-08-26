"""Geospatial helper functions for the Aery QGIS agent.

These functions are:
1. Registered as first-class agent tools (Approach 1)
2. Injected into executor globals for use inside run_qgis_code (Approach 2)
"""

import json
import math
import os
import subprocess
import tempfile
import urllib.request
import base64
from typing import Any
from contextlib import contextmanager
import threading

_proj_env_lock = threading.Lock()


@contextmanager
def clean_proj_env():
    """Temporarily remove PROJ_DATA/PROJ_LIB from environment during file writes.

    pyogrio (geopandas >= 1.0) validates bundled data and can crash if PROJ_DATA
    points elsewhere.
    """
    with _proj_env_lock:
        saved = {}
        for var in ("PROJ_DATA", "PROJ_LIB"):
            if var in os.environ:
                saved[var] = os.environ.pop(var)
        try:
            yield
        finally:
            os.environ.update(saved)


def safe_to_file(gdf, output_path: str, **kwargs) -> None:
    """Write a GeoDataFrame to file with PROJ env vars temporarily cleared.

    Avoids pyogrio PROJ data detection errors inside the QGIS environment.
    """
    with clean_proj_env():
        gdf.to_file(output_path, **kwargs)


def resolve_layer(name_or_id: str):
    """Resolve a QGIS layer by its name or ID with robust fuzzy fallback."""
    from qgis.core import QgsProject
    project = QgsProject.instance()
    # 1. Direct ID lookup
    layer = project.mapLayer(name_or_id)
    if layer:
        return layer
    # 2. Exact name lookup
    layers = project.mapLayersByName(name_or_id)
    if layers:
        return layers[0]
    # 3. Case-insensitive name lookup
    target_lower = name_or_id.lower().strip()
    for l in project.mapLayers().values():
        if l.name().lower().strip() == target_lower:
            return l
    return None


def safe_create_geodataframe(features: Any, crs: str = "EPSG:4326", **kwargs):
    """Safely construct a GeoDataFrame, handling empty or non-spatial inputs without ValueError."""
    import geopandas as gpd
    from shapely.geometry import shape, Point, Polygon, LineString

    if not features:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)

    # If already a GeoDataFrame
    if isinstance(features, gpd.GeoDataFrame):
        return features

    valid_features = []
    if isinstance(features, list):
        for f in features:
            if not isinstance(f, dict):
                continue
            # Case 1: Standard GeoJSON geometry dict or shapely object
            if "geometry" in f and f["geometry"] is not None:
                geom = f["geometry"]
                f_copy = dict(f)
                try:
                    f_copy["geometry"] = shape(geom) if isinstance(geom, dict) else geom
                    valid_features.append(f_copy)
                except Exception:
                    pass
            # Case 2: OSM point elements with lat/lon
            elif "lat" in f and "lon" in f:
                try:
                    f_copy = dict(f)
                    f_copy["geometry"] = Point(float(f["lon"]), float(f["lat"]))
                    valid_features.append(f_copy)
                except Exception:
                    pass
            # Case 3: Shape or Geometry object directly
            elif hasattr(f, "geometry"):
                valid_features.append(f)

    if not valid_features:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)

    try:
        return gpd.GeoDataFrame(valid_features, geometry="geometry", crs=crs, **kwargs)
    except Exception:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def query_overpass(query: str, timeout: int = 30) -> dict:
    """Execute an Overpass QL query with automatic mirror failover, User-Agent, and backoff."""
    import json
    import time
    import urllib.parse
    import urllib.request

    last_err = None
    headers = {
        "User-Agent": "AeryQGISPlugin/1.0 (https://github.com/eminent337/aery; geospatial-assistant)",
        "Accept": "application/json",
    }

    # Strip formatting or wrap in [out:json] if missing
    clean_query = query.strip()
    if not clean_query.startswith("[out:json]"):
        clean_query = f"[out:json][timeout:{timeout}];\n" + clean_query

    encoded_data = urllib.parse.urlencode({"data": clean_query}).encode("utf-8")

    for mirror in OVERPASS_MIRRORS:
        try:
            req = urllib.request.Request(mirror, data=encoded_data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except Exception as e:
            err_str = str(e)
            last_err = e
            # Rate limiting or 500 error - brief pause before trying next mirror
            if "429" in err_str or "500" in err_str or "504" in err_str:
                time.sleep(1.5)
            continue

    raise RuntimeError(f"All Overpass API mirrors failed. Last error: {last_err}")


def run_quickosm_query(key: str, value: str, extent=None, layer_name: str = "OSM Query") -> dict:
    """Execute a QuickOSM processing query if installed, or gracefully fallback."""
    from qgis.core import QgsProject, QgsProcessingFeedback, QgsRectangle
    import processing

    try:
        algs = [a.id() for a in QgsProject.instance().processingRegistry().algorithms()]
    except Exception:
        algs = []

    # QuickOSM extent query algorithm id
    q_alg = next((a for a in algs if "quickosm" in a.lower() and "extent" in a.lower()), None)
    if not q_alg:
        return {"success": False, "error": "QuickOSM plugin algorithm not found in QGIS processing registry."}

    try:
        params = {
            "KEY": key,
            "VALUE": value,
            "EXTENT": extent,
            "OUTPUT": "memory:",
        }
        feedback = QgsProcessingFeedback()
        res = processing.run(q_alg, params, feedback=feedback)
        return {"success": True, "output": res}
    except Exception as e:
        return {"success": False, "error": f"QuickOSM execution failed: {e}"}

def smooth_geometry(geom, simplify_tolerance: float = 0.5, preserve_topology: bool = True):
    """Simplify and smooth a polygon or geometry to remove vertex noise.

    Adapted from GeoAI (opengeos/geoai) utils/geometry.py.
    """
    if hasattr(geom, "simplify"):
        return geom.simplify(simplify_tolerance, preserve_topology=preserve_topology)
    return geom


def regularize_polygon(polygon, simplify_tolerance: float = 0.5, orthogonalize: bool = True):
    """Regularize a building footprint polygon by simplifying and aligning dominant angles.

    Adapted from GeoAI (opengeos/geoai) utils/geometry.py.
    """
    import numpy as np
    from shapely.affinity import rotate
    from shapely.geometry import Polygon

    if not hasattr(polygon, "simplify"):
        return polygon

    simplified = polygon.simplify(simplify_tolerance, preserve_topology=True)
    if not orthogonalize or not hasattr(simplified, "exterior") or simplified.exterior is None:
        return simplified

    coords = np.array(simplified.exterior.coords)
    if len(coords) < 3:
        return simplified

    segments = np.diff(coords, axis=0)
    angles = np.arctan2(segments[:, 1], segments[:, 0]) * 180 / np.pi
    binned_angles = np.round(angles / 90) * 90
    dominant_angle = np.bincount(binned_angles.astype(int) % 180).argmax()

    # Rotate to axis, box-simplify, rotate back
    rotated = rotate(simplified, -dominant_angle, origin="centroid")
    minx, miny, maxx, maxy = rotated.bounds
    # If the rotated shape is close to an envelope, return the aligned box
    rect = Polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)])
    if rotated.intersection(rect).area / max(rotated.area, 1e-6) > 0.85:
        return rotate(rect, dominant_angle, origin="centroid")
    return rotate(rotated, dominant_angle, origin="centroid")


# ── Pre-cached Fast Bounding Boxes for Major World Cities ────────────────────
# Adapted from GeoAI STACTools._LOCATION_CACHE (geoai/agents/stac_tools.py)
MAJOR_CITIES_BBOX = {
    "san francisco": [-122.5155, 37.7034, -122.3549, 37.8324],
    "new york": [-74.0479, 40.6829, -73.9067, 40.8820],
    "new york city": [-74.0479, 40.6829, -73.9067, 40.8820],
    "paris": [2.2241, 48.8156, 2.4698, 48.9022],
    "london": [-0.5103, 51.2868, 0.3340, 51.6919],
    "tokyo": [139.5694, 35.5232, 139.9182, 35.8173],
    "los angeles": [-118.6682, 33.7037, -118.1553, 34.3373],
    "chicago": [-87.9401, 41.6445, -87.5241, 42.0230],
    "accra": [-0.3100, 5.5000, -0.1000, 5.6700],
    "nairobi": [36.6500, -1.4500, 37.1000, -1.1500],
    "berlin": [13.0883, 52.3382, 13.7611, 52.6755],
    "sydney": [150.5209, -34.1183, 151.3430, -33.5781],
}


def get_city_bbox(city_name: str) -> Optional[list[float]]:
    """Get fast cached WGS84 bounding box [minx, miny, maxx, maxy] for major cities."""
    return MAJOR_CITIES_BBOX.get(city_name.lower().strip())


# ── STAC & Planetary Data Search Tools ───────────────────────────────────────
# Adapted from GeoAI STACTools & download utilities (geoai/agents/stac_tools.py)

def search_stac(
    collection: str = "sentinel-2-l2a",
    bbox: Optional[list[float]] = None,
    time_range: Optional[str] = None,
    max_items: int = 5,
    endpoint: str = "https://planetarycomputer.microsoft.com/api/stac/v1",
) -> dict:
    """Search a STAC API endpoint (e.g. Microsoft Planetary Computer or Earth Search).

    Returns a list of items with their Cloud-Optimized GeoTIFF (COG) asset URLs.
    """
    import json
    import urllib.request

    payload: dict = {
        "collections": [collection],
        "limit": max_items,
    }
    if bbox:
        payload["bbox"] = bbox
    if time_range:
        payload["datetime"] = time_range

    search_url = endpoint.rstrip("/") + "/search"
    try:
        req = urllib.request.Request(
            search_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Aery-QGIS-Plugin"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        items = []
        for feature in data.get("features", []):
            assets = {}
            for k, v in feature.get("assets", {}).items():
                assets[k] = {
                    "href": v.get("href"),
                    "title": v.get("title", k),
                    "type": v.get("type", ""),
                }
            items.append({
                "id": feature.get("id"),
                "datetime": feature.get("properties", {}).get("datetime"),
                "bbox": feature.get("bbox"),
                "assets": assets,
            })

        return {
            "endpoint": endpoint,
            "collection": collection,
            "count": len(items),
            "items": items,
        }
    except Exception as e:
        return {"error": str(e), "collection": collection, "count": 0, "items": []}


def load_cog_layer(
    url: str,
    layer_name: str = "Satellite COG",
    iface=None,
) -> dict:
    """Load a remote Cloud-Optimized GeoTIFF (COG) directly into the QGIS map canvas.

    Uses GDAL's /vsicurl/ virtual filesystem for tile streaming.
    """
    from qgis.core import QgsRasterLayer, QgsProject

    # Wrap in /vsicurl/ if standard HTTP(S) URL
    source_uri = f"/vsicurl/{url}" if url.startswith("http://") or url.startswith("https://") else url
    layer = QgsRasterLayer(source_uri, layer_name, "gdal")

    if not layer.isValid():
        return {"success": False, "error": f"Failed to load COG layer from {url}"}

    QgsProject.instance().addMapLayer(layer)
    if iface:
        iface.mapCanvas().refresh()

    return {
        "success": True,
        "layer_name": layer.name(),
        "layer_id": layer.id(),
        "crs": layer.crs().authid(),
    }


def get_gee_tile_url(image_or_map_id, vis_params: Optional[dict] = None) -> str:
    """Get an XYZ tile URL template from a Google Earth Engine image or mapId dict.

    Adapted from GeoLibre MapLibre Earth Engine plugin (maplibre-earth-engine.ts).
    """
    if isinstance(image_or_map_id, dict):
        # Already a mapId dictionary from ee.data.getMapId
        tile_url = image_or_map_id.get("tile_fetcher", {}).get("url_format", "")
        if not tile_url and "mapid" in image_or_map_id:
            mapid = image_or_map_id["mapid"]
            token = image_or_map_id.get("token", "")
            tile_url = f"https://earthengine.googleapis.com/v1/{mapid}/tiles/{{z}}/{{x}}/{{y}}?token={token}"
        return tile_url

    # Earth Engine Image object
    try:
        import ee
        map_id_dict = ee.data.getMapId({"image": image_or_map_id, **(vis_params or {})})
        return map_id_dict.get("tile_fetcher", {}).get("url_format", "")
    except Exception:
        return ""


def load_gee_tile_layer(
    image_or_map_id,
    layer_name: str = "Earth Engine Layer",
    vis_params: Optional[dict] = None,
    iface=None,
) -> dict:
    """Load a live GEE tile stream directly into QGIS as an XYZ raster layer.

    Eliminates downloading heavy GeoTIFFs to disk; streams tiles instantly.
    """
    from qgis.core import QgsRasterLayer, QgsProject

    tile_url = get_gee_tile_url(image_or_map_id, vis_params)
    if not tile_url:
        return {"success": False, "error": "Failed to generate GEE tile URL."}

    # Construct QGIS XYZ WMS URI
    uri = f"type=xyz&url={tile_url}&zmax=24&zmin=0"
    layer = QgsRasterLayer(uri, layer_name, "wms")

    if not layer.isValid():
        return {"success": False, "error": f"Failed to initialize live GEE raster layer from {tile_url}"}

    QgsProject.instance().addMapLayer(layer)
    if iface:
        iface.mapCanvas().refresh()

    return {
        "success": True,
        "layer_name": layer.name(),
        "layer_id": layer.id(),
        "tile_url": tile_url,
    }

def export_webmap(output_dir: str, basemap: str = "osm",
                  extent: str = "", include_search: bool = False,
                  title: str = "", iface=None) -> dict:
    """Export the current QGIS project as an interactive Leaflet.js web map.

    Args:
        output_dir: Full path to output directory (created if missing).
        basemap: 'osm', 'satellite', 'topo', 'stamen_toner', or 'none'.
        extent: Bbox override 'xmin,xmax,ymin,ymax' in project CRS (default: canvas).
        include_search: Add a nominatim geocoding search box.
        title: HTML page title (default: project name).
        iface: QGIS iface object (auto-injected by executor).

    Returns:
        Dict with format, files list, and output_dir.
    """
    from qgis.core import QgsProject, QgsVectorLayer, QgsVectorFileWriter, Qgis

    out_dir = output_dir
    os.makedirs(out_dir, exist_ok=True)
    data_dir = os.path.join(out_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    project = QgsProject.instance()
    all_layers = list(project.mapLayers().values())
    layer_files = []

    for i, lyr in enumerate(all_layers):
        name = lyr.name().replace(" ", "_").replace("/", "_")
        try:
            if lyr.type() == Qgis.LayerType.Vector:
                export_path = os.path.join(data_dir, f"{name}_{i}.geojson")
                options = QgsVectorLayer.LayerOptions()
                exp = QgsVectorFileWriter(export_path, "UTF-8", lyr.fields(),
                                          lyr.wkbType(), lyr.crs(), options)
                exp.addFeatures(lyr.getFeatures())
                exp = None
                layer_files.append({"name": lyr.name(), "file": f"data/{name}_{i}.geojson",
                                    "count": lyr.featureCount()})
            elif lyr.type() == Qgis.LayerType.Raster:
                src = lyr.source()
                if src and os.path.isfile(src):
                    try:
                        from osgeo import gdal
                        ds = gdal.Open(src)
                        if ds:
                            gdal.Translate(os.path.join(data_dir, f"{name}_{i}.tif"), ds)
                            layer_files.append({"name": lyr.name(), "file": f"data/{name}_{i}.tif",
                                                "bandcount": ds.RasterCount})
                    except ImportError:
                        pass
        except Exception as e:
            print(f"  skip {lyr.name()}: {e}")

    # Parse extent
    bbox = None
    if extent:
        try:
            parts = [float(v) for v in extent.split(",")]
            if len(parts) == 4:
                from qgis.core import QgsRectangle
                bbox = QgsRectangle(parts[0], parts[2], parts[1], parts[3])
        except Exception:
            pass

    if not bbox and iface:
        bbox = iface.mapCanvas().extent()

    html = _build_leaflet_html(layer_files, basemap, include_search, title or None, bbox)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)

    layer_files.append({"name": "index.html", "file": "index.html", "size": len(html)})
    print(f"Webmap: {len(layer_files)} files to {out_dir}")
    return {"format": "leaflet", "files": layer_files, "output_dir": out_dir}


def publish_geoserver(layer: str, geoserver_url: str, username: str, password: str,
                      workspace: str = "default", layer_name: str = "",
                      publish_as: str = "auto") -> dict:
    """Publish a vector or raster layer to a GeoServer REST endpoint.

    Args:
        layer: QGIS layer name to publish.
        geoserver_url: GeoServer base URL (e.g. 'https://geoserver.example.com/geoserver').
        username: GeoServer admin username.
        password: GeoServer admin password.
        workspace: GeoServer workspace name (default: 'default').
        layer_name: GeoServer layer name (default: same as QGIS layer).
        publish_as: 'auto', 'vector', or 'raster'.

    Returns:
        Dict with published status, URLs, and layer info.
    """
    from qgis.core import QgsProject

    # Validate HTTPS — warn if credentials would travel in plaintext
    if geoserver_url.startswith("http://"):
        import warnings
        warnings.warn(
            f"GeoServer URL uses HTTP — credentials will be sent in plaintext. "
            f"Use HTTPS for production: https://{geoserver_url[7:]}",
            UserWarning,
        )

    layer_name = layer_name or layer
    gs_url = geoserver_url.rstrip("/")

    lyr = next((l for l in QgsProject.instance().mapLayers().values()
                if l.name() == layer), None)
    if lyr is None:
        raise ValueError(f"Layer not found: {layer}")

    is_raster = str(lyr.type()) == "Raster"
    publish_type = "raster" if is_raster or publish_as == "raster" else "vector"
    src_path = lyr.source()
    if not src_path or not os.path.isfile(src_path):
        raise FileNotFoundError(f"Layer source not found: {src_path}")

    tmp = tempfile.mkdtemp(prefix="gs_upload_")
    ext = ".tif" if publish_type == "raster" else ".gpkg"
    upload_path = os.path.join(tmp, layer_name + ext)

    if publish_type == "vector":
        subprocess.run(["ogr2ogr", "-overwrite", "-f", "GPKG", upload_path, src_path],
                       check=True, capture_output=True)
    else:
        import shutil
        shutil.copy2(src_path, upload_path)

    boundary = "----GeoServerBoundary7MA4YWxk"
    with open(upload_path, "rb") as f:
        payload = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(upload_path)}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()

    rest = f"/rest/workspaces/{workspace}/datastores/{layer_name}/file.{ext[1:]}"
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    req = urllib.request.Request(gs_url + rest, data=body, method="PUT")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status = resp.status
        ok, reason = True, ""
        print(f"Published: {status}")
    except Exception as exc:
        ok, reason = False, str(exc)
        print(f"Error: {exc}")

    result = {
        "published": ok, "layer": layer_name, "workspace": workspace,
        "type": publish_type, "geoserver_url": gs_url,
        "wfs_url": f"{gs_url}/{workspace}/wfs", "wms_url": f"{gs_url}/{workspace}/wms",
    }
    if reason:
        result["error"] = reason
    print(json.dumps(result))
    return result


def set_layer_style(layer: str, style: str, band: int = 1,
                    colormap: str = "viridis", min: float = None, max: float = None,
                    red: int = 1, green: int = 2, blue: int = 3,
                    column: str = "", classes: int = 5,
                    method: str = "jenks", color_ramp: str = "Reds",
                    legend_title: str = "", legend_expression: str = "",
                    iface=None) -> dict:
    """Apply visual styles to raster and vector layers.

    Args:
        layer: Layer name or ID.
        style: 'singleband', 'multiband', 'graduated', 'categorized', or 'paletted'.
        band: Band for singleband (default: 1).
        colormap: Colormap name (viridis, gray, rdylgn, spectral, terrain, etc.).
        min: Min pixel value for stretch (auto-detected if None).
        max: Max pixel value for stretch (auto-detected if None).
        red/green/blue: Band indices for multiband RGB.
        column: Attribute column for graduated/categorized style.
        classes: Number of class bins for graduated (default: 5).
        method: Classification method (jenks, equal, quantile, std).
        color_ramp: Named colour ramp (Reds, Blues, Spectral, Viridis, etc.).
        legend_title: Legend header text.
        legend_expression: Rule-based legend entries separated by '|'.
        iface: QGIS iface object (auto-injected by executor).

    Returns:
        Dict with styled layer info.
    """
    from qgis.core import (
        QgsProject, QgsColorRampShader, QgsRasterShader,
        QgsSingleBandPseudoColorRenderer, QgsMultiBandColorRenderer,
        QgsGraduatedSymbolRenderer, QgsCategorizedSymbolRenderer,
        QgsStyle, QgsClassificationJenks, QColor, Qgis,
    )

    proj = QgsProject.instance()
    lyr = next((l for l in proj.mapLayers().values() if l.name() == layer), None)
    if lyr is None:
        raise ValueError(f"Layer not found: {layer}")

    renderer = None

    if style == "singleband":
        prov = lyr.dataProvider()
        stats = None
        try:
            stats = prov.bandStatistics(band, Qgis.BandStatistics.All)
        except Exception:
            pass
        mn = float(min if min is not None else (stats.minimumValue if stats else 0))
        mx = float(max if max is not None else (stats.maximumValue if stats else 255))
        ramp = QgsColorRampShader()
        ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
        ramp.setColorRampItemList([
            QgsColorRampShader.ColorRampItem(mn, QColor("#313695"), str(mn)),
            QgsColorRampShader.ColorRampItem((mn + mx) / 3, QColor("#74add1"), "lo"),
            QgsColorRampShader.ColorRampItem((mn + mx) / 3 * 2, QColor("#ffffbf"), "mid"),
            QgsColorRampShader.ColorRampItem(mx, QColor("#d73027"), str(mx)),
        ])
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(ramp)
        renderer = QgsSingleBandPseudoColorRenderer(prov, band, shader)

    elif style == "multiband":
        renderer = QgsMultiBandColorRenderer(lyr.dataProvider(), red, green, blue)

    elif style == "graduated":
        fields = [f.name() for f in lyr.fields()]
        idx = fields.index(column) if column in fields else -1
        style_hnd = QgsStyle.defaultStyle()
        cr = style_hnd.colorRamp(color_ramp) or QgsStyle.defaultStyle().colorRamp("Reds")
        renderer = QgsGraduatedSymbolRenderer.createRenderer(lyr, idx, classes, cr, None)
        renderer.setClassificationMethod(QgsClassificationJenks())

    elif style == "categorized":
        fields = [f.name() for f in lyr.fields()]
        idx = fields.index(column) if column in fields else -1
        renderer = QgsCategorizedSymbolRenderer.createRenderer(lyr, idx, QgsStyle.defaultStyle())

    elif style == "paletted":
        renderer = lyr.renderer()

    if renderer:
        lyr.setRenderer(renderer)
    lyr.triggerRepaint()

    if legend_title:
        lyr.setName(legend_title)

    result = {
        "styled": layer,
        "style": style,
        "renderer": type(renderer).__name__ if renderer else "none",
    }
    print(json.dumps(result))
    return result


def multi_map_layout(layout_name: str, output_path: str,
                     paper_format: str = "A3", orientation: str = "landscape",
                     grid: str = "", panels: list = None,
                     margin_mm: float = 20, iface=None) -> dict:
    """Create a single print-layout PDF with multiple map panels arranged in a grid.

    Args:
        layout_name: Name for the new QgsPrintLayout.
        output_path: Full path to export (PDF).
        paper_format: Paper size (A2, A3, A4, Letter).
        orientation: 'portrait' or 'landscape'.
        grid: 'rows,cols' e.g. '2,2' (default: auto from panel count).
        panels: List of dicts with 'title', 'layer_set', 'extent'.
        margin_mm: Page margin in mm (default: 20).
        iface: QGIS iface object (auto-injected by executor).

    Returns:
        Dict with success status and output path.
    """
    from qgis.core import (
        QgsProject, QgsPrintLayout, QgsLayoutItemPage,
        QgsLayoutItemMap, QgsLayoutItemLabel, QgsLayoutExporter,
        QgsLayoutPoint, QgsLayoutSize, QgsRectangle, QFont,
    )
    from PyQt6.QtCore import QRectF

    panels = panels or []
    proj = QgsProject.instance()
    mgr = proj.layoutManager()

    # Remove existing layout with same name
    for i in range(mgr.printLayouts().count()):
        if mgr.printLayouts().at(i).name() == layout_name:
            mgr.removeLayout(mgr.printLayouts().at(i))

    layout = QgsPrintLayout(proj)
    page = layout.pageCollection().pages()[0]
    page.setPageSize(paper_format,
                     QgsLayoutItemPage.Orientation.Landscape if orientation == "landscape"
                     else QgsLayoutItemPage.Orientation.Portrait)

    usable_w = page.pageSize().width() - margin_mm * 2
    usable_h = page.pageSize().height() - margin_mm * 2

    n_panels = len(panels) if panels else 1
    if grid == "auto" or not grid:
        cols = math.ceil(math.sqrt(n_panels))
        rows = math.ceil(n_panels / cols)
    else:
        p = grid.split(",")
        rows = int(p[0]) if len(p) > 0 else math.ceil(math.sqrt(n_panels))
        cols = int(p[1]) if len(p) > 1 else rows

    gap = 20
    cell_w = (usable_w - gap * (cols - 1)) / cols
    cell_h = (usable_h - gap * (rows - 1)) / rows
    all_layers = {l.name(): l for l in proj.mapLayers().values() if l.isValid()}

    # Save original visibility state so we can restore it even on failure
    original_visibility = {name: lyr.isVisible() for name, lyr in all_layers.items()}
    try:
        for idx, pdef in enumerate(panels):
            row, col = idx // cols, idx % cols
            x = margin_mm + col * (cell_w + gap)
            y = margin_mm + row * (cell_h + gap)

            lset = pdef.get("layer_set", [])
            for lyr in all_layers.values():
                lyr.setVisible(False)
            for nm in lset:
                if nm in all_layers:
                    all_layers[nm].setVisible(True)

            map_itm = QgsLayoutItemMap(layout)
            map_itm.setRect(QRectF())
            map_itm.attemptMove(QgsLayoutPoint(x, y))
            map_itm.attemptResize(QgsLayoutSize(cell_w, cell_h))
            ext_str = pdef.get("extent")
            if ext_str:
                xy = [float(v) for v in ext_str.split(",")]
                map_itm.setExtent(QgsRectangle(xy[0], xy[2], xy[1], xy[3]))
            elif iface:
                map_itm.setExtent(iface.mapCanvas().extent())
            layout.addLayoutItem(map_itm)

            tt = pdef.get("title", "")
            if tt:
                lbl = QgsLayoutItemLabel(layout)
                lbl.setText(tt)
                lbl.setFont(QFont("Arial", 10, QFont.Weight.Bold))
                lbl.attemptMove(QgsLayoutPoint(x, y - 12))
                lbl.adjustSizeToText()
                layout.addLayoutItem(lbl)

        exporter = QgsLayoutExporter(layout)
        exported = exporter.exportToPdf(output_path, QgsLayoutExporter.PdfExportSettings())
        ok = exported == QgsLayoutExporter.ExportResult.Success
        print(f"Multi-map PDF: {ok} -> {output_path}")
        return {"success": ok, "output_path": output_path}
    finally:
        # Restore original layer visibility regardless of success/failure
        for name, visible in original_visibility.items():
            if name in all_layers:
                all_layers[name].setVisible(visible)


def save_map_theme(theme_name: str) -> dict:
    """Save the current QGIS map theme (layer visibility + renderer state).

    Args:
        theme_name: Name for the saved theme.

    Returns:
        Dict with saved theme name and all available themes.
    """
    from qgis.core import QgsProject, QgsMapThemeCollection

    proj = QgsProject.instance()
    mgr = proj.mapThemeCollection()
    theme_name = theme_name.strip()

    layer_records = [
        QgsMapThemeCollection.MapThemeLayerRecord(l)
        for l in proj.mapLayers().values() if l.isValid()
    ]

    try:
        mgr.addMapTheme(theme_name, layer_records)
    except Exception:
        pass

    proj.write()
    result = {"saved": theme_name, "themes": sorted(mgr.mapThemes())}
    print(json.dumps(result))
    return result


def load_map_theme(theme_name: str, refresh: bool = True, iface=None) -> dict:
    """Load a previously saved QGIS map theme.

    Args:
        theme_name: Name of the theme to restore.
        refresh: Redraw canvas after loading (default: True).
        iface: QGIS iface object (auto-injected by executor).

    Returns:
        Dict with loaded theme name and record count.
    """
    from qgis.core import QgsProject

    proj = QgsProject.instance()
    mgr = proj.mapThemeCollection()

    records = mgr.mapThemeRecords(theme_name)
    if not records:
        raise ValueError(f"Theme not found: {sorted(mgr.mapThemes())}")

    for rec in records:
        lyr = rec.layer()
        if lyr:
            lyr.setVisible(rec.isVisible())

    proj.write()

    if refresh and iface:
        iface.mapCanvas().refreshAllLayers()
        iface.mapCanvas().refresh()

    result = {"loaded": theme_name, "records": len(records)}
    print(json.dumps(result))
    return result


def _build_leaflet_html(layer_files: list, basemap: str = "osm",
                        include_search: bool = False, title: str = None,
                        bbox=None) -> str:
    """Build a self-contained Leaflet.js HTML string from layer file references."""
    _is_rect = hasattr(bbox, "center") and hasattr(bbox, "yMinimum")
    if _is_rect:
        center = [bbox.center().y(), bbox.center().x()]
        bounds = [[bbox.yMinimum(), bbox.xMinimum()], [bbox.yMaximum(), bbox.xMaximum()]]
    else:
        center, bounds = [0, 0], None

    basemap_urls = {
        "osm": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "topo": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "stamen_toner": "https://stamen-tiles-{s}.a.ssl.fastly.net/toner/{z}/{x}/{y}.png",
        "none": None,
    }
    bm_url = basemap_urls.get(basemap)
    bm_attr = (
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        if basemap == "osm"
        else '&copy; Esri'
        if basemap == "satellite"
        else ""
    )

    layer_js = []
    for lf in layer_files:
        f = lf.get("file", "")
        if f.endswith(".geojson"):
            layer_js.append(f'fetch("{f}").then(r=>r.json()).then(data=>L.geoJSON(data,{{}}).addTo(map))')
        elif f.endswith(".tif") or f.endswith(".tiff"):
            layer_js.append(f'L.imageOverlay("{f}", bounds).addTo(map)')

    search_block = ""
    if include_search:
        search_block = (
            '<div id="search" style="position:absolute;top:10px;left:60px;z-index:1000;">'
            '<input id="q" placeholder="Search location..." style="padding:4px 8px;width:200px;">'
            '<button onclick="doSearch()">Go</button></div>\n'
            '<script>\nfunction doSearch(){'
            'var q=document.getElementById("q").value;'
            'fetch("https://nominatim.openstreetmap.org/search?format=json&q="+encodeURIComponent(q))'
            '.then(r=>r.json()).then(d=>{if(d[0]){'
            'map.setView([d[0].lat,d[0].lon],12);'
            'L.marker([d[0].lat,d[0].lon]).addTo(map);}})}\n</script>'
        )

    bounds_js = f"var bounds={json.dumps(bounds)};" if bounds else ""
    center_js = f"var center={json.dumps(center)};"
    tile_js = f'L.tileLayer("{bm_url}", {{attribution: "{bm_attr}"}}).addTo(map);' if bm_url else ""
    layer_js_str = "\n    ".join(layer_js)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title or "QGIS Web Map"}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>#map{{height:600px;}}</style>
</head><body>
<h1>{title or "QGIS Web Map"}</h1>
{search_block}
<div id="map"></div>
<script>
{center_js}
{bounds_js}
var map = L.map('map');
{"map.fitBounds(bounds);" if bounds else "map.setView(center, 8);"}
{tile_js}
{layer_js_str}
</script></body></html>"""


def list_map_themes() -> dict:
    """List all saved map themes in the current QGIS project.

    Returns:
        Dict with list of theme names.
    """
    from qgis.core import QgsProject

    proj = QgsProject.instance()
    mgr = proj.mapThemeCollection()
    themes = sorted(mgr.mapThemes())
    result = {"themes": themes, "count": len(themes)}
    print(json.dumps(result))
    return result


def refresh_canvas(iface=None) -> dict:
    """Refresh the QGIS map canvas and all layers.

    Call after set_layer_style, set visibility toggles, layer removals,
    or any operation that changes the visual state.

    Args:
        iface: QGIS iface object (auto-injected by executor).

    Returns:
        Dict with refresh status.
    """
    if iface:
        iface.mapCanvas().refreshAllLayers()
        iface.mapCanvas().refresh()
    result = {"refreshed": True}
    print(json.dumps(result))
    return result


# Tool definitions for registration in tools.py
GEOSPATIAL_TOOLS = [
    {
        "name": "export_webmap",
        "description": (
            "Export the current QGIS project as an interactive web map using Leaflet.js. "
            "Serializes visible vector layers as GeoJSON and raster tiles as GeoTIFF, "
            "then builds a self-contained index.html with Leaflet.js. "
            "Output: index.html + data/ directory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "output_dir": {"type": "string", "description": "Full path to output directory (created if missing)"},
                "basemap": {"type": "string", "enum": ["osm", "satellite", "topo", "stamen_toner", "none"],
                            "description": "Basemap (default: osm)"},
                "extent": {"type": "string", "description": "Bbox override 'xmin,xmax,ymin,ymax' in project CRS"},
                "include_search": {"type": "boolean", "description": "Add geocoding search box (default: false)"},
                "title": {"type": "string", "description": "HTML page title (default: project name)"},
            },
            "required": ["output_dir"],
        },
        "execute": export_webmap,
    },
    {
        "name": "publish_geoserver",
        "description": (
            "Publish a vector or raster layer to a GeoServer REST endpoint. "
            "Exports to a temp file, uploads via multipart REST PUT, "
            "creates/updates the datastore and publishes the layer for WFS/WMS access."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "description": "QGIS layer name to publish"},
                "workspace": {"type": "string", "description": "GeoServer workspace name (default: 'default')"},
                "geoserver_url": {"type": "string", "description": "GeoServer base URL"},
                "layer_name": {"type": "string", "description": "GeoServer layer name (default: same as QGIS layer)"},
                "username": {"type": "string", "description": "GeoServer admin username"},
                "password": {"type": "string", "description": "GeoServer admin password"},
                "publish_as": {"type": "string", "enum": ["vector", "raster", "auto"],
                               "description": "'auto' (default), 'vector', or 'raster'"},
            },
            "required": ["layer", "geoserver_url", "username", "password"],
        },
        "execute": publish_geoserver,
    },
    {
        "name": "set_layer_style",
        "description": (
            "Apply visual styles (colormaps, RGB bands, graduated or categorized renderers) "
            "to raster and vector layers without writing raw QGIS Python."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "description": "Layer name or ID"},
                "style": {"type": "string", "enum": ["singleband", "multiband", "graduated", "categorized", "paletted"],
                          "description": "Style type"},
                "band": {"type": "number", "description": "Band for singleband (default: 1)"},
                "colormap": {"type": "string", "description": "Colormap: viridis, gray, rdylgn, spectral, terrain"},
                "min": {"type": "number", "description": "Min pixel value for stretch (auto-detected if omitted)"},
                "max": {"type": "number", "description": "Max pixel value for stretch (auto-detected if omitted)"},
                "red": {"type": "number", "description": "Red band index for multiband RGB"},
                "green": {"type": "number", "description": "Green band index for multiband RGB"},
                "blue": {"type": "number", "description": "Blue band index for multiband RGB"},
                "column": {"type": "string", "description": "Attribute column for graduated/categorized"},
                "classes": {"type": "number", "description": "Number of class bins for graduated (default: 5)"},
                "method": {"type": "string", "enum": ["jenks", "equal", "quantile", "std"],
                           "description": "Classification method (default: jenks)"},
                "color_ramp": {"type": "string", "description": "Named colour ramp (Reds, Blues, Spectral, Viridis)"},
                "legend_title": {"type": "string", "description": "Legend header text"},
                "legend_expression": {"type": "string", "description": "Rule-based legend entries separated by '|'"},
            },
            "required": ["layer", "style"],
        },
        "execute": set_layer_style,
    },
    {
        "name": "multi_map_layout",
        "description": (
            "Create a single print-layout PDF with multiple map panels arranged in a grid. "
            "Each panel shows its own layer set and optional extent. "
            "Best for before/after comparisons, multi-temporal overviews."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layout_name": {"type": "string", "description": "Name for the new QgsPrintLayout"},
                "output_path": {"type": "string", "description": "Full path to export (PDF)"},
                "paper_format": {"type": "string", "enum": ["A2", "A3", "A4", "Letter"],
                                 "description": "Default: A3"},
                "orientation": {"type": "string", "enum": ["portrait", "landscape"],
                                "description": "Default: landscape"},
                "grid": {"type": "string", "description": "'rows,cols' e.g. '2,2' (default: auto)"},
                "panels": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Panel label"},
                            "layer_set": {"type": "array", "items": {"type": "string"},
                                          "description": "Layer names to show"},
                            "extent": {"type": "string", "description": "'xmin,xmax,ymin,ymax' in project CRS"},
                        },
                    },
                    "description": "Panels to arrange in the layout",
                },
                "margin_mm": {"type": "number", "description": "Page margin in mm (default: 20)"},
            },
            "required": ["layout_name", "output_path"],
        },
        "execute": multi_map_layout,
    },
    {
        "name": "save_map_theme",
        "description": (
            "Save the current QGIS map theme (layer visibility + renderer state) under a name. "
            "Restore it later with load_map_theme to backtrack without rerunning code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "theme_name": {"type": "string", "description": "Name for the saved theme"},
            },
            "required": ["theme_name"],
        },
        "execute": save_map_theme,
    },
    {
        "name": "load_map_theme",
        "description": (
            "Load a previously saved QGIS map theme: sets layer visibility to the saved state. "
            "The fastest way to reset layer visibility without rerunning anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "theme_name": {"type": "string", "description": "Name of the theme to restore"},
                "refresh": {"type": "boolean", "description": "Redraw canvas after loading (default: true)"},
            },
            "required": ["theme_name"],
        },
        "execute": load_map_theme,
    },
    {
        "name": "list_map_themes",
        "description": (
            "List all saved map themes in the current QGIS project. "
            "Use before load_map_theme to see available themes."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "execute": list_map_themes,
        "execute": list_map_themes,
    },
    {
        "name": "refresh_canvas",
        "description": (
            "Refresh the QGIS map canvas and all layers. "
            "Call after set_layer_style, set visibility toggles, layer removals, "
            "or any operation that changes the visual state."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "execute": refresh_canvas,
    },
]
# ---------------------------------------------------------------------------
# Layer helpers (GeoLibre-style convenience helpers)
# ---------------------------------------------------------------------------
iface = None  # Set by plugin at runtime via set_iface()
def set_iface(qgis_iface):
    """Set the global QGIS interface handle."""
    global iface
    iface = qgis_iface
_GEOM_TYPE_MAP = {
    "point": "Point",
    "linestring": "LineString",
    "line": "LineString",
    "polygon": "Polygon",
    "multipoint": "MultiPoint",
    "multilinestring": "MultiLineString",
    "multipolygon": "MultiPolygon",
}
_QGIS_VARIANT_TYPES = {
    "int": "int",
    "integer": "int",
    "double": "double",
    "float": "double",
    "string": "string",
    "bool": "bool",
    "boolean": "bool",
    "date": "date",
    "datetime": "datetime",
}
def create_scratch_layer(
    geom_type: str,
    name: str = "",
    fields: list[tuple] | None = None,
    crs: str = "EPSG:4326",
) -> "QgsVectorLayer":
    """Create an in-memory scratch vector layer and add it to the project.
    Returns:
        The newly created (and project-added) QgsVectorLayer.
    Raises:
        RuntimeError: If the geometry type is invalid.
    """
    from qgis.core import QgsProject, QgsVectorLayer, QgsFields, QgsField
    from PyQt6.QtCore import QVariant
    gt = _GEOM_TYPE_MAP.get(geom_type.lower())
    if gt is None:
        raise RuntimeError(f"Invalid geometry type: {geom_type}")
    layer_name = name or gt.lower()
    layer = QgsVectorLayer(f"{gt}?crs={crs}", layer_name, "memory")
    if not layer.isValid():
        raise RuntimeError(f"Failed to create scratch layer: {gt}")
    prov = layer.dataProvider()
    field_defs = []
    # Always include a 'name' field by default
    if fields is None:
        field_defs.append(QgsField("name", QVariant.String))
    else:
        for fname, ftype in fields:
            qt_type_name = _QGIS_VARIANT_TYPES.get(str(ftype).lower(), "string")
            variant_type = getattr(QVariant, qt_type_name.capitalize(), QVariant.String)
            field_defs.append(QgsField(fname, variant_type))
    prov.addAttributes(field_defs)
    layer.updateFields()
    QgsProject.instance().addMapLayer(layer)
    return layer
def resolve_layer(layer_ref) -> "QgsVectorLayer | None":
    """Resolve a layer reference (object, id, or name) to a layer.
    Returns None if not found.
    """
    from qgis.core import QgsProject
    if layer_ref is None:
        return None
    # Already a layer object
    if hasattr(layer_ref, "id"):
        return layer_ref
    if isinstance(layer_ref, str):
        project = QgsProject.instance()
        # Try layer id first (most reliable)
        layer = project.mapLayer(layer_ref)
        if layer is not None:
            return layer
        # Try by name (layer name is user-visible, ids are internal)
        for lyr in project.mapLayers().values():
            if lyr.name() == layer_ref:
                return lyr
    return None
def load_layer(path: str) -> "QgsVectorLayer | None":
    """Load a vector layer from a file path (GeoJSON, Shapefile, GPKG, etc.).
    Returns the layer if valid, None otherwise.
    """
    from qgis.core import QgsProject, QgsVectorLayer
    layer = QgsVectorLayer(path, os.path.basename(path), "ogr")
    if layer.isValid():
        QgsProject.instance().addMapLayer(layer)
        return layer
    return None
def get_active_layer() -> "QgsVectorLayer | None":
    """Get the currently active layer from the QGIS interface.
    Returns None if no interface is available or no layer is active.
    """
    if iface is None:
        return None
    try:
        return iface.activeLayer()
    except Exception:
        return None
def reproject_layer(layer, crs: str, output_path: str | None = None) -> "QgsVectorLayer | str | None":
    """Reproject a layer to a different CRS.
    Uses native:reprojectlayer for vector layers and gdal:warpreproject for
    raster layers.
    Returns:
        Reprojected layer (vector) or output path (raster), or None on failure.
    """
    from qgis.core import QgsProject
    import processing
    params: dict = {}
    if layer.type() == 0:  # Vector
        params = {
            "INPUT": layer,
            "TARGET_CRS": crs,
            "OUTPUT": output_path or "memory:",
        }
        result = processing.run("native:reprojectlayer", params)
        output = result.get("OUTPUT")
        if isinstance(output, str):
            return output
        if hasattr(output, "isValid"):
            QgsProject.instance().addMapLayer(output)
        return output
    elif layer.type() == 1:  # Raster
        params = {
            "INPUT": layer,
            "TARGET_CRS": crs,
            "OUTPUT": output_path or "TEMPORARY_OUTPUT",
        }
        result = processing.run("gdal:warpreproject", params)
        output = result.get("OUTPUT")
        if isinstance(output, str):
            return output
        if hasattr(output, "isValid"):
            QgsProject.instance().addMapLayer(output)
        return output
    return None
def set_layer_style_simple(
    layer,
    style: str = "single",
    color_or_ramp: str | list = "#ff0000",
    **kwargs,
) -> bool:
    """Apply a simple style to a layer.
    Args:
        layer: QgsVectorLayer or QgsRasterLayer.
        style: "single", "categorized", "graduated", "rule", "heatmap".
        color_or_ramp: Single color hex for "single", or field name(s) for
                       categorized/graduated.
        **kwargs: Additional style parameters (e.g., field, classes, opacity).
    Returns:
        True if styling was applied successfully.
    """
    from qgis.core import (
        QgsFillSymbol,
        QgsLineSymbol,
        QgsMarkerSymbol,
        QgsSingleSymbolRenderer,
        QgsCategorizedSymbolRenderer,
        QgsGraduatedSymbolRenderer,
        QgsRendererCategory,
        QgsStyle,
    )
    try:
        if style == "single":
            if hasattr(layer, "geometryType"):
                gt = layer.geometryType()
                if gt == 0:  # Point
                    symbol = QgsMarkerSymbol.createSimple({"color": color_or_ramp, "size": "3"})
                elif gt == 1:  # Line
                    symbol = QgsLineSymbol.createSimple({"color": color_or_ramp, "width": "1"})
                elif gt == 2:  # Polygon
                    symbol = QgsFillSymbol.createSimple({"color": color_or_ramp, "outline_color": "#000000"})
                else:
                    symbol = QgsFillSymbol.createSimple({"color": color_or_ramp})
            else:
                symbol = QgsFillSymbol.createSimple({"color": color_or_ramp})
            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)
        elif style == "categorized":
            field = kwargs.get("field", "")
            if not field:
                return False
            categories = []
            if isinstance(color_or_ramp, list):
                values = color_or_ramp
            else:
                values = [layer.uniqueValues(layer.fields().lookupField(field))]
            if isinstance(values, list) and len(values) > 0 and isinstance(values[0], list):
                values = values[0]
            symbol = QgsFillSymbol.createSimple({"color": "#ff0000"})
            for i, val in enumerate(values):
                category = QgsRendererCategory(val, symbol, str(val))
                categories.append(category)
            renderer = QgsCategorizedSymbolRenderer(categories, layer.fields().lookupField(field))
            layer.setRenderer(renderer)
        elif style == "graduated":
            field = kwargs.get("field", "")
            classes = kwargs.get("classes", 5)
            if not field:
                return False
            renderer = QgsGraduatedSymbolRenderer.createRenderer(
                layer, field, classes, QgsGraduatedSymbolRenderer.Mode(0),
            )
            layer.setRenderer(renderer)
        layer.triggerRepaint()
        return True
    except Exception:
        return False