#!/usr/bin/env python3
"""SQLite-backed session store with branching support for Aery QGIS Plugin.

Replaces JSONL sessions with SQLite for better querying, branching, and export.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from aery_plugin.logger import logger


@dataclass
class Session:
    """Represents an AI chat session."""
    id: str
    project_dir: str
    name: str
    parent_id: Optional[str] = None
    branch_name: str = "main"
    messages: list[dict] = field(default_factory=list)
    agent_state: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    
    def to_json(self) -> dict:
        return {
            "id": self.id,
            "project_dir": self.project_dir,
            "name": self.name,
            "parent_id": self.parent_id,
            "branch_name": self.branch_name,
            "messages": self.messages,
            "agent_state": self.agent_state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_json(cls, data: dict) -> "Session":
        return cls(**data)


DEFAULT_SESSIONS_DIR = Path.home() / ".local" / "share" / "aery_qgis" / "sessions"
DB_FILE = DEFAULT_SESSIONS_DIR / "sessions.db"


def get_sessions_dir() -> Path:
    """Return the sessions directory, creating it if needed."""
    DEFAULT_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_SESSIONS_DIR


@contextmanager
def _db_connection():
    """Context manager for database connections."""
    get_sessions_dir()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _init_db():
    """Initialize the database schema."""
    with _db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                project_dir TEXT NOT NULL,
                name TEXT NOT NULL,
                parent_id TEXT,
                branch_name TEXT DEFAULT 'main',
                messages TEXT NOT NULL DEFAULT '[]',
                agent_state TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (parent_id) REFERENCES sessions(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_project_dir 
            ON sessions(project_dir)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_parent_id 
            ON sessions(parent_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_branch_name 
            ON sessions(branch_name)
        """)
        conn.commit()


