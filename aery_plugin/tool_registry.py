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

# Actual registered tools in the Python agent (17 total)
REGISTERED_TOOLS = [
    # Core tools (5)
    ("run_qgis_code", "Execute any Python in the QGIS main thread. Full access to all QGIS/PyQt6/numpy/pandas/sklearn globals."),
    ("get_project_context", "Snapshot all layers, CRS, extents, field schemas, raster band info, processing providers."),
    ("capture_canvas", "Export the live map canvas as a high-DPI PNG for analysis or display."),
    ("web_search", "Search the web via DuckDuckGo."),
    ("web_fetch", "Fetch and parse content from a URL."),
    # Geospatial tools (8)
    ("export_webmap", "Export the current QGIS project as an interactive Leaflet.js web map."),
    ("publish_geoserver", "Publish a vector or raster layer to a GeoServer REST endpoint."),
    ("set_layer_style", "Apply visual styles (colormaps, RGB, graduated, categorized) to layers."),
    ("multi_map_layout", "Create a single print-layout PDF with multiple map panels."),
    ("save_map_theme", "Save the current QGIS map theme (layer visibility + renderer state)."),
    ("load_map_theme", "Load a previously saved QGIS map theme."),
    ("list_map_themes", "List all saved map themes in the current project."),
    ("refresh_canvas", "Refresh the QGIS map canvas and all layers."),
    # Graph query tools (4)
    ("query_provenance", "Trace layer lineage: what produced it, what it was derived from."),
    ("query_tool_chain", "Query what tools can follow a given tool in a processing pipeline."),
    ("query_graph", "Query the project knowledge graph for spatial relationships and structure."),
    ("query_spatial_relationships", "Query spatial relationships between layers (overlaps, contains, within, touches)."),
]


class ToolRegistryDialog(QDialog):
    """Tool registry dialog showing all registered agent tools."""

    def __init__(self, parent: Optional[QWidget] = None, rpc=None):
        super().__init__(parent)
        self.setWindowTitle("GEOSPATIAL TOOL REGISTRY")
        self.setFixedSize(450, 580)
        self.setStyleSheet(f"background-color: {BG_BASE}; color: {TEXT_MAIN}; font-family: 'Public Sans';")
        self._build_ui()
        self._populate(REGISTERED_TOOLS, source=f"Python agent ({len(REGISTERED_TOOLS)} tools)")

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
        self.list_layout.insertWidget(self.list_layout.count() - 1, card)
