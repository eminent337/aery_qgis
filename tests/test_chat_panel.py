"""Tests for ChatPanel — simplified settings-menu design."""

import json
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QTextEdit, QToolButton

from aery_plugin.chat_panel import ChatPanel, MessageBubble, ToolBlock


@pytest.fixture(scope="session")
def qapp():
    """Create a QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def panel(qapp):
    """Create a ChatPanel with mocked dependencies."""
    iface = MagicMock()
    rpc_bridge = MagicMock()
    rpc_bridge.event_received = MagicMock()
    rpc_bridge.response_received = MagicMock()
    rpc_bridge.error_occurred = MagicMock()
    rpc_bridge.process_exited = MagicMock()
    rpc_bridge.event_received.connect = MagicMock()
    rpc_bridge.response_received.connect = MagicMock()
    rpc_bridge.error_occurred.connect = MagicMock()
    rpc_bridge.process_exited.connect = MagicMock()

    with patch("qgis.core.QgsProject") as mock_proj:
        mock_proj.instance.return_value.fileName.return_value = "/tmp/test_project.qgz"
        p = ChatPanel(iface, rpc_bridge)
        yield p
        p.close()


def test_panel_created(panel):
    """Panel is a QDockWidget with compact QGIS-style width."""
    assert panel.windowTitle() == "Aery"
    assert panel.minimumWidth() >= 260
    assert panel.minimumWidth() <= 280


def test_input_field_exists(panel):
    """Panel has a multiline input field with placeholder and larger input area."""
    assert panel._input is not None
    assert isinstance(panel._input, QTextEdit)
    assert "geospatial" in panel._input.placeholderText().lower()
    assert panel._input.minimumHeight() >= 44


def test_activity_frame_exists(panel):
    """Panel has an activity strip that starts hidden."""
    assert panel._activity_frame is not None
    assert panel._activity_star is not None
    assert not panel._activity_frame.isVisible()


def test_gear_button_exists(panel):
    """Panel has a gear settings button."""
    assert panel._gear_btn is not None
    assert isinstance(panel._gear_btn, QToolButton)


def test_dock_button_exists(panel):
    """Panel has a dock/undock button."""
    assert panel._dock_btn is not None
    assert isinstance(panel._dock_btn, QToolButton)


def test_activity_label_tracks_agent_state(panel):
    """Agent events update the activity strip."""
    panel._on_event({"type": "message_start", "message": {"role": "assistant"}})
    assert "thinking" in panel._activity_label.text().lower()

    panel._on_event({"type": "tool_execution_start", "tool": "run_processing"})
    assert "processing" in panel._activity_label.text().lower()

    panel._on_event({"type": "message_end"})
    assert not panel._activity_frame.isVisible()


def test_activity_strip_updates_on_stream(panel):
    """Activity strip shows thinking state on stream."""
    panel.show()
    panel._on_event({"type": "message_start", "message": {"role": "assistant"}})
    assert panel._activity_frame.isVisible()
    assert "thinking" in panel._activity_label.text().lower()


def test_activity_strip_returns_ready_on_end(panel):
    """Activity strip returns to ready when streaming ends."""
    panel._on_event({"type": "message_start", "message": {"role": "assistant"}})
    panel._on_event({"type": "message_end"})
    assert "ready" in panel._activity_label.text().lower()


def test_send_button_exists(panel):
    """Panel has a send button."""
    assert panel._send_btn is not None
    assert isinstance(panel._send_btn, QPushButton)


def test_send_button_initially_disabled_look(panel):
    """Send button starts with muted appearance when input is empty."""
    assert not panel._is_streaming
    assert panel._input.toPlainText() == ""


def test_send_message_clears_input(panel):
    """After send, the input field is cleared."""
    panel._input.setPlainText("test message")
    panel._on_send()
    assert panel._input.toPlainText() == ""


def test_send_message_toggles_streaming(panel):
    """After send, streaming state becomes active."""
    panel._input.setPlainText("test")
    panel._on_send()
    assert panel._is_streaming


def test_abort_ends_streaming(panel):
    """Abort resets streaming state and adds system message."""
    panel._input.setPlainText("test")
    panel._on_send()
    assert panel._is_streaming
    panel._abort()
    assert not panel._is_streaming


def test_send_button_click_aborts_when_running(panel):
    """Send button becomes stop while running and aborts on click."""
    panel._input.setPlainText("test")
    panel._on_send()
    assert panel._send_btn.text() == "■"
    panel.rpc.abort.reset_mock()
    panel._send_btn.click()
    panel.rpc.abort.assert_called_once()
    assert panel._send_btn.text() == "➤"


def test_send_while_running_queues_locally_and_shows_count(panel):
    """Submitting during streaming should queue locally, not call follow_up."""
    panel._input.setPlainText("first")
    panel._on_send()
    panel.rpc.prompt.reset_mock()
    panel.rpc.abort.reset_mock()
    if hasattr(panel.rpc, "follow_up"):
        panel.rpc.follow_up = MagicMock()

    before = panel._feed_layout.count()
    panel._input.setPlainText("second")
    panel._on_send()

    # Should NOT have called follow_up or abort
    if hasattr(panel.rpc, "follow_up"):
        panel.rpc.follow_up.assert_not_called()
    panel.rpc.abort.assert_not_called()
    # Should be in local queue
    assert panel._local_prompt_queue == ["second"]
    assert panel._feed_layout.count() > before
    assert panel._input.toPlainText() == ""
    # Queue count should show in activity
    assert "queued" in panel._activity_label.text().lower()


def test_abort_ignores_late_aborted_stream_and_next_prompt_starts_clean(panel):
    """Late events from an aborted turn must not leak into the next prompt."""
    panel._input.setPlainText("first")
    panel._on_send()
    panel._on_event({"type": "message_start", "message": {"role": "assistant"}})
    panel._on_event({
        "type": "message_update",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "stale aborted text"}]},
    })
    before_abort_count = panel._feed_layout.count()
    panel._abort()

    panel._on_event({
        "type": "message_end",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "I found stale QGIS API calls"}]},
    })
    assert panel._feed_layout.count() == before_abort_count + 1

    panel._input.setPlainText("second")
    panel._on_send()
    panel._on_event({"type": "message_start", "message": {"role": "assistant"}})
    panel._on_event({
        "type": "message_end",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "fresh reply"}]},
    })
    widgets = [panel._feed_layout.itemAt(i).widget() for i in range(panel._feed_layout.count() - 1)]
    texts = []
    for w in widgets:
        if not hasattr(w, "findChildren"):
            continue
        for lbl in w.findChildren(QLabel):
            texts.append(lbl.text())
    assert not any("I found stale QGIS API calls" in t for t in texts)
    assert any("fresh reply" in t for t in texts)


def test_append_bubble_user(panel):
    """Appending a user message adds to feed layout."""
    panel._add_bubble("YOU", "hello world", "user")
    assert panel._feed_layout.count() >= 2


def test_append_bubble_assistant(panel):
    """Appending an assistant message."""
    panel._add_bubble("AERY", "processed", "assistant")
    assert panel._feed_layout.count() >= 2


def test_append_bubble_error(panel):
    """Appending an error message."""
    panel._add_bubble("ERROR", "something broke", "error")
    assert panel._feed_layout.count() >= 2


def test_tool_block_running(panel):
    """Tool activity should stay out of transcript and only update status."""
    before = panel._feed_layout.count()
    panel._add_tool_block("buffer", "running")
    assert panel._feed_layout.count() == before
    assert "buffer" in panel._activity_label.text().lower() or "running" in panel._activity_label.text().lower()


def test_tool_block_done(panel):
    """Completed tool activity stays out of transcript."""
    before = panel._feed_layout.count()
    panel._add_tool_block("buffer", "done", "Buffer created successfully")
    assert panel._feed_layout.count() == before


def test_clear_feed(panel):
    """Clearing removes all messages but keeps stretch."""
    panel._add_bubble("YOU", "hello", "user")
    panel._add_bubble("AERY", "hi", "assistant")
    assert panel._feed_layout.count() >= 3
    panel._clear_feed()
    assert panel._feed_layout.count() == 1


def test_connect_rpc(panel):
    """connect_rpc wires up RPC signals."""
    panel.connect_rpc()
    assert panel.rpc.event_received.connect.called
    assert panel.rpc.response_received.connect.called
    assert panel.rpc.error_occurred.connect.called
    assert panel.rpc.process_exited.connect.called


def test_disconnect_rpc(panel):
    """disconnect_rpc tears down RPC signals without error."""
    panel.connect_rpc()
    panel.disconnect_rpc()


def test_set_rpc(panel):
    """set_rpc replaces the bridge and reconnects."""
    new_rpc = MagicMock()
    new_rpc.event_received = MagicMock()
    new_rpc.response_received = MagicMock()
    new_rpc.error_occurred = MagicMock()
    new_rpc.process_exited = MagicMock()
    new_rpc.event_received.connect = MagicMock()
    new_rpc.response_received.connect = MagicMock()
    new_rpc.error_occurred.connect = MagicMock()
    new_rpc.process_exited.connect = MagicMock()

    panel.set_rpc(new_rpc)
    assert panel.rpc is new_rpc


def test_set_ready(panel):
    """set_ready marks panel as ready and adds system message."""
    panel.set_ready()
    assert panel._ready is True


def test_on_event_message_start(panel):
    """message_start event sets streaming state."""
    panel._on_event({"type": "message_start", "message": {"role": "assistant"}})
    assert panel._is_streaming is True


def test_on_event_message_end(panel):
    """message_end event ends streaming."""
    panel._is_streaming = True
    panel._on_event({"type": "message_end"})
    assert panel._is_streaming is False


def test_on_event_message_end_with_direct_text_renders_bubble(panel):
    """Assistant text present only in message_end is still rendered."""
    before = panel._feed_layout.count()
    panel._on_event({
        "type": "message_start",
        "message": {"role": "assistant"},
    })
    panel._on_event({
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "final answer"}],
        },
    })
    assert panel._feed_layout.count() > before


def test_user_role_stream_events_do_not_echo_prompt(panel):
    """Non-assistant stream events should not show the user's own prompt as assistant output."""
    panel._input.setPlainText("hii")
    panel._on_send()
    before = panel._feed_layout.count()
    panel._on_event({"type": "message_start", "message": {"role": "user"}})
    panel._on_event({
        "type": "message_update",
        "message": {"role": "user", "content": [{"type": "text", "text": "hii"}]},
    })
    panel._on_event({"type": "message_end", "message": {"role": "user"}})
    assert panel._feed_layout.count() == before


