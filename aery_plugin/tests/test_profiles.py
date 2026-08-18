"""Tests for the profile model and persistence (GeoLibre pattern)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from aery_plugin.profiles import (
    AssistantProfile,
    delete_profile,
    get_default_profile_id,
    list_profiles,
    load_profile,
    profile_file,
    save_profile,
    select_active_profile,
    set_default_profile_id,
)


@pytest.fixture
def temp_profiles_dir():
    """Override profiles directory for testing."""
    with tempfile.TemporaryDirectory() as tmp:
        with patch("aery_plugin.profiles.DEFAULT_PROFILES_DIR", Path(tmp)):
            with patch.dict(os.environ, {"AERY_PROFILES_DIR": tmp}):
                yield Path(tmp)


def test_assistant_profile_to_from_json():
    p = AssistantProfile(
        id="p1",
        name="My Kilo",
        provider="kilo",
        model="kilo-auto/free",
        credentials={"token": "abc"},
    )
    json_data = p.to_json()
    p2 = AssistantProfile.from_json(json_data)
    assert p2.id == p.id
    assert p2.name == p.name
    assert p2.provider == p.provider
    assert p2.credentials == p.credentials


def test_save_and_load_profile(temp_profiles_dir):
    p = AssistantProfile(id="test-profile", name="Test", provider="kilo")
    assert save_profile(p) is True
    assert profile_file("test-profile").exists()

    loaded = load_profile("test-profile")
    assert loaded is not None
    assert loaded.id == "test-profile"
    assert loaded.name == "Test"
    assert loaded.provider == "kilo"


def test_load_nonexistent_returns_none(temp_profiles_dir):
    assert load_profile("does-not-exist") is None


def test_delete_profile(temp_profiles_dir):
    p = AssistantProfile(id="to-delete", name="Delete Me", provider="kilo")
    save_profile(p)
    assert delete_profile("to-delete") is True
    assert not profile_file("to-delete").exists()
    assert load_profile("to-delete") is None


def test_list_profiles(temp_profiles_dir):
    for i in range(3):
        save_profile(AssistantProfile(id=f"p{i}", name=f"Profile {i}", provider="kilo"))
    profiles = list_profiles()
    assert len(profiles) == 3
    # Sorted by name
    assert [p.name for p in profiles] == ["Profile 0", "Profile 1", "Profile 2"]


def test_default_profile_persistence(temp_profiles_dir):
    assert get_default_profile_id() is None
    set_default_profile_id("my-profile")
    assert get_default_profile_id() == "my-profile"
    set_default_profile_id(None)
    assert get_default_profile_id() is None


def test_select_active_profile_logic():
    profiles = [
        AssistantProfile(id="p1", name="First", provider="kilo"),
        AssistantProfile(id="p2", name="Second", provider="kilo"),
    ]

    # User explicitly chose -> that one wins
    active = select_active_profile(profiles, "p1", "p2", True)
    assert active.id == "p2"

    # No explicit choice, default profile exists -> default wins
    active = select_active_profile(profiles, "p1", "p2", False)
    assert active.id == "p1"

    # No explicit, no default, session selection -> session wins
    active = select_active_profile(profiles, None, "p2", False)
    assert active.id == "p2"

    # No explicit, no default, no session -> None
    active = select_active_profile(profiles, None, None, False)
    assert active is None

    # Non-existent ids return None
    active = select_active_profile(profiles, "none", None, False)
    assert active is None