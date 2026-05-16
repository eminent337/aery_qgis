"""JSON-RPC bridge between QGIS plugin and Aery agent subprocess."""

import json
import os
import signal
import subprocess
import tempfile
import threading
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_aery_binary() -> str:
    """Find the Aery binary.

    Priority:
    1. Bundled binary next to this plugin file
    2. System `aery` command (as fallback, user must have Node.js)
    """
    bundled = os.path.join(PLUGIN_DIR, "bin", "aery-qgis-runner")
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return bundled
    return "aery"


def _get_agent_dir() -> str:
    """Return the plugin-local Aery agent directory.

    This isolates the QGIS plugin's Aery config from the main
    ~/.aery/agent/ installation, so changing provider/model in
    the plugin does not affect the CLI Aery and vice versa.
    """
    return os.path.join(PLUGIN_DIR, "agent")


def _ensure_agent_dir() -> str:
    """Create the plugin-local agent dir and seed auth.json from ~/.aery/ if present."""
    agent_dir = _get_agent_dir()
    os.makedirs(agent_dir, exist_ok=True)

    # Copy auth.json from main Aery if it exists (shared credentials)
    auth_src = os.path.expanduser("~/.aery/agent/auth.json")
    auth_dst = os.path.join(agent_dir, "auth.json")
    if os.path.isfile(auth_src) and not os.path.isfile(auth_dst):
        try:
            with open(auth_src) as f:
                auth_data = json.load(f)
            with open(auth_dst, "w") as f:
                json.dump(auth_data, f)
        except (json.JSONDecodeError, IOError):
            pass

    # Write a clean QGIS-only settings.json (no default model — runner provides it)
    settings_path = os.path.join(agent_dir, "settings.json")
    if not os.path.isfile(settings_path):
        # Pick the first configured provider from auth.json, fall back to aery-gateway
        auth_path = os.path.join(agent_dir, "auth.json")
        default_provider = "aery-gateway"
        default_model = "anthropic/claude-haiku-4-5-20251001"
        if os.path.isfile(auth_path):
            try:
                with open(auth_path) as f:
                    auth_data = json.load(f)
                for pid, entry in auth_data.items():
                    if isinstance(entry, dict) and (entry.get("key") or entry.get("access")):
                        default_provider = pid
                        # Pick first model from known providers
                        from aery_plugin import oauth_helper as _oh
                        cfg = _oh.API_PROVIDERS.get(pid) or {}
                        models = cfg.get("models", [])
                        if models:
                            default_model = models[0][0]
                        break
            except Exception:
                pass
        qgis_settings = {
            "quietStartup": True,
            "defaultProvider": default_provider,
            "defaultModel": default_model,
            "defaultThinkingLevel": "off",
        }
        with open(settings_path, "w") as f:
            json.dump(qgis_settings, f, indent=2)

    # Clean up stale auth.json.tmp left by a crashed Aery binary
    tmp_path = os.path.join(agent_dir, "auth.json.tmp")
    if os.path.exists(tmp_path):
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return agent_dir


