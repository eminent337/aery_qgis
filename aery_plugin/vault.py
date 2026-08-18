"""OS keyring-backed credential vault with AES-256-GCM encryption."""

from __future__ import annotations

import json
import os
import base64
from pathlib import Path
from typing import Any, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import hashlib

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

from aery_plugin.logger import logger

SERVICE_NAME = "aery_qgis"
KEK_KEYRING_ID = "vault_kek"  # Key Encryption Key identifier in keyring
SALT_SIZE = 16
KEK_SIZE = 32  # 256-bit KEK
DEK_SIZE = 32  # 256-bit DEK for AES-256-GCM
PBKDF2_ITERATIONS = 480000  # NIST recommended minimum


def _get_vault_dir() -> Path:
    env_dir = os.environ.get("AERY_VAULT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".local" / "share" / "aery_qgis" / "vault"


def _fallback_key_file():
    return _get_vault_dir() / ".vault_kek"


def _fallback_data_file():
    return _get_vault_dir() / "vault.enc"


class Vault:
    """Secure credential storage using OS keyring with AES-256-GCM fallback."""

    def __init__(self, namespace: str = "default"):
        self.namespace = namespace
        self._kek: Optional[bytes] = None
        self._dek: Optional[bytes] = None
        self._salt: Optional[bytes] = None
        self._init_keys()

    def _init_keys(self) -> None:
        """Initialize KEK and derive namespace-specific DEK."""
        vdir = _get_vault_dir()
        vdir.mkdir(parents=True, exist_ok=True)
        key_file = vdir / ".vault_kek"

        # Load or create KEK (shared across namespaces)
        if key_file.exists():
            self._kek = key_file.read_bytes()
        else:
            self._kek = None
            if self._use_keyring():
                try:
                    kek_str = keyring.get_password(SERVICE_NAME, KEK_KEYRING_ID)
                    if kek_str:
                        self._kek = base64.b64decode(kek_str)
                except Exception:
                    pass

            if self._kek is None or len(self._kek) != KEK_SIZE:
                self._kek = os.urandom(KEK_SIZE)
                if self._use_keyring():
                    try:
                        keyring.set_password(
                            SERVICE_NAME, KEK_KEYRING_ID,
                            base64.b64encode(self._kek).decode()
                        )
                    except Exception:
                        pass
                key_file.write_bytes(self._kek)
                key_file.chmod(0o600)

        # Load or create shared salt (single file, multiple namespaces)
        salt_file = vdir / ".vault_salt"
        if salt_file.exists():
            self._salt = salt_file.read_bytes()
        else:
            self._salt = os.urandom(SALT_SIZE)
            salt_file.write_bytes(self._salt)
            salt_file.chmod(0o600)

        # Derive namespace-specific DEK from KEK + salt + namespace
        self._derive_dek()

    def _derive_dek(self) -> None:
        """Derive namespace-specific DEK from KEK, salt, and namespace."""
        # Include namespace in the derivation context for per-namespace isolation
        context = f"{self.namespace}:aery_qgis".encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=DEK_SIZE,
            salt=self._salt,
            iterations=PBKDF2_ITERATIONS,
        )
        self._dek = kdf.derive(self._kek + context)

    def _use_keyring(self) -> bool:
        """Only use system keyring when not running under isolated test environment."""
        if os.environ.get("AERY_VAULT_DIR") or os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        return KEYRING_AVAILABLE

    def _keyring_key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def _fallback_key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def set(self, key: str, value: str) -> bool:
        """Store a value using AES-256-GCM encryption."""
        fk = self._keyring_key(key)

        # Try keyring first (if available)
        if self._use_keyring():
            try:
                keyring.set_password(SERVICE_NAME, fk, value)
                return True
            except Exception as e:
                logger.debug(f"Vault: keyring set failed for {fk}: {e}")

        # Fallback to encrypted file
        return self._set_fallback(key, value)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve a value, checking keyring then fallback."""
        fk = self._keyring_key(key)

        # Try keyring first
        if self._use_keyring():
            try:
                value = keyring.get_password(SERVICE_NAME, fk)
                if value is not None:
                    return value
            except Exception:
                pass

        # Fallback to encrypted file
        return self._get_fallback(key, default)

    def delete(self, key: str) -> bool:
        """Delete a key from both keyring and fallback."""
        fk = self._keyring_key(key)
        deleted = False

        if self._use_keyring():
            try:
                keyring.delete_password(SERVICE_NAME, fk)
                deleted = True
            except Exception:
                pass

        return self._delete_fallback(key) or deleted

    def list_keys(self) -> list[str]:
        """List all keys in the namespace (from fallback file)."""
        return list(self._load_fallback_data().keys())

    def clear_namespace(self) -> bool:
        """Clear all keys in this namespace."""
        # Clear keyring entries
        if self._use_keyring():
            try:
                for key in self.list_keys():
                    keyring.delete_password(SERVICE_NAME, self._keyring_key(key))
            except Exception:
                pass

        return self._save_fallback_data({})

    def set_profile_secret(self, profile_id: str, secret_name: str, value: str) -> bool:
        return self.set(f"profile:{profile_id}:{secret_name}", value)

    def get_profile_secret(self, profile_id: str, secret_name: str, default: Optional[str] = None) -> Optional[str]:
        return self.get(f"profile:{profile_id}:{secret_name}", default)

    def delete_profile_secret(self, profile_id: str, secret_name: str) -> bool:
        return self.delete(f"profile:{profile_id}:{secret_name}")

    def list_profile_secrets(self, profile_id: str) -> list[str]:
        # Keys are stored as {namespace}:profile:{profile_id}:{secret_name}
        prefix = f"{self.namespace}:profile:{profile_id}:"
        return [k[len(prefix):] for k in self.list_keys() if k.startswith(prefix)]

    def set_oauth_tokens(self, provider_id: str, access_token: str, refresh_token: str, expires_at: int) -> bool:
        tokens = json.dumps({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        })
        return self.set(f"oauth:{provider_id}", tokens)

    def get_oauth_tokens(self, provider_id: str) -> Optional[dict]:
        data = self.get(f"oauth:{provider_id}")
        if data:
            return json.loads(data)
        return None

    def delete_oauth_tokens(self, provider_id: str) -> bool:
        return self.delete(f"oauth:{provider_id}")

    # ── Encrypted fallback storage ────────────────────────────────────────

    def _get_data_file(self) -> Path:
        """Return namespace-specific data file."""
        return _get_vault_dir() / f"vault_{self.namespace}.enc"

    def _load_fallback_data(self) -> dict:
        """Load and decrypt fallback data."""
        data_file = self._get_data_file()
        if not data_file.exists():
            return {}

        try:
            encrypted = data_file.read_bytes()
            # Format: salt(16) | ciphertext
            if len(encrypted) < SALT_SIZE:
                return {}
            salt = encrypted[:SALT_SIZE]
            ciphertext = encrypted[SALT_SIZE:]

            # Re-derive DEK with loaded salt
            original_salt = self._salt
            self._salt = salt
            self._derive_dek()

            # Decrypt with AES-256-GCM
            aesgcm = AESGCM(self._dek)
            plaintext = aesgcm.decrypt(self._salt, ciphertext, None)
            return json.loads(plaintext.decode())
        except Exception as e:
            logger.warning(f"Vault: failed to load fallback data: {e}")
            return {}
        finally:
            self._salt = original_salt

    def _save_fallback_data(self, data: dict) -> bool:
        """Encrypt and save fallback data."""
        data_file = self._get_data_file()
        try:
            plaintext = json.dumps(data).encode()
            aesgcm = AESGCM(self._dek)
            ciphertext = aesgcm.encrypt(self._salt, plaintext, None)

            # Read existing salt from file to preserve it
            existing = b''
            if data_file.exists():
                existing = data_file.read_bytes()[:SALT_SIZE]
            write_salt = existing if len(existing) == SALT_SIZE else self._salt

            # Write: salt | ciphertext
            data_file.write_bytes(write_salt + ciphertext)
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
        return {
            "namespace": self.namespace,
            "keyring_available": KEYRING_AVAILABLE,
            "keyring_in_use": self._use_keyring(),
            "fallback_available": _fallback_data_file().exists() or os.environ.get("AERY_VAULT_DIR"),
            "keys_count": len(self.list_keys()),
            "encryption": "AES-256-GCM",
            "kek_source": "keyring" if self._use_keyring() else "file",
        }


# Singleton mapping for namespaces
_vault_instances: dict[str, Vault] = {}


def get_vault(namespace: str = "default") -> Vault:
    """Get or create vault instance for namespace."""
    if namespace not in _vault_instances:
        _vault_instances[namespace] = Vault(namespace)
    return _vault_instances[namespace]


def reset_vault() -> None:
    """Reset in-memory singletons without clearing persistent storage."""
    global _vault_instances
    _vault_instances.clear()
