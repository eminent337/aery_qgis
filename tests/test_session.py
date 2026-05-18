"""Tests for the session persistence module."""

import json
import os
import tempfile
import time

import pytest

from aery_plugin.session import (
    create_session,
    append_message,
    load_session,
    list_sessions,
    delete_session,
    get_latest_session,
    MAX_SESSION_BYTES,
    MAX_MESSAGE_TEXT,
)


@pytest.fixture
def project_dir(tmp_path):
    return str(tmp_path)


class TestCreateSession:
    def test_creates_session_file(self, project_dir):
        session_id = create_session(project_dir)
        assert session_id
        path = os.path.join(project_dir, ".aery", "sessions", f"{session_id}.jsonl")
        assert os.path.exists(path)

    def test_session_header(self, project_dir):
        session_id = create_session(project_dir)
        path = os.path.join(project_dir, ".aery", "sessions", f"{session_id}.jsonl")
        with open(path) as f:
            header = json.loads(f.readline())
        assert header["type"] == "session_start"
        assert header["session_id"] == session_id
        assert "timestamp" in header


class TestAppendMessage:
    def test_appends_message(self, project_dir):
        session_id = create_session(project_dir)
        result = append_message(project_dir, session_id, {"role": "user", "content": "hello"})
        assert result is True

        messages = load_session(project_dir, session_id)
        assert len(messages) == 2  # header + user message
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "hello"

    def test_truncates_large_content(self, project_dir):
        session_id = create_session(project_dir)
        large_content = "x" * 10000
        append_message(project_dir, session_id, {"role": "user", "content": large_content})

        messages = load_session(project_dir, session_id)
        user_msg = [m for m in messages if m.get("role") == "user"][0]
        assert len(user_msg["content"]) < len(large_content)
        assert user_msg.get("_truncated") is True

    def test_skips_when_file_too_large(self, project_dir):
        session_id = create_session(project_dir)
        path = os.path.join(project_dir, ".aery", "sessions", f"{session_id}.jsonl")

        # Write a large file
        with open(path, "a") as f:
            for _ in range(1000):
                f.write(json.dumps({"role": "user", "content": "x" * 2000}) + "\n")

        result = append_message(project_dir, session_id, {"role": "user", "content": "new"})
        assert result is False


class TestLoadSession:
    def test_loads_all_messages(self, project_dir):
        session_id = create_session(project_dir)
        for i in range(5):
            append_message(project_dir, session_id, {"role": "user", "content": f"msg {i}"})

        messages = load_session(project_dir, session_id)
        assert len(messages) == 6  # header + 5 messages

    def test_limits_messages(self, project_dir):
        session_id = create_session(project_dir)
        for i in range(200):
            append_message(project_dir, session_id, {"role": "user", "content": f"msg {i}"})

        messages = load_session(project_dir, session_id, max_messages=10)
        assert len(messages) == 10

    def test_returns_empty_for_missing_session(self, project_dir):
        messages = load_session(project_dir, "nonexistent")
        assert messages == []


class TestListSessions:
    def test_lists_sessions(self, project_dir):
        id1 = create_session(project_dir)
        time.sleep(0.01)
        id2 = create_session(project_dir)

        sessions = list_sessions(project_dir)
        assert len(sessions) == 2
        # Most recent first
        assert sessions[0]["session_id"] == id2
        assert sessions[1]["session_id"] == id1

    def test_extracts_first_prompt(self, project_dir):
        session_id = create_session(project_dir)
        append_message(project_dir, session_id, {"role": "user", "content": "buffer all roads"})

        sessions = list_sessions(project_dir)
        assert sessions[0]["first_prompt"] == "buffer all roads"

    def test_returns_empty_for_no_sessions(self, project_dir):
        sessions = list_sessions(project_dir)
        assert sessions == []


class TestDeleteSession:
    def test_deletes_session(self, project_dir):
        session_id = create_session(project_dir)
        assert delete_session(project_dir, session_id) is True
        assert not os.path.exists(
            os.path.join(project_dir, ".aery", "sessions", f"{session_id}.jsonl")
        )

    def test_returns_false_for_missing(self, project_dir):
        assert delete_session(project_dir, "nonexistent") is False


class TestGetLatestSession:
    def test_returns_latest(self, project_dir):
        id1 = create_session(project_dir)
        time.sleep(0.01)
        id2 = create_session(project_dir)

        assert get_latest_session(project_dir) == id2

    def test_returns_none_when_empty(self, project_dir):
        assert get_latest_session(project_dir) is None
