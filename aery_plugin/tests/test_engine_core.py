from aery_plugin.engine.core import AeryEngine
from aery_plugin.engine.ttsr import StreamRule

def test_ttsr_interception():
    engine = AeryEngine()
    rule = StreamRule(pattern=r"os\.system", correction="Use QgsProcess instead.")
    engine.add_rule(rule)
    
    # Simulate a stream generator yielding bad tokens
    def mock_stream():
        yield "import os\n"
        yield "os.sy"
        yield "stem('rm -rf')"
        
    result = engine.run_stream(mock_stream())
    assert result.aborted is True
    assert "Use QgsProcess instead." in result.injected_prompt
