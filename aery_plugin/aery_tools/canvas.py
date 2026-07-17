import base64
import io
import os
import tempfile
from typing import Optional, Dict, Any

class InspectCanvasTool:
    """Aery-Style tool for taking a screenshot of the QGIS canvas."""
    
    name = "inspect_canvas"
    description = "Capture the QGIS map canvas to visually analyze symbology, layouts, or map state."
    
    def __init__(self, iface):
        self.iface = iface

    def execute(self, params: dict) -> Dict[str, Any]:
        """Returns the image as an OpenAI/Aery-compatible multimodal message block."""
        b64_img = self._capture()
        if not b64_img:
            return {"type": "text", "text": "Failed to capture the QGIS canvas."}
            
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64_img}"
            }
        }

    def _capture(self) -> str:
        try:
            if not self.iface:
                from qgis.utils import iface
                self.iface = iface
            
            canvas = self.iface.mapCanvas()
            if not canvas:
                return ""
                
            # Wait for any ongoing async rendering (WMS tiles, etc.)
            if hasattr(canvas, 'waitWhileRendering'):
                canvas.waitWhileRendering(5000)

            # Save map canvas directly to PNG using temp file
            temp_dir = tempfile.gettempdir()
            file_path = os.path.join(temp_dir, "aery_canvas_capture.png")
            canvas.saveAsImage(file_path)
            
            with open(file_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                
            return encoded_string
        except Exception as e:
            return ""
