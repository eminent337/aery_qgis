"""Tests for the GeoLibre-style approval queue.

The inline _PermissionWidget (ui_dialogs.py) can't be constructed without a
QApplication, but the queueable PermissionManager core is unit-testable:
multiple pending approvals register independently and resolve out of order,
mirroring the AssistantPanel codeQueueRef pattern where several tool calls
dispatched in one turn each surface their own card.
"""

from __future__ import annotations

import pytest
import threading
import time

from aery_plugin.agent_permissions import PermissionManager


def test_multiple_pending_approvals_resolve_independently():
    pm = PermissionManager()
    pm.register_request("req-a")
    pm.register_request("req-b")
    pm.register_request("req-c")

    # Resolve out of order: approve c, deny a, approve b (always)
    pm.resolve("req-c", approved=True)
    pm.resolve("req-a", approved=False)
    pm.resolve("req-b", approved=True, always=True)

    assert pm._requests["req-a"]["approved"] is False
    assert pm._requests["req-b"]["approved"] is True
    assert pm._requests["req-c"]["approved"] is True
    assert pm.always is True  # always-allow from req-b


def test_queued_approvals_wait_and_resolve_from_other_thread():
    """Two blocked wait_for_approval calls both resolve when the UI acts."""
    pm = PermissionManager()
    pm.register_request("q1")
    pm.register_request("q2")

    results = {}

    def waiter(rid):
        results[rid] = pm.wait_for_approval(rid, timeout=3.0)

    t1 = threading.Thread(target=waiter, args=("q1",))
    t2 = threading.Thread(target=waiter, args=("q2",))
    t1.start()
    t2.start()
    time.sleep(0.1)

    pm.resolve("q1", approved=True)
    pm.resolve("q2", approved=False)

    t1.join()
    t2.join()
    assert results["q1"] is True
    assert results["q2"] is False


def test_stop_cancels_all_pending_approvals():
    """Global Stop must deny every queued request (cancel_all)."""
    pm = PermissionManager()
    for i in range(5):
        pm.register_request(f"r{i}")

    pm.cancel_all()

    for i in range(5):
        assert pm._requests[f"r{i}"]["approved"] is False
        assert pm._requests[f"r{i}"]["event"].is_set()


def test_approval_survives_turn_reset_but_not_session_reset():
    pm = PermissionManager()
    pm.register_request("r1")
    pm.resolve("r1", approved=True, always=True)
    pm._requests.clear()  # end-of-turn cleanup

    pm.reset()
    assert pm.always is True

    pm.reset_session()
    assert pm.always is False


def test_wait_for_approval_timeout_returns_false():
    pm = PermissionManager()
    pm.register_request("t1")
    start = time.monotonic()
    result = pm.wait_for_approval("t1", timeout=0.2)
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed < 1.0


def test_registry_for_typed_destructive_tool_asks():
    """The dispatcher's registry selection must surface destructive asks."""
    from aery_plugin.tools_new import TypedToolBridge

    bridge = TypedToolBridge()
    perm = bridge.check_permission("remove_layer", {"layer": "roads"})
    assert perm["behavior"] == "ask"
    assert perm["risk_level"] == "medium"

    # Non-destructive typed tools are allowed without prompting.
    perm = bridge.check_permission("list_layers", {})
    assert perm["behavior"] == "allow"