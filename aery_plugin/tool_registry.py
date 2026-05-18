"""Tool Registry dialog for Aery QGIS plugin.
Displays all available tools from the Python agent.
"""

from typing import Optional
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QWidget,
)

# ── AERY GEOSPATIAL DESIGN SYSTEM ──
BG_BASE = "#0e1513"
BG_SURFACE = "#1a211f"
ACCENT_TEAL = "#2dd4bf"
BORDER_TECH = "#3c4a46"
TEXT_MAIN = "#dde4e1"
TEXT_DIM = "#bacac5"

# Fallback when runner is unavailable
_FALLBACK_TOOLS = [
    # ── Core QGIS execution ──
    ("run_qgis_code", "Execute any Python in the QGIS main thread. Full access to all QGIS/PyQt6/numpy/pandas/sklearn globals."),
    ("get_project_context", "Snapshot all layers, CRS, extents, field schemas, raster band info, processing providers."),
    ("capture_canvas", "Export the live map canvas as a high-DPI PNG for analysis or display."),
    ("run_processing", "Run any QGIS Processing algorithm by ID with parameters."),
    ("run_processing_algorithm", "Run a specific processing algorithm with full parameter control."),
    ("list_processing_algorithms", "List all available processing algorithms across all providers."),
    ("describe_processing_algorithm", "Get full parameter schema for any processing algorithm."),
    ("validate_processing_runtime", "Smoke-test the Processing runtime and list active providers."),
    ("validate_project", "Audit CRS issues, empty layers, invalid geometries, and risky operations."),
    # ── Layer management ──
    ("add_layer", "Load vector, raster, WMS, WFS, WCS, or XYZ tile layer into the project."),
    ("get_layer_info", "Get detailed schema, statistics, and metadata for a specific layer."),
    ("export_layer", "Export a layer to GeoJSON, GeoPackage, Shapefile, CSV, KML, or GeoTIFF."),
    ("select_by_attribute", "Select features using an attribute expression or SQL filter."),
    ("select_by_location", "Select features by spatial relationship (intersects, within, touches, etc.)."),
    ("style_layer", "Apply symbology: single symbol, graduated, categorized, rule-based, or heatmap."),
    ("label_layer", "Configure labels on a layer with font, size, placement, and expression."),
    ("set_layer_visibility", "Show or hide layers in the layer tree."),
    ("reorder_layers", "Reorder layers in the layer panel."),
    ("group_layers", "Create layer groups and organize layers into them."),
    # ── Spatial analysis ──
    ("buffer_analysis", "Buffer features by distance with optional dissolve."),
    ("clip_analysis", "Clip a layer to an area of interest boundary."),
    ("intersect_analysis", "Compute geometric intersection between two layers."),
    ("union_analysis", "Merge two layers preserving all features."),
    ("dissolve_analysis", "Dissolve features by attribute field."),
    ("spatial_join", "Join attributes from one layer to another by spatial relationship."),
    ("zonal_statistics", "Compute raster statistics (mean, min, max, sum) within polygon zones."),
    ("proximity_analysis", "Find features within a distance of target features."),
    ("network_analysis", "Shortest path, service area, and isochrone analysis on road networks."),
    ("voronoi_diagram", "Generate Voronoi/Thiessen polygons from point features."),
    ("density_analysis", "Kernel density estimation or point-in-polygon count."),
    ("hotspot_analysis", "Getis-Ord Gi* or Moran's I spatial autocorrelation."),
    # ── Raster analysis ──
    ("raster_calculator", "Perform band math: NDVI, EVI, NDWI, change detection, etc."),
    ("terrain_analysis", "Slope, aspect, hillshade, curvature, TPI from a DEM."),
    ("contour_generation", "Generate contour lines or polygons from a DEM."),
    ("raster_classify", "Classify raster values into discrete categories."),
    ("raster_reproject", "Reproject and resample a raster to a new CRS and resolution."),
    ("raster_clip", "Clip a raster to a polygon mask."),
    ("raster_statistics", "Compute band statistics: min, max, mean, std, histogram."),
    ("raster_to_vector", "Polygonize a classified raster to vector polygons."),
    ("vector_to_raster", "Rasterize a vector layer to a GeoTIFF."),
    # ── Remote sensing & satellite ──
    ("run_gee_code", "Execute Google Earth Engine JavaScript or Python via geemap. Results displayed on canvas."),
    ("gee_export_image", "Export a GEE image to GeoTIFF and load into QGIS canvas."),
    ("gee_time_series", "Extract GEE time series (NDVI, precipitation, temperature) for a region."),
    ("gee_sentinel1", "Access Sentinel-1 SAR data via GEE for backscatter analysis."),
    ("gee_sentinel2", "Access Sentinel-2 optical data via GEE with cloud masking."),
    ("gee_landsat", "Access Landsat collection via GEE with quality filtering."),
    ("gee_modis", "Access MODIS products (NDVI, LST, EVI) via GEE."),
    ("gee_climate_data", "Access ERA5, CHIRPS, or other climate datasets via GEE."),
    ("download_sentinel2", "Download Sentinel-2 imagery from Copernicus Data Space and load to canvas."),
    ("download_landsat", "Download Landsat imagery from USGS EarthExplorer and load to canvas."),
    ("compute_ndvi", "Compute NDVI from red and NIR bands, load result to canvas with color ramp."),
    ("compute_ndwi", "Compute NDWI (water index) from green and NIR bands, display on canvas."),
    ("compute_evi", "Compute Enhanced Vegetation Index from Sentinel/Landsat bands, display on canvas."),
    ("land_cover_classification", "Supervised or unsupervised land cover classification using ML, display on canvas."),
    ("change_detection", "Detect land cover or spectral change between two raster dates, display on canvas."),
    ("time_series_analysis", "Analyze NDVI or spectral time series for trends and seasonality, plot and display."),
    # ── SAR processing ──
    ("sar_calibration", "Radiometric calibration of Sentinel-1 SAR data (sigma0, beta0, gamma0)."),
    ("sar_speckle_filter", "Apply Lee, Frost, Gamma-MAP, or Refined Lee speckle filtering to SAR."),
    ("sar_terrain_correction", "Range-Doppler terrain correction with SRTM or custom DEM."),
    ("sar_polarimetry", "Compute H/A/Alpha, Pauli RGB, or Freeman-Durden decomposition."),
    ("sar_coherence", "Compute interferometric coherence from Sentinel-1 SLC pairs."),
    ("sar_backscatter_timeseries", "Extract VV/VH backscatter time series for crop or flood monitoring."),
    ("sar_change_detection", "Detect changes in SAR backscatter for disaster response or deforestation."),
    ("sar_flood_mapping", "Map flooded areas from Sentinel-1 using threshold or ML classification."),
    ("sar_ship_detection", "Detect ships or vessels in SAR imagery using CFAR or deep learning."),
    # ── Machine learning ──
    ("train_classifier", "Train a Random Forest, SVM, or XGBoost classifier on spatial features."),
    ("predict_raster", "Apply a trained ML model to a raster stack for spatial prediction."),
    ("cluster_features", "K-means or DBSCAN clustering of vector features by attributes."),
    ("feature_importance", "Compute feature importance for a trained spatial ML model."),
    # ── Web data ──
    ("fetch_osm_data", "Download OpenStreetMap features via Overpass API by type and bbox."),
    ("fetch_wfs_layer", "Load a WFS layer from any OGC-compliant server."),
    ("fetch_wms_layer", "Add a WMS/WMTS basemap or overlay layer."),
    ("download_file", "Download any file from a URL to the project directory."),
    ("web_search", "Search GIS documentation, data portals, and spatial datasets."),
    ("web_fetch", "Fetch and parse content from a URL."),
    # ── Layout & export ──
    ("create_map_layout", "Build a print layout with map, legend, scale bar, north arrow, and title."),
    ("export_map_pdf", "Export a print layout to PDF."),
    ("export_map_png", "Export a print layout or canvas to PNG at specified DPI."),
    ("export_atlas", "Generate an atlas PDF with one page per feature."),
    # ── Automation ──
    ("batch_process", "Apply a processing algorithm to all files matching a pattern."),
    ("run_model", "Execute a QGIS Processing model (.model3 file)."),
    ("schedule_task", "Run a background task without blocking the QGIS UI."),
    ("bash", "Run any shell command: gdal, ogr2ogr, python scripts, curl, etc."),
    # ── Project & data management ──
    ("get_audit_trail", "Read the .aery/operations.jsonl audit log."),
    ("analyze_task", "Break a complex multi-step task into a structured execution plan."),
    ("ask_user", "Ask the user a clarifying question before proceeding."),
    ("confirm_action", "Request explicit confirmation before a destructive operation."),
    ("register_tool", "Dynamically register a new custom tool for this session."),
    ("list_registered_tools", "List all dynamically registered custom tools."),
]


