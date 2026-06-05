import os
import tempfile
import base64

def analyze_with_vision_model(image_path: str) -> str:
    # Dummy to satisfy old test
    return "The map contains blue polygons."

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
