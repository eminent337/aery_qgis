def test_agent_builds_system_prompt():
    from aery_plugin.agent import Agent
    agent = Agent(executor=None, iface=None)
    prompt = agent._build_system_prompt()
    assert "geospatial" in prompt.lower() or "QGIS" in prompt
