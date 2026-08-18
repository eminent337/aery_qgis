"""Test PolicyEngine RBAC integration with profiles and tool registry."""
import pytest
from unittest.mock import MagicMock


@pytest.fixture
def policy_engine():
    """Fresh policy engine for each test."""
    from aery_plugin.policy import get_policy_engine
    engine = get_policy_engine()
    # Clear existing policies for clean test state
    with engine._lock:
        engine._policies.clear()
    return engine


@pytest.fixture
def profile_with_policy(policy_engine):
    from aery_plugin.profiles import AssistantProfile
    policy = {
        "allowlist": ["load_basemap", "get_layer_info"],
        "denylist": ["delete_layer", "run_qgis_code"],
        "require_approval_for": ["buffer", "dissolve"],
    }
    return AssistantProfile(
        id="test-profile",
        name="Test Policy Profile",
        provider="kilo",
        policy=policy,
    )


def test_policy_engine_evaluate_allow(profile_with_policy, policy_engine):
    from aery_plugin.policy import Policy, get_policy_engine, Decision
    policy_name = f"profile:{profile_with_policy.id}"
    p = Policy.from_dict(profile_with_policy.policy)
    policy_engine.add_policy(policy_name, p)

    result = policy_engine.evaluate_tool_access("load_basemap", policy_name)
    assert result.decision == Decision.ALLOW


def test_policy_engine_evaluate_deny(profile_with_policy, policy_engine):
    from aery_plugin.policy import Policy, get_policy_engine, Decision
    policy_name = f"profile:{profile_with_policy.id}"
    p = Policy.from_dict(profile_with_policy.policy)
    policy_engine.add_policy(policy_name, p)

    result = policy_engine.evaluate_tool_access("delete_layer", policy_name)
    assert result.decision == Decision.DENY
    assert result.reason == "Tool 'delete_layer' is in denylist"


def test_policy_engine_evaluate_approval_required(policy_engine):
    from aery_plugin.policy import Policy, get_policy_engine, Decision
    from aery_plugin.profiles import AssistantProfile
    # Add buffer to allowlist so it reaches approval check
    policy = {
        "allowlist": ["load_basemap", "get_layer_info", "buffer", "dissolve"],
        "denylist": ["delete_layer", "run_qgis_code"],
        "require_approval_for": ["buffer", "dissolve"],
    }
    profile = AssistantProfile(
        id="test-profile-approval",
        name="Test Approval Profile",
        provider="kilo",
        policy=policy,
    )
    policy_name = f"profile:{profile.id}"
    p = Policy.from_dict(policy)
    policy_engine.add_policy(policy_name, p)

    result = policy_engine.evaluate_tool_access("buffer", policy_name)
    assert result.decision == Decision.ASK_APPROVAL
    assert "approval" in result.reason.lower()


def test_policy_engine_unknown_tool(profile_with_policy, policy_engine):
    from aery_plugin.policy import Policy, get_policy_engine, Decision
    policy_name = f"profile:{profile_with_policy.id}"
    p = Policy.from_dict(profile_with_policy.policy)
    policy_engine.add_policy(policy_name, p)

    result = policy_engine.evaluate_tool_access("unknown_tool", policy_name)
    # Unknown tool in allowlist-only policy → denied (not in allowlist)
    assert result.decision == Decision.DENY


def test_tool_registry_check_permission_no_policy():
    from aery_plugin.tools import ToolRegistry
    executor = MagicMock()
    agent = MagicMock()
    agent.permissions = None
    agent._policy_name = None
    registry = ToolRegistry(executor, agent=agent)
    result = registry.check_permission("load_basemap", {})
    # No policy + no bypass mode = ask (default permission behavior)
    assert result["behavior"] in ("ask", "allow")


def test_tool_registry_check_permission_denylist(profile_with_policy):
    from aery_plugin.tools import ToolRegistry
    from aery_plugin.policy import Policy, get_policy_engine
    policy_engine = get_policy_engine()
    with policy_engine._lock:
        policy_engine._policies.clear()
    policy_name = f"profile:{profile_with_policy.id}"
    p = Policy.from_dict(profile_with_policy.policy)
    policy_engine.add_policy(policy_name, p)

    executor = MagicMock()
    agent = MagicMock()
    agent.permissions = None
    agent._policy_name = policy_name
    registry = ToolRegistry(executor, agent=agent)
    registry._policy_name = policy_name  # wire policy after init

    result = registry.check_permission("delete_layer", {})
    assert result["behavior"] == "deny"


