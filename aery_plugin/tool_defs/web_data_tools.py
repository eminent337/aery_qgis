"""Web-data tools for the Aery QGIS agent.

All network-facing templates validate their URL with `_aery_ssrf_guard`
BEFORE any fetch and (where a response object is available) re-check the
final URL after redirects. The guard is pure string/regex validation
so it runs inside the run_qgis_code sandbox, which blocks the
`socket` / `ipaddress` / `urllib` *imports* (those reach the
network only through restricted proxies). It blocks:
  - non-http(s) schemes (file://, ftp://, gopher://, ...)
  - private / loopback / link-local / reserved IP literals
    (127.0.0.1, 10.x, 192.168.x, 172.16-31.x,
     169.254.x, ::1, fc00::/fd00::, 0.0.0.0)
  - obvious private hostnames (localhost, *.local, *.internal, metadata)
Redirects are followed by urllib by default, so the post-fetch
re-check on `resp.geturl()` catches an attacker-supplied
`http://example.com` that 302s to `http://169.254.169.254/`.
"""

# Shared SSRF guard, injected into every network template below.
_SSRF_GUARD = '''
import re as _ssrf_re
def _aery_ssrf_guard(_u):
    """Raise ValueError if _u is not a safe http(s) public URL."""
    if not isinstance(_u, str) or not _u.strip():
        raise ValueError("Refusing to fetch empty URL")
    _u = _u.strip()
    # Scheme check (case-insensitive).
    _m = _ssrf_re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", _u)
    _scheme = (_m.group(1).lower() if _m else "")
    if _scheme not in ("http", "https"):
        raise ValueError(
            "Refusing non-http(s) URL scheme '%s' (only http/https allowed)" % _scheme)
    # Strip scheme + leading slashes, then pull the host from the authority.
    _rest = _u.split(":", 1)[1].lstrip("/")
    _auth = _rest.split("/", 1)[0]
    _host = _auth.split("@", 1)[-1].split(":")[0].strip("[]").lower()
    if not _host:
        raise ValueError("Refusing URL with no host")
    # Private hostnames.
    if _host in ("localhost", "metadata", "metadata.google.internal"):
        raise ValueError("Refusing private hostname '%s'" % _host)
    if _host.endswith(".localhost") or _host.endswith(".local") \\
       or _host.endswith(".internal") or _host.endswith(".intranet"):
        raise ValueError("Refusing private hostname '%s'" % _host)
    # IP literals (v4 and v6) - block private/loopback/link-local/reserved.
    _ip = _host
    try:
        _oct = [int(_p) for _p in _ip.split(".")]
    except ValueError:
        _oct = None
    if _oct is not None and len(_oct) == 4 and all(0 <= _o <= 255 for _o in _oct):
        _a, _b = _oct[0], _oct[1]
        if _a == 0:                                     # 0.0.0.0/8
            raise ValueError("Refusing private IP %s" % _ip)
        if _a == 10:                                    # 10.0.0.0/8
            raise ValueError("Refusing private IP %s" % _ip)
        if _a == 127:                                   # 127.0.0.0/8 loopback
            raise ValueError("Refusing loopback IP %s" % _ip)
        if _a == 169 and _b == 254:                     # 169.254.0.0/16 link-local
            raise ValueError("Refusing link-local IP %s" % _ip)
        if _a == 192 and _b == 168:                     # 192.168.0.0/16
            raise ValueError("Refusing private IP %s" % _ip)
        if _a == 172 and 16 <= _b <= 31:                # 172.16.0.0/12
            raise ValueError("Refusing private IP %s" % _ip)
        if 100 == _a and 64 <= _b <= 127:               # 100.64.0.0/10 CGNAT
            raise ValueError("Refusing shared CGNAT IP %s" % _ip)
    if _host.startswith("::ffff:") or _host.startswith("::"):  # v6 loopback/unspecified
        raise ValueError("Refusing loopback/unspecified IPv6 %s" % _host)
    if _host.startswith("fc") or _host.startswith("fd"):         # v6 unique-local
        raise ValueError("Refusing private IPv6 %s" % _host)
'''

