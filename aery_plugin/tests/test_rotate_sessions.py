import pytest
import os
import json
import time
from aery_plugin.session import rotate_sessions, append_message, create_session, list_sessions

def test_rotate_sessions(tmp_path):
    project_dir = str(tmp_path)
    # Create 55 sessions
    for i in range(55):
        sid = create_session(project_dir)
        # Sleep slightly so mtime differs if on fast disk, or mock it
        append_message(project_dir, sid, {"role": "user", "content": f"msg {i}"})
        
    sessions = list_sessions(project_dir)
    assert len(sessions) == 55
    
    rotate_sessions(project_dir, max_sessions=50)
    
    sessions_after = list_sessions(project_dir)
    assert len(sessions_after) == 50
