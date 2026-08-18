"""Policy Engine for RBAC in Aery QGIS Plugin.

Provides PolicyEngine to evaluate tool access, data access, and approval requirements
based on profile policies and session context.
"""

from __future__ import annotations

import fnmatch
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from aery_plugin.logger import logger

try:
    from aery_plugin.profiles import Policy, DataRule
    HAS_POLICIES = True
except ImportError:
    HAS_POLICIES = False


class Decision(Enum):
    """Decision outcome from policy evaluation."""
    ALLOW = "allow"
    DENY = "deny"
    ASK_APPROVAL = "ask_approval"


@dataclass
class PolicyResult:
    """Result of policy evaluation."""
    decision: Decision
    reason: str
    tool_name: str
    policy_name: Optional[str] = None


class PolicyEngine:
    """Evaluates policies for tool and data access."""
    
    def __init__(self):
        self._policies: Dict[str, Policy] = {}
        self._lock = threading.RLock()
    
    def add_policy(self, name: str, policy: Policy) -> None:
        """Add or update a policy."""
        with self._lock:
            self._policies[name] = policy
            logger.info(f"Added policy: {name}")
    
    def remove_policy(self, name: str) -> bool:
        """Remove a policy."""
        with self._lock:
            if name in self._policies:
                del self._policies[name]
                return True
            return False
    
    def get_policy(self, name: str) -> Optional[Policy]:
        """Get a policy by name."""
        with self._lock:
            return self._policies.get(name)
    
    def list_policies(self) -> list[str]:
        """List all policy names."""
        with self._lock:
            return list(self._policies.keys())
    
    def evaluate_tool_access(
        self,
        tool_name: str,
        policy_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PolicyResult:
        """Evaluate whether a tool can be used."""
        policy = self._get_effective_policy(policy_name, metadata)
        
        if policy is None:
            return PolicyResult(
                decision=Decision.ALLOW,
                reason="No policy configured, allowing by default",
                tool_name=tool_name,
            )
        
        # Check denylist first
        if tool_name in policy.denylist:
            return PolicyResult(
                decision=Decision.DENY,
                reason=f"Tool '{tool_name}' is in denylist",
                tool_name=tool_name,
                policy_name=policy_name or "default",
            )
        
        # Check allowlist
        if policy.allowlist:
            if tool_name not in policy.allowlist:
                return PolicyResult(
                    decision=Decision.DENY,
                    reason=f"Tool '{tool_name}' not in allowlist",
                    tool_name=tool_name,
                    policy_name=policy_name or "default",
                )
        
        # Check if approval is required
        if tool_name in policy.require_approval_for:
            return PolicyResult(
                decision=Decision.ASK_APPROVAL,
                reason=f"Tool '{tool_name}' requires approval",
                tool_name=tool_name,
                policy_name=policy_name or "default",
            )
        
        # Check tool count limit
        if metadata:
            tool_count = metadata.get("tool_call_count", 0)
            if tool_count >= policy.max_tools_per_turn:
                return PolicyResult(
                    decision=Decision.DENY,
                    reason=f"Tool call limit ({policy.max_tools_per_turn}) reached",
                    tool_name=tool_name,
                    policy_name=policy_name or "default",
                )
        
        return PolicyResult(
            decision=Decision.ALLOW,
            reason="Tool access allowed",
            tool_name=tool_name,
            policy_name=policy_name or "default",
        )
    
    def evaluate_data_access(
        self,
        path: str,
        policy_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PolicyResult:
        """Evaluate whether a data path can be accessed."""
        policy = self._get_effective_policy(policy_name, metadata)
        
        if policy is None or not policy.data_rules:
            return PolicyResult(
                decision=Decision.ALLOW,
                reason="No data rules configured",
                tool_name=path,
            )
        
        for rule in policy.data_rules:
            if fnmatch.fnmatch(path, rule.pattern) or rule.pattern in path:
                if not rule.allow:
                    return PolicyResult(
                        decision=Decision.DENY,
                        reason=f"Data access denied by rule '{rule.name}': {rule.description}",
                        tool_name=path,
                        policy_name=policy_name or "default",
                    )
                else:
                    return PolicyResult(
                        decision=Decision.ALLOW,
                        reason=f"Data access allowed by rule '{rule.name}'",
                        tool_name=path,
                        policy_name=policy_name or "default",
                    )
        
        # No matching rule, deny by default
        return PolicyResult(
            decision=Decision.DENY,
            reason=f"No matching data rule for path: {path}",
            tool_name=path,
            policy_name=policy_name or "default",
        )
    
    def _get_effective_policy(
        self,
        policy_name: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[Policy]:
        """Get the effective policy for evaluation."""
        # Try explicit policy name first
        if policy_name:
            policy = self._policies.get(policy_name)
            if policy:
                return self._resolve_inheritance(policy)
        
        # Try metadata policy
        if metadata:
            meta_policy = metadata.get("policy_name")
            if meta_policy and meta_policy in self._policies:
                policy = self._policies[meta_policy]
                return self._resolve_inheritance(policy)
        
        return None
    
    def _resolve_inheritance(self, policy: Policy) -> Policy:
        """Resolve policy inheritance chain."""
        if policy.inherits_from and policy.inherits_from in self._policies:
            base = self._policies[policy.inherits_from]
            merged = Policy(
                allowlist=policy.allowlist or base.allowlist,
                denylist=policy.denylist or base.denylist,
                data_rules=policy.data_rules or base.data_rules,
                inherits_from=None,
                max_tools_per_turn=policy.max_tools_per_turn or base.max_tools_per_turn,
                require_approval_for=policy.require_approval_for or base.require_approval_for,
            )
            return merged
        return policy


# Global policy engine instance
_engine: Optional[PolicyEngine] = None
_engine_lock = threading.Lock()


def get_policy_engine() -> PolicyEngine:
    """Get or create the global policy engine."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = PolicyEngine()
        return _engine


def evaluate_tool_access(
    tool_name: str,
    policy_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> PolicyResult:
    """Evaluate tool access against policy engine."""
    return get_policy_engine().evaluate_tool_access(tool_name, policy_name, metadata)


def evaluate_data_access(
    path: str,
    policy_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> PolicyResult:
    """Evaluate data access against policy engine."""
    return get_policy_engine().evaluate_data_access(path, policy_name, metadata)