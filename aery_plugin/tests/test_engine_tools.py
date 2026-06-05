from aery_plugin.engine.tools import EvalTool

def test_eval_persistence():
    tool = EvalTool()
    res1 = tool.execute("my_layer = 'Roads'")
    res2 = tool.execute("print(my_layer)")
    assert "Roads" in res2