def test_context_json_in_assistant_stream_stays_out_of_transcript(panel):
    """Raw context JSON must be suppressed from assistant transcript rendering."""
    panel._on_event({"type": "message_start", "message": {"role": "assistant"}})
    panel._on_event({
        "type": "message_update",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": '{"project_path":"/tmp/x.qgz","project_dir":"/tmp","layers":[],"crs":"EPSG:4326"}'}],
        },
    })
    panel._on_event({"type": "message_end", "message": {"role": "assistant"}})
    assert panel._feed_layout.count() == 1


def test_on_event_tool_start(panel):
    """tool_execution_start updates status only."""
    before = panel._feed_layout.count()
    panel._on_event({"type": "tool_execution_start", "tool": "buffer"})
    assert panel._feed_layout.count() == before
    assert "buffer" in panel._activity_label.text().lower() or "orchestrating" in panel._activity_label.text().lower()


def test_on_event_tool_end(panel):
    """tool_execution_end keeps transcript clean."""
    before = panel._feed_layout.count()
    panel._on_event({"type": "tool_execution_end", "tool": "buffer", "result": "ok"})
    assert panel._feed_layout.count() == before


def test_on_event_agent_end(panel):
    """agent_end ends streaming."""
    panel._is_streaming = True
    panel._on_event({"type": "agent_end"})
    assert panel._is_streaming is False


