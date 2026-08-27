"""Tests for per-project conversation sessions (dialog + persistence + resume)."""

import json
import os
import time

import pytest

from aery_plugin import session as session_mod
from aery_plugin.agent import Agent


@pytest.fixture
def project_dir(tmp_path):
    return str(tmp_path)


def test_create_append_list_roundtrip(project_dir):
    sid = session_mod.create_session(project_dir)
    session_mod.append_message(project_dir, sid, {"role": "user", "content": "plot cafe in London"})
    session_mod.append_message(project_dir, sid, {"role": "assistant", "content": "Done."})

    sessions = session_mod.list_sessions(project_dir)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["session_id"] == sid
    assert "cafe" in s["first_prompt"]
    assert s["message_count"] >= 2

def test_sessions_are_per_project(tmp_path):
    p1, p2 = str(tmp_path / "a"), str(tmp_path / "b")
    os.makedirs(p1, exist_ok=True)
    os.makedirs(p2, exist_ok=True)

    sid_a = session_mod.create_session(p1)
    sid_b = session_mod.create_session(p2)
    session_mod.append_message(p1, sid_a, {"role": "user", "content": "project A prompt"})

    # Each project lists only its own sessions; A's session is absent from B.
    listed_a = session_mod.list_sessions(p1)
    listed_b = session_mod.list_sessions(p2)
    assert [s["session_id"] for s in listed_a] == [sid_a]
    assert sid_a not in [s["session_id"] for s in listed_b]
    assert sid_b in [s["session_id"] for s in listed_b]


def test_agent_resume_session_restores_history(project_dir):
    sid = session_mod.create_session(project_dir)
    session_mod.append_message(project_dir, sid, {"role": "user", "content": "hello agent"})
    session_mod.append_message(project_dir, sid, {"role": "assistant", "content": "hi human"})

    agent = Agent(None)
    msgs = agent.resume_session(project_dir, sid)
    assert len(msgs) == 2
    assert agent._project_dir == project_dir
    assert agent._session_id == sid
    assert msgs[0]["content"] == "hello agent"


def test_agent_persists_messages_after_resume(project_dir):
    sid = session_mod.create_session(project_dir)
    session_mod.append_message(project_dir, sid, {"role": "user", "content": "first"})
    session_mod.append_message(project_dir, sid, {"role": "assistant", "content": "first reply"})

    agent = Agent(None)
    agent.resume_session(project_dir, sid)
    # New message must append to the same session file
    agent._persist_message({"role": "user", "content": "second"})
    loaded = session_mod.load_session(project_dir, sid)
    assert any(
        m.get("role") == "user" and "second" in str(m.get("content"))
        for m in loaded
    )

def test_dialog_sources_persisted_sessions(project_dir, monkeypatch):
    """The sessions dialog surfaces the per-project persisted sessions."""
    sid = session_mod.create_session(project_dir)
    session_mod.append_message(project_dir, sid, {"role": "user", "content": "resume me please"})

    # Verify list_sessions (what _populate_sessions consumes) carries the data
    # the dialog renders: session_id, first_prompt, message_count.
    sessions = session_mod.list_sessions(project_dir)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["session_id"] == sid
    assert "resume me please" in s["first_prompt"]
    assert s["message_count"] >= 1

    # The dialog is per-project: project_dir drives which sessions are listed.
    from aery_plugin import provider_settings as ps
    assert hasattr(ps.SessionSwitcherDialog, "_populate_sessions")
    assert ps.SessionSwitcherDialog._switch_to_session.__doc__ and "Resume" in ps.SessionSwitcherDialog._switch_to_session.__doc__


def test_resume_flow_end_to_end(project_dir):
    """Full flow: persist -> resume -> agent continues in same session."""
    sid = session_mod.create_session(project_dir)
    session_mod.append_message(project_dir, sid, {"role": "user", "content": "make layer X"})
    session_mod.append_message(project_dir, sid, {"role": "assistant", "content": "made layer X"})

    agent = Agent(None)
    agent.resume_session(project_dir, sid)
    history = agent.get_history()
    assert history[-1]["content"] == "made layer X"
    # A follow-up persists into the same session
    agent._persist_message({"role": "user", "content": "now style layer X"})
    reloaded = session_mod.load_session(project_dir, sid)
    assert any("now style layer X" in str(m.get("content")) for m in reloaded)