class RPCBridge(QObject):
    """Manages Aery subprocess and JSON-RPC communication.

    Spawns the Aery standalone binary with built-in QGIS tools.
    Provider config is passed via --provider-file (temp JSON file).
    The binary reads and deletes the file on startup.
    """

    event_received = pyqtSignal(dict)
    response_received = pyqtSignal(str, dict)
    error_occurred = pyqtSignal(str)
    process_exited = pyqtSignal(int)
    disconnected = pyqtSignal()

    def __init__(
        self,
        cwd: str,
        port: int,
        provider_config: Optional[dict] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._cwd = cwd
        self._port = port
        self._provider_config = provider_config or {}
        self._provider_file: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._running = False
        self._intentional_shutdown = False
        self._pending_responses: dict[str, Callable] = {}

    @staticmethod
    def _get_resources_dir() -> str:
        """Return path to the plugin's resources/ directory."""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")

    def _load_rules(self) -> dict:
        """Load geospatial rules from resources/geospatial_rules.json."""
        rules_path = os.path.join(self._get_resources_dir(), "geospatial_rules.json")
        with open(rules_path) as f:
            return json.load(f)

    def _load_qgis_system_prompt(self) -> str:
        """Build the geospatial system prompt from the shared JSON rulebook."""
        rules = self._load_rules()

        # System prompt built from JSON (Python bridge side)
        # Advanced tooling sections (SAR, GEE, LIDAR, network, ML) are appended here
        # from code because they are Python/QGIS API usage examples not needed on the TS side.
        advanced_sections = """=== RASTER ANALYSIS ===
# Read raster band stats:
stats = layer.dataProvider().bandStatistics(1, QgsRasterBandStats.All)
result = {'min': stats.minimumValue, 'max': stats.maximumValue, 'mean': stats.mean, 'std': stats.stdDev}
# Rasterio for advanced raster work:
import rasterio; from rasterio.warp import reproject, Resampling
with rasterio.open(path) as src: data = src.read(1); transform = src.transform; crs = src.crs
# NDVI from Sentinel-2 bands:
ndvi = (nir.astype(float) - red.astype(float)) / (nir + red + 1e-10)

=== VECTOR DATA MANIPULATION ===
# Edit features in code:
layer.startEditing()
for feat in layer.getFeatures(QgsFeatureRequest().setFilterExpression('pop > 1000')):
    layer.changeAttributeValue(feat.id(), layer.fields().indexOf('category'), 'urban')
layer.commitChanges()
# Add new field:
layer.dataProvider().addAttributes([QgsField('score', QVariant.Double)]); layer.updateFields()
# Spatial index for fast queries:
idx = QgsSpatialIndex(layer.getFeatures()); nearby = idx.nearestNeighbor(QgsPointXY(x, y), 5)
# Distance calculations:
da = QgsDistanceArea(); da.setEllipsoid('WGS84')
dist_m = da.measureLine(QgsPointXY(lon1,lat1), QgsPointXY(lon2,lat2))

=== WEB DATA FETCHING ===
# Download OSM data via Overpass:
import urllib.request, json
query = '[out:json];node[amenity=hospital](bbox);out;'
url = f'https://overpass-api.de/api/interpreter?data={urllib.parse.quote(query)}'
with urllib.request.urlopen(url, timeout=30) as r: data = json.loads(r.read())
# Download file:
urllib.request.urlretrieve(url, f'{project_dir}/data.gpkg')
# WFS layer:
uri = QgsDataSourceUri(); uri.setParam('url', wfs_url); uri.setParam('typename', layer_name)
layer = QgsVectorLayer(uri.uri(), 'wfs_layer', 'WFS')
# WMS/WMTS:
layer = QgsRasterLayer('crs=EPSG:4326&format=image/png&layers=layer_name&styles=&url=https://...', 'wms', 'wms')

=== MACHINE LEARNING IN QGIS ===
# Land cover classification with sklearn:
import numpy as np; from sklearn.ensemble import RandomForestClassifier
# Extract training samples from raster at point locations
# Build feature matrix from band values, train RF, predict on full raster
# Write classified raster back with rasterio
# Clustering (unsupervised):
from sklearn.cluster import KMeans
km = KMeans(n_clusters=5); labels = km.fit_predict(X)
# Object-based image analysis: segment raster -> extract stats -> classify

=== NETWORK ANALYSIS ===
# Road network shortest path:
result = processing.run('native:shortestpathpointtopoint', {'INPUT': road_layer, 'STRATEGY': 0, 'START_POINT': 'x1,y1 [EPSG:4326]', 'END_POINT': 'x2,y2 [EPSG:4326]', 'OUTPUT': 'TEMPORARY_OUTPUT'})
# Service area (isochrone):
result = processing.run('native:serviceareafrompoint', {'INPUT': road_layer, 'STRATEGY': 1, 'START_POINT': 'x,y [EPSG:4326]', 'TRAVEL_COST2': 600, 'OUTPUT': 'TEMPORARY_OUTPUT'})
# NetworkX for custom graph analysis:
import networkx as nx; G = nx.Graph()
for feat in road_layer.getFeatures(): G.add_edge(feat['from_node'], feat['to_node'], weight=feat['length'])
path = nx.shortest_path(G, source, target, weight='weight')

=== 3D AND TERRAIN ===
# Hillshade:
processing.run('qgis:hillshade', {'INPUT': dem, 'Z_FACTOR': 1.5, 'AZIMUTH': 315, 'V_ANGLE': 45, 'OUTPUT': f'{project_dir}/hillshade.tif'})
# Slope/aspect:
processing.run('native:slope', {'INPUT': dem, 'Z_FACTOR': 1.0, 'OUTPUT': f'{project_dir}/slope.tif'})
# Contours:
processing.run('gdal:contour', {'INPUT': dem, 'INTERVAL': 50, 'OUTPUT': f'{project_dir}/contours.gpkg'})
# Profile along line: extract raster values along a line geometry
processing.run('native:setzfromraster', {'INPUT': line_layer, 'RASTER': dem, 'BAND': 1, 'OUTPUT': 'TEMPORARY_OUTPUT'})

=== DISPLAY ON CANVAS (ALWAYS DO THIS AFTER PRODUCING OUTPUT) ===
# Load any raster result to canvas:
layer = QgsRasterLayer(output_path, 'result_name')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().setExtent(layer.extent()); iface.mapCanvas().refresh()
# Load vector result to canvas:
layer = QgsVectorLayer(output_path, 'result_name', 'ogr')
QgsProject.instance().addMapLayer(layer)
# Apply pseudocolor ramp to raster (NDVI, elevation, etc.):
from qgis.core import QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
shader = QgsRasterShader(); color_ramp = QgsColorRampShader()
color_ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
color_ramp.setColorRampItemList([
    QgsColorRampShader.ColorRampItem(-1, QColor('#d73027'), '-1'),
    QgsColorRampShader.ColorRampItem(0, QColor('#fee08b'), '0'),
    QgsColorRampShader.ColorRampItem(1, QColor('#1a9850'), '1'),
])

=== WEB MAP EXPORT ===
# Interactive Leaflet web map ---
export_webmap(output_dir='./webmap', basemap='osm', include_search=False, title='My Map')
# Returns index.html + data/*.geojson — upload both to any web host
# Output: single HTML page, no server-side code needed

=== GEOSERVER PUBLISHING ===
# Publish layer to GeoServer via REST ---
# Requires: running GeoServer, ogr2ogr, admin credentials
publish_geoserver(layer='roads', geoserver_url='http://localhost:8080/geoserver',
                  username='admin', password='geoserver', workspace='my_workspace',
                  layer_name='roads_ws')
# Vector: exported as GeoPackage, published via REST datastore
# Raster: exported as GeoTIFF
# WFS: geoserver_url/workspace/wfs  |  WMS: geoserver_url/workspace/wms

=== STYLE & VISUAL STATE ===
# Style before exporting ---
set_layer_style(layer='ndvi', style='singleband', colormap='RdYlGn', band=1,
                min=-1, max=1, legend_title='NDVI')
# gradient NDVI ramp, legend title
# Graduated choropleth:
set_layer_style(layer='population', style='graduated', column='pop',
                classes=5, method='jenks', color_ramp='Reds')
# Freeze visual state:
save_map_theme(theme_name='ndvi_view')
# Multi-panel PDF layout:
multi_map_layout(layout_name='comparison', output_path='/path/multi.pdf',
                 paper_format='A3', orientation='landscape',
                 grid='2,2',
                 panels=[{title:'Before', layer_set:['layer1'], extent:'auto'},
                         {title:'After', layer_set:['layer2'], extent:'auto'}])
"""

        lines = []
        lines += [
            f"You are {rules['identity']['role']}.",
            f"You can do anything: {rules['identity']['capabilities']}.",
            f"Workflow: {rules['identity']['workflow']}",
            "",
            "=== QGIS WORKFLOW ===",
        ]
        lines += list(rules.get("workflow_steps", []))
        lines += ["", "=== PROCESSING SEARCH FILTER ===", rules.get("processing_search_filter", "")]
        lines += ["", "=== GLOBALS ALWAYS AVAILABLE IN run_qgis_code ==="]
        lines += list(rules.get("globals_available", []))
        lines += [rules.get("globals_note", ""), "", "=== CRS RULES ==="]
        lines += list(rules.get("crs_rules", []))
        lines += ["", "=== SAFETY RULES ==="]
        lines += list(rules.get("safety_rules", []))
        lines += ["", "=== PROCESSING PATTERNS ==="]
        for k, v in rules.get("processing_patterns", {}).items():
            lines.append(f"# {k}:\n{v}")
        lines += ["", "=== STYLING IN CODE ==="]
        for k, v in rules.get("styling_code", {}).items():
            lines.append(f"# {k}:\n{v}")
        lines += ["", "=== ERROR RECOVERY ==="]
        lines += list(rules.get("error_recovery", []))
        core = "\n".join(lines) + "\n"
        return core + advanced_sections

    def _write_provider_file(self) -> Optional[str]:
        """Write provider config to a temp file. Returns the file path."""
        if not self._provider_config:
            return None
        fd, path = tempfile.mkstemp(prefix="aery_provider_", suffix=".json")
        os.close(fd)
        with open(path, "w") as f:
            json.dump(self._provider_config, f)
        os.chmod(path, 0o600)  # Secure: only owner can read
        return path

    def spawn(self) -> None:
        """Start the Aery binary in RPC mode.

        Uses: aery-qgis-runner <executor-port>
        Provider/auth config is resolved from the isolated agent dir
        via AERY_CODING_AGENT_DIR env var (see _ensure_agent_dir).
        """
        binary = _find_aery_binary()
        is_bundled = binary != "aery"

        # Write provider config to temp file (binary reads and deletes it)
        self._provider_file = self._write_provider_file()

        try:
            if is_bundled:
                cmd = [binary, str(self._port)]
                if self._provider_file:
                    cmd.extend(["--provider-file", self._provider_file])
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self._cwd,
                    text=True,
                )
            else:
                self._process = subprocess.Popen(
                    ["aery", "--mode", "rpc"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self._cwd,
                    text=True,
                )
            self._running = True

            # Reader thread for stdout
            self._reader_thread = threading.Thread(
                target=self._read_stdout, daemon=True
            )
            self._reader_thread.start()

            # Stderr reader
            self._stderr_thread = threading.Thread(
                target=self._read_stderr, daemon=True
            )
            self._stderr_thread.start()

        except FileNotFoundError:
            if not is_bundled:
                self.error_occurred.emit(
                    "Bundled binary not found. Build it first:\n"
                    "  cd runner && bun build --compile --target=bun-linux-x64-modern "
                    "--outfile=../aery_plugin/bin/aery-qgis-runner entry.ts"
                )
            else:
                self.error_occurred.emit(f"Binary not found at: {binary}")
        except Exception as e:
            self.error_occurred.emit(f"Failed to start Aery: {e}")

    def _handle_disconnect(self):
        """Clean up after the runner process dies unexpectedly.

        Sets _running = False, closes streams, emits disconnected signal.
        Called when BrokenPipeError or empty stdout/stderr indicate the
        subprocess has exited.
        """
        if not self._running:
            return
        self._running = False
        # Close streams to unblock reader threads
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(self._process, stream_name, None) if self._process else None
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                pass
        self.disconnected.emit()

    def send_command(self, command: dict[str, Any], callback: Optional[Callable] = None):
        """Send a JSON-RPC command to Aery via stdin."""
        if not self._process or not self._running:
            self.error_occurred.emit("Aery is not running")
            return

        cmd_id = command.get("id")
        if callback and cmd_id:
            self._pending_responses[cmd_id] = callback

        try:
            self._process.stdin.write(json.dumps(command) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self._handle_disconnect()
            self.error_occurred.emit(f"Connection lost: {e}")

    def prompt(self, message: str):
        """Send a prompt command to the agent."""
        self.send_command({"type": "prompt", "message": message})

    def follow_up(self, message: str):
        """Queue a follow-up prompt behind the current running turn."""
        self.send_command({"type": "follow_up", "message": message})

    def abort(self):
        """Abort the current agent operation completely.

        Sends abort + abort_bash + abort_retry to ensure the agent
        stops processing AND kills any running shell child processes.
        """
        self.send_command({"type": "abort"})
        self.send_command({"type": "abort_bash"})
        self.send_command({"type": "abort_retry"})

    def _read_stdout(self):
        """Read JSON lines from Aery's stdout."""
        while self._running and self._process:
            try:
                line = self._process.stdout.readline()
                if not line:
                    break

                data = json.loads(line.strip())
                self._dispatch_event(data)

            except json.JSONDecodeError:
                continue
            except Exception as e:
                if self._running:
                    self.error_occurred.emit(f"RPC error: {e}")
                break

        if self._process and not self._intentional_shutdown:
            exit_code = self._process.poll()
            self.process_exited.emit(exit_code if exit_code is not None else 1)

    def _read_stderr(self):
        """Read and log Aery's stderr to QgsMessageLog."""
        try:
            from qgis.core import QgsMessageLog, Qgis
            def _log(msg):
                QgsMessageLog.logMessage(msg, "Aery", Qgis.MessageLevel.Warning)
        except ImportError:
            def _log(msg):
                pass  # not in QGIS context (tests)

        while self._running and self._process:
            try:
                line = self._process.stderr.readline()
                if not line:
                    break
                _log(line.strip())
            except Exception:
                break

    def _dispatch_event(self, data: dict[str, Any]):
        """Route an RPC event to the appropriate handler, filtering out thinking blocks."""
        event_type = data.get("type")

        # --- Filter thinking/delta events at source ---
        if event_type in ("thinking_start", "thinking_delta", "thinking_end"):
            return
        if event_type == "message_update":
            # Filter assistantMessageEvent thinking sub-events
            ame = data.get("assistantMessageEvent", {})
            ame_type = ame.get("type", "")
            if ame_type in ("thinking_start", "thinking_delta", "thinking_end"):
                return
            # Strip thinking blocks from partial content
            if "partial" in data and isinstance(data["partial"], dict):
                pcontent = data["partial"].get("content", [])
                if isinstance(pcontent, list):
                    data["partial"]["content"] = [
                        b for b in pcontent if b.get("type") != "thinking"
                    ]

        # --- Strip thinking blocks from message content ---
        for key in ("message", "partial"):
            msg = data.get(key)
            if isinstance(msg, dict):
                content = msg.get("content", [])
                if isinstance(content, list):
                    msg["content"] = [
                        b for b in content if b.get("type") != "thinking"
                    ]

        if event_type == "response":
            cmd = data.get("command", "")
            cmd_id = data.get("id")
            if cmd_id and cmd_id in self._pending_responses:
                self._pending_responses[cmd_id](data)
                del self._pending_responses[cmd_id]
            self.response_received.emit(cmd, data)
        else:
            # Streaming events (message_start, tool_execution, etc.)
            self.event_received.emit(data)

    def shutdown(self):
        """Terminate the Aery subprocess and suppress normal disconnect UI events."""
        self._intentional_shutdown = True
        self._running = False
        process = self._process
        self._process = None
        if process:
            for stream_name in ("stdin", "stdout", "stderr"):
                stream = getattr(process, stream_name, None)
                try:
                    if stream:
                        stream.close()
                except Exception:
                    pass
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=0.75)
                    except subprocess.TimeoutExpired:
                        process.kill()
            except Exception:
                pass
        self._pending_responses.clear()
        # Clean up stale provider temp file (compound-risk: binary can crash before
        # reading it, leaving the /tmp file behind; also a dangling reference here)
        if self._provider_file and os.path.exists(self._provider_file):
            try:
                os.unlink(self._provider_file)
            except Exception:
                pass
            finally:
                self._provider_file = None
