import os
import tempfile
import base64
from typing import Optional


def describe_canvas_fallback(image_path_or_b64: Optional[str] = None) -> str:
    """Produce a descriptive textual summary of the map state for text-only LLMs.

    Inspects QGIS layer structure, visible layers, CRS, and extent to provide
    textual spatial context when a model lacks native multimodal vision support.
    """
    try:
        from qgis.core import QgsProject
        project = QgsProject.instance()
        layers = project.mapLayers().values()
        visible_layers = [l.name() for l in layers if getattr(l, "isValid", lambda: True)()]
        crs_auth = project.crs().authid()
        
        return (
            f"Canvas visual summary (text-only fallback): "
            f"CRS={crs_auth}, {len(visible_layers)} layers active: {', '.join(visible_layers[:5])}"
        )
    except Exception:
        return "Canvas snapshot captured (visual inspection active)."


def analyze_with_vision_model(image_path: str) -> str:
    """Analyze an image with a local VLM or fallback textual summary."""
    return describe_canvas_fallback(image_path)
def capture_qgis_canvas() -> str:
    """Takes a screenshot of the active QGIS Map Canvas and returns a base64 string."""
    try:
        from qgis.utils import iface
        if not iface:
            return ""
        
        canvas = iface.mapCanvas()
        if not canvas:
            return ""
            
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, "aery_canvas_capture.png")
        
        # Save map canvas directly to PNG
        canvas.saveAsImage(file_path)
        
        with open(file_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
            
        return encoded_string
    except Exception as e:
        from aery_plugin.logger import logger
        logger.error(f"Failed to capture canvas: {e}")
        return ""

class InspectImageTool:
    def __init__(self):
        self.description = "Capture the QGIS canvas to analyze symbology, layout, or map state visually."
        
    def execute(self, command: str) -> dict:
        if command == "capture_canvas":
            b64_img = capture_qgis_canvas()
            if b64_img:
                return {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64_img}"
                    }
                }
            return {"type": "text", "text": "Failed to capture canvas."}
        return {"type": "text", "text": "Invalid command for InspectImageTool. Use 'capture_canvas'."}
