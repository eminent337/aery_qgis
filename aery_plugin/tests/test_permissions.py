import pytest
import threading
from aery_plugin.agent_permissions import PermissionManager

def test_permission_manager_initialization():
    pm = PermissionManager()
    assert pm.always is False
    assert not pm._requests

def test_permission_manager_register_and_resolve():
    pm = PermissionManager()
    req_id = "test-req-1"
    
    # Register request
    pm.register_request(req_id)
    assert req_id in pm._requests
    assert pm._requests[req_id]["approved"] is False
    
    # Resolve it
    pm.resolve(req_id, approved=True, always=False)
    
    assert pm._requests[req_id]["approved"] is True
    assert pm._requests[req_id]["event"].is_set()
    assert pm.always is False

def test_permission_manager_cancel():
    pm = PermissionManager()
    req_id = "test-req-2"
    
    pm.register_request(req_id)
    pm.cancel(req_id)
    
    assert pm._requests[req_id]["approved"] is False
    assert pm._requests[req_id]["event"].is_set()

def test_permission_manager_wait_for_approval():
    pm = PermissionManager()
    req_id = "test-req-3"
    pm.register_request(req_id)
    
    # Simulate UI resolving the permission from another thread
    def resolve_later():
        import time
        time.sleep(0.1)
        pm.resolve(req_id, approved=True)
        
    t = threading.Thread(target=resolve_later)
    t.start()
    
    # This should block until resolved
    result = pm.wait_for_approval(req_id, timeout=2.0)
    assert result is True
    
    t.join()

def test_permission_manager_reset_keeps_session_flags():
    pm = PermissionManager()
    pm.always = True
    pm.code_approved = True
    pm.register_request("req-1")
    pm.reset()
    # Per-turn reset must keep session-scoped flags alive.
    assert pm.always is True
    assert pm.code_approved is True
    assert not pm._requests
def test_permission_manager_reset_session_clears_always():
    pm = PermissionManager()
    pm.always = True
    pm.code_approved = True
    pm.register_request("req-1")
    pm.reset_session()
    assert pm.always is False
    assert pm.code_approved is False
    assert not pm._requests
def test_permission_manager_always_survives_multiple_turns():
    """Regression for P3 Step 1: 'always allow this session' must survive
    across multiple per-turn reset() calls."""
    pm = PermissionManager()
    req_id = "setup"
    pm.register_request(req_id)
    pm.resolve(req_id, approved=True, always=True)
    pm._requests.clear()  # simulate end-of-turn cleanup
    for turn in range(5):
        pm.reset()
        assert pm.always is True, f"always flag lost on turn {turn}"
    pm.reset_session()
    assert pm.always is False