TOOLS = [
    {
        "name": "fetch_osm_data",
        "description": "Download OSM features via Overpass API with spatial filtering and bbox",
        "parameters": {
            "type": "object",
            "properties": {
                "feature_type": {"type": "string", "description": "OSM feature type, e.g. 'way[building]', 'node[amenity=restaurant]'"},
                "bbox": {"type": "array", "items": {"type": "number"}, "description": "Bounding box [minlon, minlat, maxlon, maxlat]"},
                "output_name": {"type": "string", "description": "Output layer name"}
            },
            "required": ["feature_type", "bbox", "output_name"]
        },
        "code": _SSRF_GUARD + '''
from qgis.core import QgsVectorLayer, QgsField, QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsCoordinateReferenceSystem
from PyQt6.QtCore import QVariant
import urllib.request, urllib.parse, json

feature_type = {feature_type}
bbox = {bbox}
output_name = {output_name}

_aery_ssrf_guard('https://overpass-api.de/api/interpreter')
query = f'[out:json];({feature_type}({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});out geom;);'
data = urllib.parse.urlencode({'data': query}).encode()
req = urllib.request.Request('https://overpass-api.de/api/interpreter', data=data)
with urllib.request.urlopen(req, timeout=60) as resp:
    result_json = json.loads(resp.read().decode())

uri = 'Point?crs=EPSG:4326'
mem_layer = QgsVectorLayer(uri, output_name, 'memory')
dp = mem_layer.dataProvider()
dp.addAttributes([QgsField('osm_id', QVariant.String), QgsField('tags', QVariant.String)])
mem_layer.updateFields()

elements = result_json.get('elements', [])
for element in elements:
    feat = QgsFeature(mem_layer.fields())
    if element['type'] == 'node':
        geom = QgsGeometry.fromPointXY(QgsPointXY(element['lon'], element['lat']))
    elif 'center' in element:
        geom = QgsGeometry.fromPointXY(QgsPointXY(element['center']['lon'], element['center']['lat']))
    else:
        continue
    feat.setGeometry(geom)
    feat.setAttributes([str(element.get('id', '')), json.dumps(element.get('tags', {}))])
    dp.addFeature(feat)

mem_layer.updateExtents()
if not mem_layer.isValid():
    raise RuntimeError('Failed to create OSM data layer')
QgsProject.instance().addMapLayer(mem_layer)
iface.mapCanvas().refresh()
result = f'Added OSM layer {output_name} with {len(elements)} features'
'''
    },
    {
        "name": "fetch_wfs_layer",
        "description": "Load a WFS layer from a WFS server by URL and type name",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "WFS server URL"},
                "type_name": {"type": "string", "description": "WFS layer type name"},
                "output_name": {"type": "string", "description": "Output layer name"}
            },
            "required": ["url", "type_name", "output_name"]
        },
        "code": _SSRF_GUARD + '''
from qgis.core import QgsVectorLayer, QgsProject

url = {url}
type_name = {type_name}
output_name = {output_name}

_aery_ssrf_guard(url)
uri = f'url={url}&typename={type_name}'
layer = QgsVectorLayer(uri, output_name, 'WFS')
if not layer.isValid():
    raise RuntimeError(f'Failed to load WFS layer from {url}')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
result = f'Added WFS layer {output_name}'
'''
    },
    {
        "name": "fetch_wms_layer",
        "description": "CRITICAL: DO NOT USE THIS TOOL FOR BASEMAPS (e.g. ESRI World Imagery, Google Satellite, OSM)! If you need a satellite or map background, you MUST use the `load_basemap` tool instead! Only use this tool for specific scientific WMS layers (like NASA weather or specific government servers). Add a WMS/WMTS basemap from a WMS server. Use 'wms' provider. Always call capture_canvas after to verify.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "WMS server URL"},
                "layers": {"type": "string", "description": "WMS layer name(s) comma-separated"},
                "output_name": {"type": "string", "description": "Output layer name"}
            },
            "required": ["url", "layers", "output_name"]
        },
        "code": _SSRF_GUARD + '''
from qgis.core import QgsRasterLayer, QgsProject

_wms_url = {url}
_wms_layers = {layers}
_wms_output = {output_name}

_aery_ssrf_guard(_wms_url)
uri = 'crs=EPSG:3857&format=image/png&layers=' + _wms_layers + '&styles=&url=' + _wms_url
layer = QgsRasterLayer(uri, _wms_output, 'wms')
if not layer.isValid():
    raise RuntimeError('Failed to load WMS layer from ' + _wms_url)
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
result = 'Added WMS layer ' + _wms_output
'''
    },
]
