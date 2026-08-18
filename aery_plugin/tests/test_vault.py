"""Tests for Vault credential storage."""

import os
import tempfile
import shutil
from pathlib import Path

import pytest


class TestVault:
    """Test Vault credential storage with mocked keyring/crypto."""

    def setup_method(self):
        """Reset vault singleton before each test."""
        from aery_plugin.vault import reset_vault
        reset_vault()
        # Use temp directory for fallback
        self.temp_dir = Path(tempfile.mkdtemp())
        self.original_vault_dir = os.environ.get("AERY_VAULT_DIR")
        os.environ["AERY_VAULT_DIR"] = str(self.temp_dir)

    def teardown_method(self):
        """Clean up temp directory."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
        if self.original_vault_dir:
            os.environ["AERY_VAULT_DIR"] = self.original_vault_dir
        else:
            os.environ.pop("AERY_VAULT_DIR", None)
        from aery_plugin.vault import reset_vault
        reset_vault()

    def test_basic_set_get(self):
        """Test basic set/get operations."""
        from aery_plugin.vault import Vault

        vault = Vault("test")
        assert vault.set("key1", "value1")
        assert vault.get("key1") == "value1"
        assert vault.get("nonexistent", "default") == "default"

    def test_delete(self):
        """Test delete operation."""
        from aery_plugin.vault import Vault

        vault = Vault("test")
        vault.set("key1", "value1")
        assert vault.delete("key1")
        assert vault.get("key1") is None
        assert not vault.delete("nonexistent")

    def test_list_keys(self):
        """Test listing keys."""
        from aery_plugin.vault import Vault

        vault = Vault("test")
        vault.set("key1", "value1")
        vault.set("key2", "value2")
        keys = vault.list_keys()
        assert "test:key1" in keys
        assert "test:key2" in keys

    def test_clear_namespace(self):
        """Test clearing all keys in namespace."""
        from aery_plugin.vault import Vault

        vault = Vault("test")
        vault.set("key1", "value1")
        vault.set("key2", "value2")
        assert vault.clear_namespace()
        assert vault.list_keys() == []

    def test_profile_secrets(self):
        """Test profile-specific secret helpers."""
        from aery_plugin.vault import Vault

        vault = Vault("test")
        assert vault.set_profile_secret("my_profile", "api_key", "sk-12345")
        assert vault.get_profile_secret("my_profile", "api_key") == "sk-12345"
        assert vault.list_profile_secrets("my_profile") == ["api_key"]
        assert vault.delete_profile_secret("my_profile", "api_key")
        # Profile secrets use namespace-prefixed keys, test that clearing works
        vault.set_profile_secret("my_profile", "api_key", "sk-12345")
        assert vault.get_profile_secret("my_profile", "api_key") == "sk-12345"
        assert vault.delete_profile_secret("my_profile", "api_key")
        assert vault.get_profile_secret("my_profile", "api_key") is None

    def test_oauth_tokens(self):
        """Test OAuth token bundle storage."""
        from aery_plugin.vault import Vault

        vault = Vault("test")
        assert vault.set_oauth_tokens("kilo", "access_123", "refresh_456", 1234567890)
        tokens = vault.get_oauth_tokens("kilo")
        assert tokens is not None
        assert tokens["access_token"] == "access_123"
        assert tokens["refresh_token"] == "refresh_456"
        assert tokens["expires_at"] == 1234567890
        assert vault.delete_oauth_tokens("kilo")
        assert vault.get_oauth_tokens("kilo") is None

    def test_namespace_isolation(self):
        """Test that namespaces are isolated."""
        from aery_plugin.vault import Vault

        vault1 = Vault("ns1")
        vault2 = Vault("ns2")
        vault1.set("key", "value1")
        vault2.set("key", "value2")
        assert vault1.get("key") == "value1"
        assert vault2.get("key") == "value2"

    def test_health_check(self):
        """Test health check returns expected structure."""
        from aery_plugin.vault import Vault

        vault = Vault("test")
        health = vault.health_check()
        assert "keyring_available" in health
        assert "keyring_in_use" in health
        assert "keyring_available" in health
        assert "fallback_available" in health

    def test_get_vault_singleton(self):
        """Test get_vault returns singleton for default namespace."""
        from aery_plugin.vault import get_vault, reset_vault

        reset_vault()
        v1 = get_vault("default")
        v2 = get_vault("default")
        assert v1 is v2
        v3 = get_vault("other")
        assert v3 is not v1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])