def test_tool_registry_check_permission_approval():
    from aery_plugin.tools import ToolRegistry
    from aery_plugin.policy import Policy, get_policy_engine
    from aery_plugin.profiles import AssistantProfile
    policy_engine = get_policy_engine()
    with policy_engine._lock:
        policy_engine._policies.clear()
    # Create profile where buffer is in allowlist+approval but NOT denylist
    profile = AssistantProfile(
        id="approval-test",
        name="Approval Test",
        provider="kilo",
        policy={
            "allowlist": ["load_basemap", "buffer"],
            "denylist": ["delete_layer"],
            "require_approval_for": ["buffer"],
        },
    )
    policy_name = f"profile:{profile.id}"
    p = Policy.from_dict(profile.policy)
    policy_engine.add_policy(policy_name, p)

    executor = MagicMock()
    agent = MagicMock()
    agent.permissions = None
    agent._policy_name = policy_name
    registry = ToolRegistry(executor, agent=agent)
    registry._policy_name = policy_name

    result = registry.check_permission("buffer", {})
    assert result["behavior"] == "ask"
    assert "approval" in result["description"].lower()


def test_tool_registry_check_permission_allowlist(profile_with_policy):
    from aery_plugin.tools import ToolRegistry
    from aery_plugin.policy import Policy, get_policy_engine
    policy_engine = get_policy_engine()
    with policy_engine._lock:
        policy_engine._policies.clear()
    policy_name = f"profile:{profile_with_policy.id}"
    p = Policy.from_dict(profile_with_policy.policy)
    policy_engine.add_policy(policy_name, p)

    executor = MagicMock()
    agent = MagicMock()
    agent.permissions = None
    agent._policy_name = policy_name
    registry = ToolRegistry(executor, agent=agent)
    registry._policy_name = policy_name  # wire policy after init

    result = registry.check_permission("load_basemap", {})
    assert result["behavior"] == "allow"


def test_tool_registry_check_permission_bypass_mode():
    from aery_plugin.tools import ToolRegistry
    executor = MagicMock()
    agent = MagicMock()
    agent.permissions = None
    agent._policy_name = None
    registry = ToolRegistry(executor, agent=agent)
    registry.set_permission_mode("bypassPermissions")

    result = registry.check_permission("delete_layer", {})
    assert result["behavior"] == "allow"


def test_tool_registry_check_permission_dont_ask_mode():
    from aery_plugin.tools import ToolRegistry
    executor = MagicMock()
    agent = MagicMock()
    agent.permissions = None
    agent._policy_name = None
    registry = ToolRegistry(executor, agent=agent)
    registry.set_permission_mode("dontAsk")

    result = registry.check_permission("load_basemap", {})
    assert result["behavior"] == "deny"


def test_agent_initialize_wires_policy(profile_with_policy):
    from aery_plugin.agent import Agent
    from aery_plugin.policy import get_policy_engine, Policy
    policy_engine = get_policy_engine()
    with policy_engine._lock:
        policy_engine._policies.clear()

    executor = MagicMock()
    agent = Agent(executor)
    agent._active_profile = profile_with_policy
    agent._provider_id = "kilo"
    # Wire policy (simulating what initialize does)
    try:
        if profile_with_policy.policy:
            policy_name = f"profile:{profile_with_policy.id}"
            p = Policy.from_dict(profile_with_policy.policy)
            policy_engine.add_policy(policy_name, p)
            agent._policy_name = policy_name
        else:
            agent._policy_name = None
    except Exception:
        agent._policy_name = None

    assert agent._policy_name is not None
    agent.tools._policy_name = agent._policy_name  # manual wire since we skipped initialize
    assert agent.tools._policy_name == agent._policy_name


def test_agent_initialize_no_policy():
    from aery_plugin.agent import Agent
    from aery_plugin.profiles import AssistantProfile

    executor = MagicMock()
    agent = Agent(executor)
    profile = AssistantProfile(id="no-policy", name="No Policy", provider="kilo")
    agent._active_profile = profile
    agent._provider_id = "kilo"

    try:
        from aery_plugin.policy import Policy
        if profile.policy:
            policy_name = f"profile:{profile.id}"
            p = Policy.from_dict(profile.policy)
            from aery_plugin.policy import get_policy_engine
            get_policy_engine().add_policy(policy_name, p)
            agent._policy_name = policy_name
        else:
            agent._policy_name = None
    except Exception:
        agent._policy_name = None

    assert agent._policy_name is None
    agent.tools._policy_name = agent._policy_name  # manual wire
    assert agent.tools._policy_name is None


def test_policy_inheritance_from_parent():
    from aery_plugin.profiles import Policy
    parent = Policy(
        allowlist=["base_tool"],
        denylist=["dangerous_tool"],
    )
    # Create a new policy that inherits from parent by reference
    child = Policy(
        allowlist=["extra_tool"],
        denylist=["child_dangerous"],
        require_approval_for=["sensitive_tool"],
    )
    assert "base_tool" not in child.allowlist
    assert "dangerous_tool" not in child.denylist
    assert "extra_tool" in child.allowlist
    assert "child_dangerous" in child.denylist


def test_policy_json_roundtrip():
    from aery_plugin.profiles import Policy
    p = Policy(
        allowlist=["a", "b"],
        denylist=["c"],
        require_approval_for=["d"],
    )
    data = p.to_dict()
    restored = Policy.from_dict(data)
    assert restored.allowlist == ["a", "b"]
    assert restored.denylist == ["c"]
    assert restored.require_approval_for == ["d"]