def _row_to_session(row: sqlite3.Row) -> Session:
    """Convert a database row to a Session object."""
    return Session(
        id=row["id"],
        project_dir=row["project_dir"],
        name=row["name"],
        parent_id=row["parent_id"],
        branch_name=row["branch_name"],
        messages=json.loads(row["messages"]) if row["messages"] else [],
        agent_state=json.loads(row["agent_state"]) if row["agent_state"] else {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_session(project_dir: str, name: Optional[str] = None, parent_id: Optional[str] = None) -> Session:
    """Create a new session."""
    _init_db()
    
    session_id = str(uuid.uuid4())
    now = time.time()
    
    if name is None:
        name = f"Session {datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M')}"
    
    branch_name = "main"
    if parent_id:
        # Get parent session to inherit branch name
        with _db_connection() as conn:
            parent_row = conn.execute("SELECT branch_name FROM sessions WHERE id = ?", (parent_id,)).fetchone()
            if parent_row:
                branch_name = parent_row["branch_name"]
    
    session = Session(
        id=session_id,
        project_dir=project_dir,
        name=name,
        parent_id=parent_id,
        branch_name=branch_name,
        messages=[],
        agent_state={},
        created_at=now,
        updated_at=now,
    )
    
    with _db_connection() as conn:
        conn.execute("""
            INSERT INTO sessions (id, project_dir, name, parent_id, branch_name, messages, agent_state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session.id,
            session.project_dir,
            session.name,
            session.parent_id,
            session.branch_name,
            json.dumps(session.messages),
            json.dumps(session.agent_state),
            session.created_at,
            session.updated_at,
        ))
        conn.commit()
    
    logger.info(f"Created session {session_id} for project {project_dir}")
    return session


def load_session(session_id: str) -> Optional[Session]:
    """Load a session by ID."""
    _init_db()
    
    with _db_connection() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row:
            return _row_to_session(row)
        return None


def save_session(session: Session) -> bool:
    """Save/update a session."""
    _init_db()
    
    session.updated_at = time.time()
    
    with _db_connection() as conn:
        conn.execute("""
            UPDATE sessions 
            SET name = ?, parent_id = ?, branch_name = ?, messages = ?, agent_state = ?, updated_at = ?
            WHERE id = ?
        """, (
            session.name,
            session.parent_id,
            session.branch_name,
            json.dumps(session.messages),
            json.dumps(session.agent_state),
            session.updated_at,
            session.id,
        ))
        conn.commit()
    
    return True


def delete_session(session_id: str) -> bool:
    """Delete a session."""
    _init_db()
    
    with _db_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
    
    return True


def list_sessions(project_dir: Optional[str] = None, branch_name: Optional[str] = None) -> list[Session]:
    """List all sessions, optionally filtered by project_dir and/or branch_name."""
    _init_db()
    
    query = "SELECT * FROM sessions WHERE 1=1"
    params = []
    
    if project_dir:
        query += " AND project_dir = ?"
        params.append(project_dir)
    if branch_name:
        query += " AND branch_name = ?"
        params.append(branch_name)
    
    query += " ORDER BY updated_at DESC"
    
    with _db_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_session(row) for row in rows]


def fork_session(session_id: str, new_name: Optional[str] = None, new_branch_name: Optional[str] = None) -> Optional[Session]:
    """Create a new session forked from an existing one."""
    _init_db()
    
    parent = load_session(session_id)
    if not parent:
        return None
    
    # Create new session with parent as parent_id
    new_branch = new_branch_name or f"{parent.branch_name}-fork-{int(time.time())}"
    child = create_session(
        project_dir=parent.project_dir,
        name=new_name or f"{parent.name} (fork)",
        parent_id=session_id,
    )
    child.branch_name = new_branch
    child.messages = list(parent.messages)  # Copy messages
    child.agent_state = dict(parent.agent_state)  # Copy agent state
    
    # Save the updated session
    save_session(child)
    
    logger.info(f"Forked session {session_id} -> {child.id} on branch {new_branch}")
    return child


def export_session(session_id: str) -> Optional[dict]:
    """Export a session to a portable JSON format."""
    session = load_session(session_id)
    if not session:
        return None
    
    return {
        "version": 1,
        "exported_at": time.time(),
        "session": session.to_json(),
    }


def import_session(data: dict, project_dir: Optional[str] = None) -> Optional[Session]:
    """Import a session from exported JSON."""
    if "session" not in data:
        return None
    
    session_data = data["session"]
    
    # Override project_dir if provided
    if project_dir:
        session_data["project_dir"] = project_dir
    
    session = Session.from_json(session_data)
    
    # Assign new ID to avoid conflicts
    session.id = str(uuid.uuid4())
    session.created_at = time.time()
    session.updated_at = time.time()
    
    with _db_connection() as conn:
        conn.execute("""
            INSERT INTO sessions (id, project_dir, name, parent_id, branch_name, messages, agent_state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session.id,
            session.project_dir,
            session.name,
            session.parent_id,
            session.branch_name,
            json.dumps(session.messages),
            json.dumps(session.agent_state),
            session.created_at,
            session.updated_at,
        ))
        conn.commit()
    
    logger.info(f"Imported session as {session.id}")
    return session


def get_branch_history(session_id: str) -> list[Session]:
    """Get the full branch history (ancestors) of a session."""
    _init_db()
    
    history = []
    current_id = session_id
    
    while current_id:
        session = load_session(current_id)
        if not session:
            break
        history.append(session)
        current_id = session.parent_id
    
    return history  # Returns from leaf to root


def get_branch_sessions(branch_name: str, project_dir: Optional[str] = None) -> list[Session]:
    """Get all sessions in a branch."""
    return list_sessions(project_dir=project_dir, branch_name=branch_name)


def migrate_jsonl_sessions(jsonl_dir: Path) -> int:
    """Migrate existing JSONL sessions to SQLite.
    
    Args:
        jsonl_dir: Directory containing session JSONL files
        
    Returns:
        Number of sessions migrated
    """
    _init_db()
    
    if not jsonl_dir.exists():
        return 0
    
    migrated = 0
    for jsonl_file in jsonl_dir.glob("*.jsonl"):
        try:
            with open(jsonl_file, "r") as f:
                lines = f.readlines()
            
            if not lines:
                continue
            
            # First line is usually the session header
            session_data = json.loads(lines[0])
            
            # Parse messages from subsequent lines
            messages = []
            for line in lines[1:]:
                try:
                    msg = json.loads(line.strip())
                    if isinstance(msg, dict) and "role" in msg:
                        messages.append(msg)
                except json.JSONDecodeError:
                    pass
            
            # Create session
            session_id = session_data.get("id", str(uuid.uuid4()))
            project_dir = session_data.get("project_dir", "")
            name = session_data.get("name", f"Migrated from {jsonl_file.stem}")
            
            session = Session(
                id=session_id,
                project_dir=project_dir,
                name=name,
                messages=messages,
                agent_state=session_data.get("agent_state", {}),
                created_at=session_data.get("created_at", time.time()),
                updated_at=session_data.get("updated_at", time.time()),
            )
            
            with _db_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sessions (id, project_dir, name, parent_id, branch_name, messages, agent_state, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session.id,
                    session.project_dir,
                    session.name,
                    session.parent_id,
                    session.branch_name,
                    json.dumps(session.messages),
                    json.dumps(session.agent_state),
                    session.created_at,
                    session.updated_at,
                ))
                conn.commit()
            
            migrated += 1
            logger.info(f"Migrated session {session_id} from {jsonl_file}")
            
        except Exception as e:
            logger.error(f"Failed to migrate {jsonl_file}: {e}")
    
    return migrated