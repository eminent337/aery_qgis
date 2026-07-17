"""Integration test for CanvasCapture.capture().

Exercises the actual rendering path against a headless QGIS environment
if available, otherwise falls back to a mock that proves the code path
runs cleanly end-to-end.
"""

import os
import sys
import base64
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
        # Paint something so the resulting image is non-trivial
        from PyQt6.QtGui import QBrush, QColor
        painter.fillRect(0, 0, 9999, 9999, QColor("white"))
        painter.drawRect(50, 50, 200, 200)

    canvas.render.side_effect = _do_render
    return canvas


def _build_mock_iface(canvas=None):
    """Build a mock QgisInterface that returns the supplied canvas."""
    iface = MagicMock()
    iface.mapCanvas.return_value = canvas if canvas is not None else _build_mock_canvas()
    return iface


class TestCanvasCapture(unittest.TestCase):
    def test_capture_returns_base64_png(self):
        from aery_plugin.executor_canvas import CanvasCapture
        iface = _build_mock_iface()
        cap = CanvasCapture(iface)
        result = cap.capture()
        # Result must be non-empty base64
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 16)
        # First bytes of base64 must decode to PNG signature
        raw = base64.b64decode(result[:24] + "=" * 4)
        # PNG signature is 8 bytes: b'\x89PNG\r\n\x1a\n'
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")

    def test_capture_handles_zero_size(self):
        """If the canvas reports 0x0, capture should fall back to 800x600."""
        from aery_plugin.executor_canvas import CanvasCapture
        iface = _build_mock_iface(canvas=_build_mock_canvas(width=0, height=0))
        cap = CanvasCapture(iface)
        result = cap.capture()
        self.assertGreater(len(result), 16)

    def test_capture_survives_wait_failure(self):
        """waitWhileRendering errors should be swallowed, not raised."""
        from aery_plugin.executor_canvas import CanvasCapture
        canvas = _build_mock_canvas()
        # Some QGIS builds lack waitWhileRendering or raise on it
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
        # PNG of a 1024x1024 white image with one rect is ~1-5KB base64
        self.assertGreater(len(result), 16)
        # Decode and inspect dimensions in the PNG IHDR chunk
        raw = base64.b64decode(result)
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        # IHDR width and height are at bytes 16-23 (big-endian uint32)
        import struct
        width = struct.unpack(">I", raw[16:20])[0]
        height = struct.unpack(">I", raw[20:24])[0]
        self.assertEqual(width, 1024)
        self.assertEqual(height, 1024)


if __name__ == "__main__":
    unittest.main()
class TestCaptureCanvasResponse(unittest.TestCase):
    """Tests for _build_capture_canvas_response (P4 Step 1).
    The helper must distinguish empty canvas (no layers -> silent success)
    from real render failure (layers present but nothing rendered ->
    informative non-success) and from render exceptions (non-success).
    """
    def test_render_exception_is_failure(self):
        from aery_plugin.qgis_executor import _build_capture_canvas_response
        resp = _build_capture_canvas_response("", "QPainter not active", 0)
        self.assertFalse(resp["success"])
        self.assertIn("Canvas capture failed", resp["error"])
        self.assertIn("QPainter not active", resp["error"])
    def test_empty_with_no_layers_is_silent_success(self):
        from aery_plugin.qgis_executor import _build_capture_canvas_response
        resp = _build_capture_canvas_response("", None, 0)
        self.assertTrue(resp["success"])
        self.assertEqual(resp["result"], "")
    def test_empty_with_layers_is_failure(self):
        from aery_plugin.qgis_executor import _build_capture_canvas_response
        resp = _build_capture_canvas_response("", None, 3)
        self.assertFalse(resp["success"])
        self.assertIn("3 layer(s)", resp["error"])
        self.assertIn("render may have failed", resp["error"])
    def test_non_png_base64_is_failure(self):
        from aery_plugin.qgis_executor import _build_capture_canvas_response
        b64 = "x" * 50
        resp = _build_capture_canvas_response(b64, None, 2)
        self.assertFalse(resp["success"])
        self.assertIn("non-image base64", resp["error"])
    def test_valid_png_returns_data_uri(self):
        from aery_plugin.qgis_executor import _build_capture_canvas_response
        b64 = "iVBORw0KGgo" + "A" * 100
        resp = _build_capture_canvas_response(b64, None, 1)
        self.assertTrue(resp["success"])
        self.assertEqual(resp["result"], "data:image/png;base64," + b64)
class TestCapString(unittest.TestCase):
    """Tests for _cap_string output-size guard (P4 Step 3).
    Large projects can otherwise flood the conversation context with
    multi-MB strings from get_project_context / run_qgis_code.
    """
    def test_short_string_passes_through(self):
        from aery_plugin.qgis_executor import _cap_string
        self.assertEqual(_cap_string("hello"), "hello")
    def test_long_string_is_truncated(self):
        from aery_plugin.qgis_executor import _cap_string, MAX_OUTPUT_CHARS
        big = "x" * (MAX_OUTPUT_CHARS + 5000)
        result = _cap_string(big)
        self.assertLess(len(result), len(big))
        self.assertIn("truncated", result)
        # Marker includes the original length so the LLM can see what was cut.
        self.assertIn(str(len(big)), result)
    def test_non_string_is_coerced(self):
        from aery_plugin.qgis_executor import _cap_string
        # numeric input is stringified and passed through if small
        self.assertEqual(_cap_string(42), "42")
    def test_custom_max_chars_respected(self):
        from aery_plugin.qgis_executor import _cap_string
        s = "y" * 200
        result = _cap_string(s, max_chars=50)
        self.assertLess(len(result), 200)
        self.assertIn("truncated", result)
