import base64
from aery_plugin.logger import logger

class CanvasCapture:
    """Handles capturing the QGIS map canvas as a base64 PNG image."""

    def __init__(self, iface):
        self.iface = iface

    def capture(self) -> str:
        """Capture the QGIS map canvas as a base64 PNG string.

        Renders at 0.75x for a good balance of clarity and speed.
        Waits for async tile loading (WMS/WMTS) before capturing.
        Falls back to full-resolution if the scaled render fails.
        Uses default size if canvas is not yet initialized.
        """
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtCore import QSize, Qt, QByteArray, QBuffer
        canvas = self.iface.mapCanvas()

        # Force a refresh so the latest extent/layer changes are rendered
        # before we capture. run_qgis_code only schedules refresh lazily.
        canvas.refresh()
        sw = canvas.width()
        sh = canvas.height()
        if sw < 1 or sh < 1:
            sw, sh = 800, 600

        # Wait for any ongoing async rendering (WMS tiles, etc.)
        try:
            if hasattr(canvas, 'waitWhileRendering'):
                canvas.waitWhileRendering(5000)
        except Exception as e:
            logger.debug("executor_canvas: waitWhileRendering failed: %s", e)

        # Aggressively scale down to prevent massive payload sizes (HTTP 413/400)
        # Max dimension 1024px is plenty for LLM vision models and keeps base64 under ~1MB
        max_dim = max(sw, sh)
        scale = 1024.0 / max_dim if max_dim > 1024 else 0.75

        def _render_at(scale_factor: float) -> bytes:
            w = max(1, int(sw * scale_factor))
            h = max(1, int(sh * scale_factor))
            img = QImage(QSize(w, h), QImage.Format.Format_ARGB32)
            # Fill with solid white (ARGB: 0xFFFFFFFF) instead of transparent (0)
            # This prevents LLM vision models from compositing vectors onto black
            # backgrounds and hallucinating a "blank screen" when the lines match the background.
            img.fill(0xFFFFFFFF)
            painter = QPainter(img)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if scale_factor < 1.0:
                painter.scale(scale_factor, scale_factor)
            canvas.render(painter)
            painter.end()
            # Save PNG to a QBuffer-backed QByteArray (PyQt6 rejects raw BytesIO).
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QBuffer.OpenModeFlag.WriteOnly)
            img.save(buf, format="PNG")
            buf.close()
            return bytes(ba)

        raw = _render_at(scale)
        if raw and len(raw) >= 8:
            return base64.b64encode(raw).decode()

        raw = _render_at(1.0)
        if not raw or len(raw) < 8:
            return ""
        return base64.b64encode(raw).decode()
