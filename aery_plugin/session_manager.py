"""Session Manager for multi-session isolation in Aery QGIS Plugin.

Provides SessionManager and SessionContext for isolating executors, agents,
vault namespaces, and telemetry per session - enabling multi-user/tenant support.
"""

from __future__ import annotations

import uuid
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from pathlib import Path

from aery_plugin.logger import logger

try:
    from aery_plugin.vault import get_vault
    HAS_VAULT = True
except ImportError:
    HAS_VAULT = False


@dataclass
class SessionContext:
    """Context for a single session with isolated components."""
    session_id: str
    executor: Any = None  # QGISCodeExecutor instance
    agent: Any = None     # Agent instance
    vault_namespace: str = ""
    telemetry_enabled: bool = True
    created_at: float = 0.0
    last_accessed: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def touch(self):
        """Update last_accessed timestamp."""
        import time
        self.last_accessed = time.time()
    
    def get_vault(self):
        """Get the vault for this session's namespace."""
        if not HAS_VAULT:
            return None
        return get_vault(self.vault_namespace or self.session_id)


class SessionManager:
    """Manages multiple sessions with isolation guarantees."""
    
    def __init__(self):
        self._sessions: Dict[str, SessionContext] = {}
        self._lock = threading.RLock()
        self._active_session_id: Optional[str] = None
        self._max_sessions = 10
        self._session_ttl_seconds = 3600  # 1 hour idle timeout
    
    def create_session(
        self,
        executor=None,
        agent=None,
        vault_namespace: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a new session and return its ID."""
        with self._lock:
            # Evict expired sessions if at capacity
            if len(self._sessions) >= self._max_sessions:
                self._evict_expired()
            
            session_id = str(uuid.uuid4())
            ns = vault_namespace or session_id
            
            context = SessionContext(
                session_id=session_id,
                executor=executor,
                agent=agent,
                vault_namespace=ns,
                created_at=__import__("time").time(),
                last_accessed=__import__("time").time(),
                metadata=metadata or {},
            )
            
            self._sessions[session_id] = context
            
            logger.info(f"Created session {session_id[:8]} (ns={ns})")
            return session_id
    
    def get_session(self, session_id: str) -> Optional[SessionContext]:
        """Get a session by ID."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.touch()
            return session
    
    def get_active_session(self) -> Optional[SessionContext]:
        """Get the active session."""
        with self._lock:
            if self._active_session_id:
                return self.get_session(self._active_session_id)
            # Return most recently accessed
            if self._sessions:
                return max(self._sessions.values(), key=lambda s: s.last_accessed)
            return None
    
    def set_active_session(self, session_id: str) -> bool:
        """Set the active session."""
        with self._lock:
            if session_id in self._sessions:
                self._active_session_id = session_id
                self._sessions[session_id].touch()
                return True
            return False
    
    def remove_session(self, session_id: str) -> bool:
        """Remove a session."""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                if self._active_session_id == session_id:
                    self._active_session_id = None
                return True
            return False
    
    def list_sessions(self) -> list[dict]:
        """List all sessions with their IDs and metadata."""
        with self._lock:
            result = []
            for sid, ctx in self._sessions.items():
                result.append({
                    "session_id": sid,
                    "vault_namespace": ctx.vault_namespace,
                    "created_at": ctx.created_at,
                    "last_accessed": ctx.last_accessed,
                    "is_active": sid == self._active_session_id,
                    "metadata": ctx.metadata,
                })
            return result
    
    def _evict_expired(self):
        """Remove sessions that have exceeded TTL."""
        import time
        now = time.time()
        expired = [
            sid for sid, ctx in self._sessions.items()
            if now - ctx.last_accessed > self._session_ttl_seconds
        ]
        for sid in expired:
            del self._sessions[sid]
            logger.info(f"Evicted expired session {sid[:8]}")
    
    def cleanup(self):
        """Shutdown all sessions."""
        with self._lock:
            self._sessions.clear()
            self._active_session_id = None


# Global session manager instance
_manager: Optional[SessionManager] = None
_manager_lock = threading.Lock()


def get_session_manager() -> SessionManager:
    """Get or create the global session manager."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = SessionManager()
        return _manager


def create_session(**kwargs) -> str:
    """Create a new session."""
    return get_session_manager().create_session(**kwargs)


def get_session(session_id: str) -> Optional[SessionContext]:
    """Get a session by ID."""
    return get_session_manager().get_session(session_id)


def get_active_session() -> Optional[SessionContext]:
    """Get the active session."""
    return get_session_manager().get_active_session()


def set_active_session(session_id: str) -> bool:
    """Set the active session."""
    return get_session_manager().set_active_session(session_id)


def remove_session(session_id: str) -> bool:
    """Remove a session."""
    return get_session_manager().remove_session(session_id)