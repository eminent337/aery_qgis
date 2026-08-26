"""Interactive region/box prompt map tool.

Adapted from GeoAI (opengeos/geoai) qgis_plugin/geoai/dialogs/map_tools.py (BoxPromptTool).
Allows the user to drag a rectangle on the QGIS canvas to return a bounding box or extent.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from qgis.core import QgsPointXY, QgsRectangle, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand


class RegionSelectMapTool(QgsMapTool):
    """Map tool for interactively drawing a bounding box on the canvas."""

    box_selected = pyqtSignal(object)  # Emits QgsRectangle

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.rubber_band.setColor(Qt.GlobalColor.red)
        self.rubber_band.setWidth(2)
        self.start_point = None
        self.is_drawing = False
        self.setCursor(Qt.CursorShape.CrossCursor)

    def canvasPressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_point = self.toMapCoordinates(event.pos())
            self.is_drawing = True
            self.rubber_band.reset(QgsWkbTypes.PolygonGeometry)

    def canvasMoveEvent(self, event):
        if self.is_drawing and self.start_point:
            current_point = self.toMapCoordinates(event.pos())
            self.update_rubber_band(self.start_point, current_point)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_drawing:
            end_point = self.toMapCoordinates(event.pos())
            self.is_drawing = False

            if self.start_point and self.start_point != end_point:
                rect = QgsRectangle(
                    min(self.start_point.x(), end_point.x()),
                    min(self.start_point.y(), end_point.y()),
                    max(self.start_point.x(), end_point.x()),
                    max(self.start_point.y(), end_point.y()),
                )
                self.box_selected.emit(rect)

            self.clear()

    def update_rubber_band(self, p1: QgsPointXY, p2: QgsPointXY):
        self.rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        self.rubber_band.addPoint(p1, False)
        self.rubber_band.addPoint(QgsPointXY(p2.x(), p1.y()), False)
        self.rubber_band.addPoint(p2, False)
        self.rubber_band.addPoint(QgsPointXY(p1.x(), p2.y()), True)

    def clear(self):
        if self.rubber_band:
            self.rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        self.start_point = None
        self.is_drawing = False

    def deactivate(self):
        self.clear()
        super().deactivate()
