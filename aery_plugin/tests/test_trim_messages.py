import pytest
from aery_plugin.agent import Agent

def test_trim_messages_no_op():
    agent = Agent(None)
    agent._messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"}
    ]
    agent._trim_messages()
    assert len(agent._messages) == 2

def test_trim_messages_thinking_blocks():
    agent = Agent(None)
    agent._messages = [
        {
            "role": "assistant", 
            "content": [
                {"type": "thinking", "thinking": "this is a huge thought process"},
                {"type": "text", "text": "Here is the answer."}
            ]
        }
    ]
    agent._trim_messages()
    assert agent._messages[0]["content"] == "Here is the answer."

def test_trim_messages_truncates_large_results():
    agent = Agent(None)
    large_result = "A" * 20000
    agent._messages = [
        {"role": "tool", "content": large_result, "tool_call_id": "call_1"}
    ]
    agent._trim_messages()
    assert len(agent._messages[0]["content"]) < 10000
