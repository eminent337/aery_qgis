"""Session persistence for the Aery QGIS agent.

Uses JSONL (append-only) format inspired by OpenClaude:
- One JSON object per line, crash-safe
- Head/tail reads for session listing (no full file load)
- Token-based truncation (keeps last N messages)
- Skips large tool outputs (canvas images)
- Max file size guard (1MB)

Session files are stored in: <project_dir>/.aery/sessions/<session_id>.jsonl
"""

import json
import os
import time
import uuid
from typing import Optional


# Max bytes to read for session listing (head + tail)
HEAD_TAIL_READ_SIZE = 32 * 1024  # 32KB

# Max session file size before we stop appending
MAX_SESSION_BYTES = 1 * 1024 * 1024  # 1MB

# Max messages to keep when truncating
MAX_MESSAGES = 100

# Max text length per message to persist (skip huge tool outputs)
MAX_MESSAGE_TEXT = 4000


def _sessions_dir(project_dir: str) -> str:
    """Get the sessions directory for a project."""
    d = os.path.join(project_dir, ".aery", "sessions")
    os.makedirs(d, exist_ok=True)
    return d


def _session_path(project_dir: str, session_id: str) -> str:
    """Get the file path for a session."""
    return os.path.join(_sessions_dir(project_dir), f"{session_id}.jsonl")


def _sanitize_message(msg: dict) -> dict:
    """Sanitize a message for persistence — skip large tool outputs."""
    sanitized = dict(msg)

    # Skip canvas/image data (base64 is huge)
    content = sanitized.get("content", "")
    if isinstance(content, str) and len(content) > MAX_MESSAGE_TEXT:
        sanitized["content"] = content[:MAX_MESSAGE_TEXT] + "... [truncated]"
        sanitized["_truncated"] = True

    # Skip tool output with large results
    if sanitized.get("type") == "tool_result":
        result = sanitized.get("result", "")
        if isinstance(result, str) and len(result) > MAX_MESSAGE_TEXT:
            sanitized["result"] = result[:MAX_MESSAGE_TEXT] + "... [truncated]"
            sanitized["_truncated"] = True

    return sanitized


def create_session(project_dir: str) -> str:
    """Create a new session and return its ID."""
    session_id = uuid.uuid4().hex[:12]
    path = _session_path(project_dir, session_id)

    # Write session header
    header = {
        "type": "session_start",
        "session_id": session_id,
        "timestamp": time.time(),
        "project_dir": project_dir,
    }
    with open(path, "w") as f:
        f.write(json.dumps(header) + "\n")

    return session_id


def append_message(project_dir: str, session_id: str, msg: dict) -> bool:
    """Append a message to the session file. Returns False if file is too large."""
    path = _session_path(project_dir, session_id)

    # Check file size
    try:
        if os.path.getsize(path) > MAX_SESSION_BYTES:
            return False
    except OSError:
        pass

    sanitized = _sanitize_message(msg)
    sanitized["_ts"] = time.time()

    try:
        with open(path, "a") as f:
            f.write(json.dumps(sanitized) + "\n")
        return True
    except OSError:
        return False


def load_session(project_dir: str, session_id: str, max_messages: int = MAX_MESSAGES) -> list[dict]:
    """Load messages from a session file.

    Uses head/tail strategy:
    - If file is small, load everything
    - If file is large, load head (for context) + tail (for recent messages)
    - Returns at most max_messages
    """
    path = _session_path(project_dir, session_id)
    if not os.path.exists(path):
        return []

    try:
        file_size = os.path.getsize(path)
    except OSError:
        return []

    messages = []

    if file_size <= HEAD_TAIL_READ_SIZE * 2:
        # Small file — load everything
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    else:
        # Large file — read head + tail
        with open(path, "rb") as f:
            # Read head
            head_bytes = f.read(HEAD_TAIL_READ_SIZE)
            head_text = head_bytes.decode("utf-8", errors="replace")

            # Read tail
            f.seek(max(0, file_size - HEAD_TAIL_READ_SIZE))
            tail_bytes = f.read(HEAD_TAIL_READ_SIZE)
            tail_text = tail_bytes.decode("utf-8", errors="replace")

        # Parse head lines
        for line in head_text.split("\n"):
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        # Parse tail lines (skip first partial line if it's a duplicate)
        tail_lines = tail_text.split("\n")
        for line in tail_lines[1:]:  # skip first (may be partial)
            line = line.strip()
            if line:
                try:
                    msg = json.loads(line)
                    # Deduplicate: skip if already in head
                    if not any(m.get("_ts") == msg.get("_ts") and m.get("role") == msg.get("role")
                              for m in messages[-5:]):
                        messages.append(msg)
                except json.JSONDecodeError:
                    continue

    # Keep only the last max_messages
    if len(messages) > max_messages:
        messages = messages[-max_messages:]

    return messages


def list_sessions(project_dir: str) -> list[dict]:
    """List all sessions for a project using head reads only.

    Returns list of {session_id, first_prompt, timestamp, message_count}.
    """
    sessions_dir = _sessions_dir(project_dir)
    if not os.path.exists(sessions_dir):
        return []

    results = []
    for fname in os.listdir(sessions_dir):
        if not fname.endswith(".jsonl"):
            continue

        session_id = fname[:-6]  # strip .jsonl
        path = os.path.join(sessions_dir, fname)

        try:
            file_size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
        except OSError:
            continue

        # Read head only for metadata
        try:
            with open(path, "r") as f:
                head = f.read(HEAD_TAIL_READ_SIZE)
        except OSError:
            continue

        # Extract first user message as preview
        first_prompt = ""
        message_count = 0
        for line in head.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if msg.get("role") == "user" and not first_prompt:
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        first_prompt = content[:100]
                if msg.get("role") in ("user", "assistant"):
                    message_count += 1
            except json.JSONDecodeError:
                continue

        # Estimate total message count from file size (rough: ~200 bytes per message)
        if file_size > HEAD_TAIL_READ_SIZE:
            message_count = max(message_count, file_size // 200)

        results.append({
            "session_id": session_id,
            "first_prompt": first_prompt,
            "timestamp": mtime,
            "message_count": message_count,
            "file_size": file_size,
        })

    # Sort by most recent first
    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results


def delete_session(project_dir: str, session_id: str) -> bool:
    """Delete a session file."""
    path = _session_path(project_dir, session_id)
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def get_latest_session(project_dir: str) -> Optional[str]:
    """Get the most recent session ID, or None if no sessions exist."""
    sessions = list_sessions(project_dir)
    return sessions[0]["session_id"] if sessions else None