def test_on_response_error(panel):
    """Error response adds error message."""
    panel._on_response("prompt", {"success": False, "error": "API error"})
    assert panel._feed_layout.count() >= 2


def test_on_response_success(panel):
    """Successful response does nothing special."""
    panel._on_response("prompt", {"success": True})


def test_on_error(panel):
    """Error handler adds error message."""
    panel._on_error("connection failed")
    assert panel._feed_layout.count() >= 2


def test_startup_noise_errors_stay_out_of_transcript(panel):
    """Known startup/debug noise should not be shown in transcript."""
    before = panel._feed_layout.count()
    panel._on_error("'QgisInterface' object has no attribute 'project'")
    panel._on_error("name 'os' is not defined")
    assert panel._feed_layout.count() == before


def test_show_audit_window_shows_raw_cumulative_log(panel, tmp_path):
    """Audit window should show the raw cumulative JSONL tail without formatting or filtering."""
    panel._last_context = {"project_dir": str(tmp_path)}
    audit_dir = tmp_path / ".aery"
    audit_dir.mkdir()
    lines = [
        json.dumps({"timestamp":"2026-05-13T22:00:00Z","type":"run_start","run_id":"run-old","source":"plugin"}),
        json.dumps({"timestamp":"2026-05-13T22:00:01Z","tool_name":"run_code","run_id":"run-old","success":True,"result_summary":"old result","code":"result = 'old'","risks":[]}),
        json.dumps({"timestamp":"2026-05-13T22:10:00Z","type":"run_start","run_id":"run-new","source":"plugin"}),
        json.dumps({"timestamp":"2026-05-13T22:10:01Z","tool_name":"capture_canvas","run_id":"run-new","success":True,"result_summary":"iVBORw0KGgo" + ("A" * 5000),"code":"result = 'png'","risks":[]}),
    ]
    (audit_dir / "operations.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    panel._show_dialog = MagicMock()

    panel._show_audit_window()

    title, body = panel._show_dialog.call_args[0]
    assert title == "Audit Trail"
    assert body == "".join([line + "\n" for line in lines])


def test_on_exit(panel):
    """Engine exit adds disconnection message."""
    panel._on_exit(1)
    assert panel._feed_layout.count() >= 2


def test_history_navigation(panel):
    """Sending a message adds it to history."""
    panel._input.setPlainText("first message")
    panel._on_send()
    assert "first message" in panel._history
    assert len(panel._history) == 1


def test_multiple_messages(panel):
    """Multiple messages accumulate in history."""
    panel._input.setPlainText("msg 1")
    panel._on_send()
    panel._is_streaming = False
    panel._input.setPlainText("msg 2")
    panel._on_send()
    assert len(panel._history) == 2
    assert panel._history == ["msg 1", "msg 2"]


def test_input_height_stable_on_first_type(panel):
    """Input should not snap height on first typed character or focus/cursor changes."""
    initial_input_height = panel._input.height()
    initial_bar_height = panel._input_bar.height()
    panel._input.setFocus()
    panel._input.setPlainText("a")
    panel._autosize_input()
    assert panel._input.height() == initial_input_height
    assert panel._input_bar.height() == initial_bar_height


def test_keyboard_enter_submits_shift_enter_newline_and_ctrl_c_aborts(panel):
    """Enter submits, Shift+Enter inserts newline, Ctrl+C aborts running agent."""
    panel._input.setPlainText("hello")
    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    panel._input.keyPressEvent(event)
    assert panel._history[-1] == "hello"

    panel._is_streaming = False
    panel._input.setPlainText("")
    shift_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    panel._input.keyPressEvent(shift_event)
    assert "\n" in panel._input.toPlainText()

    panel._is_streaming = True
    panel.rpc.abort.reset_mock()
    ctrl_c = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    panel._input.keyPressEvent(ctrl_c)
    panel.rpc.abort.assert_called_once()


def test_empty_message_not_sent(panel):
    """Empty message does not get sent."""
    panel._input.setPlainText("")
    panel._on_send()
    assert len(panel._history) == 0


def test_last_context_updated_by_tool_end(panel):
    """Tool execution end for get_project_context updates _last_context."""
    context_data = {"layers": [], "project_crs": "EPSG:4326", "selection_count": 5}
    panel._on_event({
        "type": "tool_execution_end",
        "tool": "get_project_context",
        "result": context_data,
    })
    assert panel._last_context.get("project_crs") == "EPSG:4326"


def test_settings_menu_cfg_triggers_on_config(panel):
    """Clicking AERY CONFIGURATION in settings menu calls on_config."""
    handled = []
    panel.on_config = lambda: handled.append(True)
    panel._on_cfg_clicked()
    assert handled == [True]


def test_settings_menu_clear_chat(panel):
    """CLEAR CHAT action clears feed."""
    panel._add_bubble("YOU", "hello", "user")
    assert panel._feed_layout.count() >= 2
    panel._on_clear_clicked()
    assert panel._feed_layout.count() == 1


def test_toggle_floating(panel):
    """Dock button toggle handler runs without error and updates glyph."""
    before = panel._dock_btn.text()
    panel._toggle_floating()
    after = panel._dock_btn.text()
    assert after in {"⇱", "⇲"}
    assert before in {"⇱", "⇲"}


def test_show_history_window(panel):
    """Session History dialog can be opened without error."""
    panel._history.append("test prompt")
    panel._show_history_window()
    assert len(panel._dialogs) >= 1
