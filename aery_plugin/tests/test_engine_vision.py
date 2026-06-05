from aery_plugin.engine.vision import InspectImageTool
from unittest.mock import patch

@patch('aery_plugin.engine.vision.capture_qgis_canvas')
def test_inspect_image(mock_capture):
    mock_capture.return_value = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    tool = InspectImageTool()
    res = tool.execute("capture_canvas")
    assert isinstance(res, dict)
    assert res["type"] == "image_url"
    assert "data:image/png;base64,iVBORw0K" in res["image_url"]["url"]
