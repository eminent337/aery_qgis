"""Regression tests: permission requests must WAIT for the user.

Root cause being guarded: the dispatcher waited on a permission request with a
fixed 120s timeout and treated a timeout as DENIED. The model then saw
"Permission denied" as a tool error and retried the same tool, re-prompting
each time, burning one turn per retry until it hit max turns
("Agent reached maximum turns.").

Fix: interactive runs (on_event present) wait indefinitely — the request stays
pending until the user approves/denies or the run is stopped (cancel_all).
Only headless runs (no UI to answer) keep a bounded timeout.
"""

import asyncio
import threading
import time

from aery_plugin.agent_permissions import PermissionManager
from aery_plugin.agent_dispatcher import ToolDispatcher


# ── PermissionManager: indefinite wait + unblock-on-reset ─────────────────────

def test_wait_for_approval_default_waits_indefinitely():
    """A request without an explicit timeout must NOT auto-deny after a timer;
    it stays pending until the user acts."""
    pm = PermissionManager()
    pm.register_request("w1")
    results = {}

    def waiter():
        results["w1"] = pm.wait_for_approval("w1")  # default timeout=None

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.2)
    assert "w1" not in results, "request auto-resolved without user input"
    pm.resolve("w1", approved=True)
    t.join(timeout=2.0)
    assert results.get("w1") is True


def test_reset_unblocks_pending_waiter_as_denied():
    """reset() must unblock any blocked wait (denied) so a pending request can
    never strand the agent thread."""
    pm = PermissionManager()
    pm.register_request("x1")
    results = {}

    def waiter():
        results["x1"] = pm.wait_for_approval("x1")

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.1)
    pm.reset()
    t.join(timeout=2.0)
    assert results.get("x1") is False
    assert not pm._requests


def test_reset_session_unblocks_pending_waiter_as_denied():
    pm = PermissionManager()
    pm.register_request("x2")
    results = {}

    def waiter():
        results["x2"] = pm.wait_for_approval("x2")

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.1)
    pm.reset_session()
    t.join(timeout=2.0)
    assert results.get("x2") is False
    assert not pm._requests


# ── Dispatcher: interactive waits, headless stays bounded ─────────────────────

class _FakeTools:
    def __init__(self, perm):
        self._perm = perm
        self.executed = []
        self.permission_modes = []

    def check_permission(self, tool_name, params, code=None):
        return self._perm

    async def execute(self, name, params, on_progress=None):
        self.executed.append((name, params))
        return "done"

    def set_permission_mode(self, mode):
        self.permission_modes.append(mode)


class _FakeAgent:
    def __init__(self, tools, permissions):
        self.tools = tools
        self.permissions = permissions
        self._session_id = "sess-1"
        self._project_dir = None

    def _snapshot_layer_state(self, name, code):
        return None

    def _diagnose_error(self, raw, name):
        return f"ERR: {name}"


def _ask_tool_call():
    return {
        "id": "call-1",
        "type": "function",
        "function": {"name": "run_qgis_code", "arguments": '{"code": "x = 1"}'},
    }


def test_dispatcher_ask_waits_for_user_when_interactive():
    """With on_event present, the dispatcher must block until the user
    resolves the request — and then execute the tool."""
    pm = PermissionManager()
    tools = _FakeTools({"behavior": "ask", "description": "run code", "risk_level": "high"})
    agent = _FakeAgent(tools, pm)
    dispatcher = ToolDispatcher(agent)

    request_seen = threading.Event()
    rid_box = {}

    def on_event(e):
        if e.get("type") == "permission_request":
            rid_box["rid"] = e["request_id"]
            request_seen.set()

    def resolver():
        assert request_seen.wait(2.0), "permission_request event never emitted"
        pm.resolve(rid_box["rid"], approved=True)

    t = threading.Thread(target=resolver)
    t.start()
    exec_results, _snaps = asyncio.run(dispatcher.execute_all([_ask_tool_call()], on_event=on_event))
    t.join(timeout=2.0)

    assert ("run_qgis_code", {"code": "x = 1"}) in tools.executed, "tool not executed after approval"
    assert exec_results[0][3] is False  # no error


