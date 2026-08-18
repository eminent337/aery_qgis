"""OS keyring-backed credential vault with encrypted file fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

try:
    import keyring
    from keyring.errors import KeyringError, KeyringLocked, NoKeyringError
    KEYRING_AVAILABLE = True
except ImportError:
    keyring = None
    KEYRING_AVAILABLE = False
    KeyringError = Exception
    KeyringLocked = Exception
    NoKeyringError = Exception

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    Fernet = None
    CRYPTO_AVAILABLE = False

from aery_plugin.logger import logger

SERVICE_NAME = "aery_qgis"


def _get_vault_dir() -> Path:
    env_dir = os.environ.get("AERY_VAULT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".local" / "share" / "aery_qgis" / "vault"


VAULT_DIR = _get_vault_dir()
FALLBACK_KEY_FILE = VAULT_DIR / ".vault_key"
FALLBACK_DATA_FILE = VAULT_DIR / "vault.enc"


class Vault:
    """Secure credential storage using OS keyring with encrypted file fallback."""

    def __init__(self, namespace: str = "default"):
        self.namespace = namespace
        self._fernet: Optional[Any] = None
        self._init_fallback()

    def _init_fallback(self) -> None:
        """Initialize encrypted file fallback if keyring unavailable."""
        if not CRYPTO_AVAILABLE:
            logger.warning("Vault: cryptography not available, fallback disabled")
            return

        vdir = _get_vault_dir()
        vdir.mkdir(parents=True, exist_ok=True)
        key_file = vdir / ".vault_key"

        # Load or create fallback encryption key
        if key_file.exists():
            key = key_file.read_bytes()
        else:
            # Try to get master key from keyring
            master_key = None
            if self._use_keyring():
                try:
                    master_key = keyring.get_password(SERVICE_NAME, "vault_master_key")
                except Exception:
                    pass

            if master_key:
                key = master_key.encode()
            else:
                key = Fernet.generate_key()
                if self._use_keyring():
                    try:
                        keyring.set_password(SERVICE_NAME, "vault_master_key", key.decode())
                    except Exception:
                        pass

            key_file.write_bytes(key)
            key_file.chmod(0o600)

        self._fernet = Fernet(key)

    def _use_keyring(self) -> bool:
        """Only use system keyring when not running under isolated test environment."""
        if os.environ.get("AERY_VAULT_DIR") or os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        return KEYRING_AVAILABLE

    def _keyring_key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def _fallback_key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    # ── Public API ──────────────────────────────────────────────────────────

    def set(self, key: str, value: str) -> bool:
        """Store a secret value. Returns True on success."""
        k = self._keyring_key(key)
        fallback_ok = False

        # Try keyring first if not in isolated test
        if self._use_keyring():
            try:
                keyring.set_password(SERVICE_NAME, k, value)
                logger.debug(f"Vault: stored {k} in keyring")
            except Exception as e:
                logger.warning(f"Vault: keyring write failed ({e})")

        # Always write to fallback file as backup
        fallback_ok = self._set_fallback(key, value)
        return fallback_ok

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve a secret value. Returns default if not found."""
        k = self._keyring_key(key)
        if self._use_keyring():
            try:
                val = keyring.get_password(SERVICE_NAME, k)
                if val is not None:
                    logger.debug(f"Vault: retrieved {k} from keyring")
                    return val
            except Exception as e:
                logger.warning(f"Vault: keyring read failed ({e}), trying fallback")

        return self._get_fallback(key, default)

    def delete(self, key: str) -> bool:
        """Delete a secret. Returns True if deleted, False if not found."""
        k = self._keyring_key(key)
        deleted = False
        if self._use_keyring():
            try:
                keyring.delete_password(SERVICE_NAME, k)
                logger.debug(f"Vault: deleted {k} from keyring")
                deleted = True
            except KeyringError:
                pass

        return self._delete_fallback(key) or deleted

    def list_keys(self) -> list[str]:
        """List all keys in this namespace. Reads from fallback file."""
        keys = set()
        data_file = self._get_data_file()
        if data_file.exists():
            try:
                data = self._load_fallback_data()
                prefix = f"{self.namespace}:"
                for k in data.keys():
                    if k.startswith(prefix):
                        keys.add(k[len(prefix) :])
            except Exception:
                pass
        return sorted(keys)

    def clear_namespace(self) -> bool:
        """Delete all keys in this namespace."""
        success = True
        for key in self.list_keys():
            if not self.delete(key):
                success = False
        return success

    # ── Profile-specific helpers ────────────────────────────────────────────

    def set_profile_secret(self, profile_id: str, secret_name: str, value: str) -> bool:
        """Store a secret for a specific profile (e.g., API key)."""
        return self.set(f"profile:{profile_id}:{secret_name}", value)

    def get_profile_secret(self, profile_id: str, secret_name: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve a profile secret."""
        return self.get(f"profile:{profile_id}:{secret_name}", default)

    def delete_profile_secret(self, profile_id: str, secret_name: str) -> bool:
        """Delete a profile secret."""
        return self.delete(f"profile:{profile_id}:{secret_name}")

    def list_profile_secrets(self, profile_id: str) -> list[str]:
        """List all secrets for a profile."""
        prefix = f"profile:{profile_id}:"
        keys = self.list_keys()
        return [k[len(prefix):] for k in keys if k.startswith(prefix)]

    # ── OAuth token bundles ──────────────────────────────────────────────────

    def set_oauth_tokens(self, provider_id: str, access_token: str, refresh_token: str, expires_at: int) -> bool:
        """Store OAuth token bundle for a provider."""
        return self.set(f"oauth:{provider_id}", json.dumps({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        }))

    def get_oauth_tokens(self, provider_id: str) -> Optional[dict]:
        """Retrieve OAuth token bundle for a provider."""
        data = self.get(f"oauth:{provider_id}")
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                pass
        return None

    def delete_oauth_tokens(self, provider_id: str) -> bool:
        """Delete OAuth token bundle for a provider."""
        return self.delete(f"oauth:{provider_id}")

    # ── Fallback encrypted file ─────────────────────────────────────────────

    def _get_data_file(self) -> Path:
        return _get_vault_dir() / "vault.enc"

    def _load_fallback_data(self) -> dict:
        """Load and decrypt fallback data file."""
        data_file = self._get_data_file()
        if not self._fernet or not data_file.exists():
            return {}
        try:
            encrypted = data_file.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            return json.loads(decrypted.decode())
        except Exception as e:
            logger.error(f"Vault: failed to load fallback data: {e}")
            return {}

    def _save_fallback_data(self, data: dict) -> bool:
        """Encrypt and save fallback data file."""
        if not self._fernet:
            return False
        data_file = self._get_data_file()
        try:
            encrypted = self._fernet.encrypt(json.dumps(data).encode())
            data_file.write_bytes(encrypted)
            data_file.chmod(0o600)
            return True
        except Exception as e:
            logger.error(f"Vault: failed to save fallback data: {e}")
            return False

    def _set_fallback(self, key: str, value: str) -> bool:
        """Write a single key to fallback file."""
        data = self._load_fallback_data()
        data[self._fallback_key(key)] = value
        return self._save_fallback_data(data)

    def _get_fallback(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Read a single key from fallback file."""
        data = self._load_fallback_data()
        return data.get(self._fallback_key(key), default)

    def _delete_fallback(self, key: str) -> bool:
        """Delete a single key from fallback file."""
        data = self._load_fallback_data()
        fk = self._fallback_key(key)
        if fk in data:
            del data[fk]
            return self._save_fallback_data(data)
        return False

    # ── Health check ────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        """Return vault health status."""
        keyring_ok = False
        keyring_backend = "none"
        if KEYRING_AVAILABLE:
            try:
                test_key = "__health_check__"
                keyring.set_password(SERVICE_NAME, test_key, "ok")
                val = keyring.get_password(SERVICE_NAME, test_key)
                keyring.delete_password(SERVICE_NAME, test_key)
                if val == "ok":
                    keyring_ok = True
                    keyring_backend = keyring.get_keyring().__class__.__name__
            except Exception:
                keyring_ok = False
                keyring_backend = "error"

        return {
            "keyring_available": KEYRING_AVAILABLE,
            "keyring_working": keyring_ok,
            "keyring_backend": keyring_backend,
            "fallback_available": self._fernet is not None,
        }


# Singleton mapping for namespaces
_vault_instances: dict[str, Vault] = {}


def get_vault(namespace: str = "default") -> Vault:
    """Get or create vault instance for namespace."""
    global _vault_instances
    if namespace in _vault_instances:
        return _vault_instances[namespace]

    vault = Vault(namespace)
    _vault_instances[namespace] = vault
    return vault


def reset_vault() -> None:
    """Reset in-memory singletons without clearing persistent storage."""
    global _vault_instances
    _vault_instances.clear()
