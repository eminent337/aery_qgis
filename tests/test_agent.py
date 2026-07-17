def test_agent_builds_system_prompt():
    from aery_plugin.agent import Agent
    agent = Agent(executor=None, iface=None)
    prompt = agent._system_prompt
    assert "geospatial" in prompt.lower() or "QGIS" in prompt

def test_try_parse_partial_json():
    from aery_plugin.agent import Agent
    agent = Agent(executor=None, iface=None)
    
    # Test completed JSON
    assert agent._try_parse_partial_json('{"a": 1, "b": "hello"}') == {"a": 1, "b": "hello"}
    
    # Test incomplete JSON with missing brace
    assert agent._try_parse_partial_json('{"layer_name": "roads"') == {"layer_name": "roads"}
    assert agent._try_parse_partial_json('{"xmin": 1.2, "ymin": 3.4') == {"xmin": 1.2, "ymin": 3.4}
    assert agent._try_parse_partial_json('{"visible": true') == {"visible": True}
    assert agent._try_parse_partial_json('{"visible": false') == {"visible": False}
    assert agent._try_parse_partial_json('{"value": null') == {"value": None}

def test_speculative_validate():
    from aery_plugin.agent import Agent
    from unittest.mock import MagicMock
    agent = Agent(executor=None, iface=None)
    agent.tools = MagicMock()
    agent.tools.validate_params = MagicMock(return_value="Missing required parameter: y; Layer 'roads' not found.")
    
    # Trigger speculative validate
    agent._speculative_validate("zoom_to_layer", '{"layer_name": "roads"')
    agent.tools.validate_params.assert_called_once_with("zoom_to_layer", {"layer_name": "roads"})
