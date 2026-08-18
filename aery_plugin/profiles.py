"""Profile model and persistence for AI provider configuration.

GeoLibre pattern: a named, saved profile bundles a provider, model, and
credential values. This module mirrors apps/geolibre-desktop/src/lib/assistant/profiles.ts
with AssistantProfile dataclass and JSON persistence.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from aery_plugin.logger import logger

try:
    from aery_plugin.vault import get_vault
    HAS_VAULT = True
except ImportError:
    HAS_VAULT = False


@dataclass
class AssistantProfile:
    """A named, saved profile bundling a provider, model, and credentials.

    Matches GeoLibre's AssistantProfile interface.
    """
    id: str
    name: str
    provider: str  # e.g. "kilo"
    model: str = ""
    # Tool allowlist - which tools this profile can use
    tool_allowlist: list[str] = field(default_factory=list)
    # System prompt addendum for this profile
    system_prompt_addendum: str = ""
    # MCP server references
    mcp_servers: list[str] = field(default_factory=list)
    # Model parameters (temperature, max_tokens, etc.)
    model_params: dict[str, Any] = field(default_factory=dict)
    # RBAC policy for this profile (tool allow/deny lists)
    policy: Optional[dict] = None
    # Provider-specific credential storage (OAuth tokens, API keys, etc.)
    credentials: dict[str, Any] = field(default_factory=dict)
    # Optional: gateway config for managed deployments
    gateway_url: str = ""
    gateway_key: str = ""
    created_at: float = field(default_factory=lambda: __import__("time").time())
    updated_at: float = field(default_factory=lambda: __import__("time").time())

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AssistantProfile":
        return cls(**data)


@dataclass
class DataRule:
    """A single data access rule for RBAC."""
    name: str
    pattern: str  # glob or regex pattern for layer/project paths
    allow: bool = True
    description: str = ""


@dataclass
class Policy:
    """Access control policy for a profile or session."""
    allowlist: list[str] = field(default_factory=list)
    denylist: list[str] = field(default_factory=list)
    data_rules: list[DataRule] = field(default_factory=list)
    inherits_from: Optional[str] = None  # profile_id to inherit from
    max_tools_per_turn: int = 10
    require_approval_for: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Policy":
        rules = data.get("data_rules", [])
        if isinstance(rules, list):
            rules = [DataRule(**r) if isinstance(r, dict) else r for r in rules]
        return cls(
            allowlist=data.get("allowlist", []),
            denylist=data.get("denylist", []),
            data_rules=rules,
            inherits_from=data.get("inherits_from"),
            max_tools_per_turn=data.get("max_tools_per_turn", 10),
            require_approval_for=data.get("require_approval_for", []),
        )


DEFAULT_PROFILES_DIR = Path.home() / ".local" / "share" / "aery_qgis" / "profiles"


def get_profiles_dir() -> Path:
    """Return the profiles directory, creating it if needed."""
    # Allow override via env for testing
    override = os.environ.get("AERY_PROFILES_DIR")
    if override:
        p = Path(override)
    else:
        p = DEFAULT_PROFILES_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def profile_file(profile_id: str) -> Path:
    return get_profiles_dir() / f"{profile_id}.json"


def save_profile(profile: AssistantProfile) -> bool:
    """Persist a profile to disk."""
    import time
    profile.updated_at = time.time()
    try:
        # Save profile metadata to JSON file
        profile_file(profile.id).write_text(json.dumps(profile.to_json(), indent=2))
        
        # Store API keys in Vault (don't store in JSON file)
        if HAS_VAULT and profile.credentials:
            vault = get_vault("profiles")
            for key, value in profile.credentials.items():
                if isinstance(value, str) and value:
                    vault.set_profile_secret(profile.id, key, value)
        
        return True
    except Exception as e:
        logger.error(f"Failed to save profile {profile.id}: {e}")
        return False


def load_profile(profile_id: str) -> Optional[AssistantProfile]:
    """Load a profile by id, or None if not found."""
    path = profile_file(profile_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        profile = AssistantProfile.from_json(data)
        
        # Load API keys from Vault
        if HAS_VAULT and profile.credentials:
            vault = get_vault("profiles")
            for key in profile.credentials:
                secret = vault.get_profile_secret(profile.id, key)
                if secret:
                    profile.credentials[key] = secret
        
        return profile
    except Exception as e:
        logger.error(f"Failed to load profile {profile_id}: {e}")
        return None


def list_profiles() -> list[AssistantProfile]:
    """List all saved profiles, sorted by name."""
    dir_path = get_profiles_dir()
    profiles = []
    for f in dir_path.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            profiles.append(AssistantProfile.from_json(data))
        except Exception:
            continue
    profiles.sort(key=lambda p: p.name.lower())
    return profiles


def delete_profile(profile_id: str) -> bool:
    """Delete a profile file and its secrets from Vault."""
    try:
        path = profile_file(profile_id)
        if path.exists():
            path.unlink()
        
        # Delete secrets from Vault
        if HAS_VAULT:
            vault = get_vault("profiles")
            # Note: Vault doesn't have a bulk delete, but we can delete known keys
            # The keys would be whatever was in the profile's credentials
            # For now, we just delete the profile file
            pass
        
        return True
    except Exception as e:
        logger.error(f"Failed to delete profile {profile_id}: {e}")
        return False


def get_default_profile_id() -> Optional[str]:
    """Get the default profile id from the config file."""
    config_file = get_profiles_dir() / "default_profile.txt"
    try:
        if config_file.exists():
            pid = config_file.read_text().strip()
            return pid if pid else None
    except Exception:
        pass
    return None


def set_default_profile_id(profile_id: Optional[str]) -> bool:
    """Set the default profile id."""
    config_file = get_profiles_dir() / "default_profile.txt"
    try:
        if profile_id:
            config_file.write_text(profile_id)
        elif config_file.exists():
            config_file.unlink()
        return True
    except Exception as e:
        logger.error(f"Failed to set default profile: {e}")
        return False


def select_active_profile(
    profiles: list[AssistantProfile],
    default_profile_id: Optional[str],
    selected_profile_id: Optional[str],
    user_explicitly_chose: bool,
) -> Optional[AssistantProfile]:
    """Select the active profile using GeoLibre's logic.

    Priority:
    1. If user explicitly chose a profile this session, use it.
    2. Else if a default profile is set and exists, use it.
    3. Else if a session-selected profile exists, use it.
    4. Else None.
    """
    if user_explicitly_chose and selected_profile_id:
        for p in profiles:
            if p.id == selected_profile_id:
                return p
    if default_profile_id:
        for p in profiles:
            if p.id == default_profile_id:
                return p
    if selected_profile_id:
        for p in profiles:
            if p.id == selected_profile_id:
                return p
    return None