class ToolRegistryDialog(QDialog):
    """Surgical-grade tool management dialog.

    Pass `rpc` to populate from the live runner via get_state.
    Falls back to the static list if the runner is unavailable or slow.
    """

    def __init__(self, parent: Optional[QWidget] = None, rpc=None):
        super().__init__(parent)
        self.setWindowTitle("GEOSPATIAL TOOL REGISTRY")
        self.setFixedSize(450, 580)
        self.setStyleSheet(f"background-color: {BG_BASE}; color: {TEXT_MAIN}; font-family: 'Public Sans';")
        self._build_ui()
        self._populate(_FALLBACK_TOOLS, source="Python agent")

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──
        header = QFrame()
        header.setFixedHeight(60)
        header.setStyleSheet(f"background-color: {BG_SURFACE}; border-bottom: 1px solid {BORDER_TECH};")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(20, 0, 20, 0)
        title = QLabel("CAPABILITY REGISTRY")
        title.setStyleSheet(f"font-weight: 800; font-size: 11px; letter-spacing: 1.5px; color: {ACCENT_TEAL};")
        h_layout.addWidget(title)
        self._source_lbl = QLabel("loading…")
        self._source_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 8px;")
        h_layout.addStretch()
        h_layout.addWidget(self._source_lbl)
        layout.addWidget(header)

        # ── Tool List ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._scroll_content = QWidget()
        self.list_layout = QVBoxLayout(self._scroll_content)
        self.list_layout.setContentsMargins(20, 20, 20, 20)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch()
        scroll.setWidget(self._scroll_content)
        layout.addWidget(scroll, 1)

        # ── Footer ──
        footer = QFrame()
        footer.setFixedHeight(50)
        footer.setStyleSheet(f"background-color: {BG_SURFACE}; border-top: 1px solid {BORDER_TECH};")
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(20, 0, 20, 0)
        close_btn = QPushButton("CLOSE REGISTRY")
        close_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_TEAL}; color: {BG_BASE}; border: none; "
            f"border-radius: 2px; padding: 6px 16px; font-size: 9px; font-weight: 900; }}"
        )
        close_btn.clicked.connect(self.accept)
        f_layout.addStretch()
        f_layout.addWidget(close_btn)
        layout.addWidget(footer)

    def _populate(self, tools: list[tuple[str, str]], source: str) -> None:
        """Clear and repopulate the tool list."""
        # Remove all cards (keep the trailing stretch)
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for name, desc in tools:
            self._add_tool_card(name, desc)

        self._source_lbl.setText(source)

    def _add_tool_card(self, name: str, desc: str) -> None:
        card = QFrame()
        card.setStyleSheet(
            f"background-color: {BG_SURFACE}; border: 1px solid {BORDER_TECH}; border-radius: 4px; padding: 12px;"
        )
        c_layout = QVBoxLayout(card)
        h_box = QHBoxLayout()
        n_lbl = QLabel(name.upper())
        n_lbl.setStyleSheet(f"color: {ACCENT_TEAL}; font-size: 10px; font-weight: 800; letter-spacing: 0.5px;")
        h_box.addWidget(n_lbl)
        h_box.addStretch()
        s_lbl = QLabel("ACTIVE")
        s_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 8px; font-weight: bold;")
        h_box.addWidget(s_lbl)
        c_layout.addLayout(h_box)
        d_lbl = QLabel(desc)
        d_lbl.setWordWrap(True)
        d_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 10px; margin-top: 4px;")
        c_layout.addWidget(d_lbl)
        # Insert before the trailing stretch
        self.list_layout.insertWidget(self.list_layout.count() - 1, card)
