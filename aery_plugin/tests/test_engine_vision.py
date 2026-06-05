from aery_plugin.engine.vision import InspectImageTool
from unittest.mock import patch

@patch('aery_plugin.engine.vision.analyze_with_vision_model')
def test_inspect_image(mock_analyze):
    mock_analyze.return_value = "The map contains blue polygons."
    tool = InspectImageTool()
    res = tool.execute("capture_canvas")
    assert "blue polygons" in res
