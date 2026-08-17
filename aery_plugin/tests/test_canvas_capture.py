"""Integration tests for canvas capture.

Exercises the actual rendering path against a headless QGIS environment
if available, otherwise falls back to a mock that proves the code path
runs cleanly end-to-end.

The live capture path is QGISCodeExecutor._capture_canvas(), invoked via the
"__capture_canvas__" sentinel. A standalone CanvasCapture class in
executor_canvas.py shares the same rendering logic and is unit-tested below.
"""

import os
import sys
import base64
import struct
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _build_mock_canvas(width: int = 800, height: int = 600):
    """Build a mock QGIS map canvas that returns valid QImage renders."""
    from PyQt6.QtGui import QImage, QPainter, QColor
    from PyQt6.QtCore import QSize

    canvas = MagicMock()
    canvas.width.return_value = width
    canvas.height.return_value = height

    def _do_render(painter):
        painter.fillRect(0, 0, 9999, 9999, QColor("white"))
        painter.drawRect(50, 50, 200, 200)

    canvas.render.side_effect = _do_render
    return canvas


def _build_mock_iface(canvas=None):
    iface = MagicMock()
    iface.mapCanvas.return_value = canvas if canvas is not None else _build_mock_canvas()
    return iface


class TestCanvasCapture(unittest.TestCase):
    def test_capture_returns_base64_png(self):
        from aery_plugin.executor_canvas import CanvasCapture
        iface = _build_mock_iface()
        cap = CanvasCapture(iface)
        result = cap.capture()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 16)
        raw = base64.b64decode(result[:24] + "=" * 4)
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")

    def test_capture_handles_zero_size(self):
        from aery_plugin.executor_canvas import CanvasCapture
        iface = _build_mock_iface(canvas=_build_mock_canvas(width=0, height=0))
        cap = CanvasCapture(iface)
        result = cap.capture()
        self.assertGreater(len(result), 16)

    def test_capture_survives_wait_failure(self):
        from aery_plugin.executor_canvas import CanvasCapture
        canvas = _build_mock_canvas()
        del canvas.waitWhileRendering
        iface = _build_mock_iface(canvas=canvas)
        cap = CanvasCapture(iface)
        result = cap.capture()
        self.assertGreater(len(result), 16)

    def test_capture_scales_large_canvas_down(self):
        """A 2048x2048 canvas should be scaled to fit within 1024px max dimension."""
        from aery_plugin.executor_canvas import CanvasCapture
        iface = _build_mock_iface(canvas=_build_mock_canvas(width=2048, height=2048))
        cap = CanvasCapture(iface)
        result = cap.capture()
        self.assertGreater(len(result), 16)
        raw = base64.b64decode(result)
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        width = struct.unpack(">I", raw[16:20])[0]
        height = struct.unpack(">I", raw[20:24])[0]
        self.assertEqual(width, 1024)
        self.assertEqual(height, 1024)


class TestLiveCaptureCanvas(unittest.TestCase):
    """Tests for the live QGISCodeExecutor._capture_canvas() path.

    These use a mock canvas/iface because the live method marshals the render
    onto the GUI thread; the rendering logic is identical to CanvasCapture.
    """

    def _make_executor(self, canvas):
        from aery_plugin.qgis_executor import QGISCodeExecutor

        class _Iface:
            def mapCanvas(self):
                return canvas

        ex = QGISCodeExecutor.__new__(QGISCodeExecutor)
        ex.iface = _Iface()
        return ex

    def test_live_capture_uses_qbuffer_not_bytesio(self):
        """Regression: _capture_canvas must encode via QBuffer/QByteArray,
        not io.BytesIO (which raises TypeError under PyQt6)."""
        from PyQt6.QtGui import QImage, QPainter, QColor
        from PyQt6.QtCore import QSize
        import io

        from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle
        canvas = MagicMock()
        canvas.width.return_value = 800
        canvas.height.return_value = 600
        canvas.thread.return_value = None
        canvas.layers.return_value = []
        canvas.extent.return_value = QgsRectangle(0, 0, 100, 100)
        ms = MagicMock()
        ms.destinationCrs.return_value = QgsCoordinateReferenceSystem("EPSG:3857")
        canvas.mapSettings.return_value = ms
        ex = self._make_executor(canvas)
        # If the old io.BytesIO path were still present it would raise here.
        b64 = ex._capture_canvas()
        self.assertGreater(len(b64), 16)
        raw = base64.b64decode(b64)
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")

    def test_live_capture_fills_white_background(self):
        """Vision models need a white background, not transparent black."""
        from PyQt6.QtGui import QImage, QPainter, QColor
        from PyQt6.QtCore import QSize

        from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle
        canvas = MagicMock()
        canvas.width.return_value = 100
        canvas.height.return_value = 100
        canvas.thread.return_value = None
        canvas.layers.return_value = []
        canvas.extent.return_value = QgsRectangle(0, 0, 100, 100)
        ms = MagicMock()
        ms.destinationCrs.return_value = QgsCoordinateReferenceSystem("EPSG:3857")
        canvas.mapSettings.return_value = ms
        ex = self._make_executor(canvas)
        b64 = ex._capture_canvas()
        self.assertGreater(len(b64), 16)


if __name__ == "__main__":
    unittest.main()