def test_dispatcher_ask_headless_times_out_as_denied(monkeypatch):
    """Without on_event (headless/automation) the wait is bounded and
    auto-denies instead of hanging forever."""
    from aery_plugin import agent_dispatcher
    monkeypatch.setattr(agent_dispatcher, "HEADLESS_PERMISSION_TIMEOUT", 0.2)

    pm = PermissionManager()
    tools = _FakeTools({"behavior": "ask", "description": "run code", "risk_level": "high"})
    agent = _FakeAgent(tools, pm)
    dispatcher = ToolDispatcher(agent)

    start = time.monotonic()
    exec_results, _snaps = asyncio.run(dispatcher.execute_all([_ask_tool_call()], on_event=None))
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, "headless run hung instead of timing out"
    assert tools.executed == [], "tool must not execute after auto-deny"
    assert exec_results[0][3] is True  # had_error
    assert "Permission denied" in exec_results[0][2]
# ── Plan-first destructive step: same wait semantics ──────────────────────────

class _StubDispatcher:
    def __init__(self):
        self.calls = []

    async def execute_all(self, tool_calls, on_event=None):
        self.calls.append(tool_calls)
        tc = tool_calls[0]
        return [(tc, tc["function"]["name"], "ok", False)], []


class _StubClient:
    def format_message_pair(self, tc, result):
        return [
            {"role": "assistant", "content": "", "tool_calls": [tc]},
            {"role": "tool", "tool_call_id": tc.get("id", ""), "content": str(result)},
        ]


def _stub_agent():
    from aery_plugin.agent import Agent, AgentState

    agent = Agent.__new__(Agent)  # skip the heavy __init__ / QThread setup
    agent._current_step_index = 0
    agent._pending_steps = [
        {"id": "s1", "type": "function", "function": {"name": "layer.commitChanges", "arguments": "{}"}}
    ]
    agent.permissions = PermissionManager()
    agent._state = AgentState.PLANNING
    agent._session_id = "sess-plan"
    agent._lock = threading.Lock()
    agent._messages = []
    agent.dispatcher = _StubDispatcher()
    agent._client = _StubClient()
    agent._persist_message = lambda m: None
    return agent


def test_execute_next_step_destructive_waits_for_approval():
    """Plan-first destructive steps must surface a permission_request and wait
    for the user (the old unwired flow made approval impossible)."""
    agent = _stub_agent()

    request_seen = threading.Event()
    rid_box = {}

    def on_event(e):
        if e.get("type") == "permission_request":
            rid_box["rid"] = e["request_id"]
            request_seen.set()

    def resolver():
        assert request_seen.wait(2.0), "permission_request event never emitted"
        agent.permissions.resolve(rid_box["rid"], approved=True)

    t = threading.Thread(target=resolver)
    t.start()
    result, should_pause = asyncio.run(agent._execute_next_step(on_event))
    t.join(timeout=2.0)

    assert result == "ok"
    assert should_pause is False
    assert agent._current_step_index == 1, "step index must advance after approval"


def test_execute_next_step_destructive_denied_cancels_step():
    """A denied destructive step cancels the step without executing it."""
    agent = _stub_agent()

    request_seen = threading.Event()
    rid_box = {}

    def on_event(e):
        if e.get("type") == "permission_request":
            rid_box["rid"] = e["request_id"]
            request_seen.set()

    def resolver():
        assert request_seen.wait(2.0), "permission_request event never emitted"
        agent.permissions.resolve(rid_box["rid"], approved=False)

    t = threading.Thread(target=resolver)
    t.start()
    result, should_pause = asyncio.run(agent._execute_next_step(on_event))
    t.join(timeout=2.0)

    assert "cancelled" in result
    assert should_pause is False
    assert agent._current_step_index == 0, "step index must not advance when cancelled"
    assert agent.dispatcher.calls == [], "denied step must not execute"
