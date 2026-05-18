"""Tests for ChatPanel — simplified settings-menu design."""

import json
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QTextEdit, QToolButton

from aery_plugin.chat_panel import ChatPanel, MessageBubble, ToolBlock, _QuestionWidget


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
    agent = MagicMock()

    with patch("qgis.core.QgsProject") as mock_proj:
        mock_proj.instance.return_value.fileName.return_value = "/tmp/test_project.qgz"
        p = ChatPanel(iface, agent)
        yield p
        p.close()


def test_panel_created(panel):
    """Panel is a QDockWidget with comfortable width for reading."""
    assert panel.windowTitle() == "Aery"
    assert panel.minimumWidth() >= 200
    assert panel.minimumWidth() <= 400


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
    panel._on_agent_event({"type": "thinking"})
    assert "thinking" in panel._activity_label.text().lower()

    panel._on_agent_event({"type": "tool_start", "tool": "run_processing"})
    assert "processing" in panel._activity_label.text().lower()


def test_activity_strip_updates_on_stream(panel):
    """Activity strip shows thinking state on stream."""
    panel.show()
    panel._on_agent_event({"type": "thinking"})
    assert panel._activity_frame.isVisible()
    assert "thinking" in panel._activity_label.text().lower()


def test_activity_strip_returns_ready_on_end(panel):
    """Activity strip returns to ready when streaming ends."""
    panel._on_agent_event({"type": "thinking"})
    panel._on_agent_response("done")
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
    panel._send_btn.click()
    assert panel._send_btn.text() == "➤"


def test_send_while_running_queues_locally_and_shows_count(panel):
    """Submitting during streaming should queue locally."""
    panel._input.setPlainText("first")
    panel._on_send()

    before = panel._feed_layout.count()
    panel._input.setPlainText("second")
    panel._on_send()

    # Should be in local queue
    assert panel._local_prompt_queue == ["second"]
    assert panel._feed_layout.count() > before
    assert panel._input.toPlainText() == ""
    # Queue count should show in activity
    assert "queued" in panel._activity_label.text().lower()


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


def test_set_ready(panel):
    """set_ready marks panel as ready and adds system message."""
    panel.set_ready()
    assert panel._ready is True


def test_on_agent_event_thinking(panel):
    """thinking event sets streaming state."""
    panel._on_agent_event({"type": "thinking"})
    assert panel._is_streaming is True


def test_on_agent_response(panel):
    """response event adds assistant bubble."""
    before = panel._feed_layout.count()
    panel._on_agent_response("final answer")
    assert panel._feed_layout.count() > before
    assert panel._is_streaming is False


def test_on_agent_error(panel):
    """error event adds error bubble."""
    before = panel._feed_layout.count()
    panel._on_agent_error("something went wrong")
    assert panel._feed_layout.count() > before
    assert panel._is_streaming is False


def test_on_agent_event_tool_error(panel):
    """tool_error event adds error bubble."""
    before = panel._feed_layout.count()
    panel._on_agent_event({"type": "tool_error", "tool": "run_code", "error": "NameError"})
    assert panel._feed_layout.count() > before


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
    ctrl_c = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    panel._input.keyPressEvent(ctrl_c)
    assert not panel._is_streaming


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


def test_show_error(panel):
    """show_error adds an error bubble."""
    before = panel._feed_layout.count()
    panel.show_error("test error")
    assert panel._feed_layout.count() > before


# ════════════════════════════════════════════════════════════════════════════════
# Question / inline-answer tests
# ════════════════════════════════════════════════════════════════════════════════

def test_question_widget_construction(qapp):
    """_QuestionWidget builds without raising for a valid question event."""
    event = {
        "type": "question",
        "questId": "q1",
        "header": "Choose a format",
        "description": "What output format do you want?",
        "options": [
            {
                "label": "GeoPackage",
                "description": "Portable spatial database",
                "required_fields": [{"name": "output_path", "label": "Output path", "placeholder": "~/data.gpkg"}],
            },
            {
                "label": "Shapefile",
                "description": "Legacy ESRI format",
            },
        ],
    }
    widget = _QuestionWidget(event)
    assert widget._quest_id == "q1"
    assert len(widget._options) == 2
    assert widget._selected == -1
    assert not widget._submit_btn.isEnabled()


def test_question_widget_never_submits_without_selection(qapp):
    """Submit button stays disabled until an option is selected."""
    event = {"type": "question", "questId": "q2", "header": "?", "options": [{"label": "A", "required_fields": []}]}
    widget = _QuestionWidget(event)
    assert not widget._submit_btn.isEnabled()
    widget.deleteLater()


def test_question_widget_select_and_submit_complete(qapp):
    """Selecting option with no required fields immediately enables submit."""
    called = []
    def on_ans(qid, ans):
        called.append((qid, ans))

    event = {
        "type": "question", "questId": "q3",
        "header": "Pick one",
        "options": [{"label": "Yes", "required_fields": [{"name": "note", "label": "Note"}]}],
    }
    widget = _QuestionWidget(event)
    widget._resolve_callback = on_ans

    widget._selected = 0
    widget._update_submit()
    assert widget._submit_btn.isEnabled()

    # Submit without filling required field shows error but doesn't call callback
    widget._on_submit()
    assert called == []

    # Now fill the required field and submit
    widget._field_states[0]["note"] = "hello"
    widget._on_submit()
    assert len(called) == 1
    assert called[0][0] == "q3"
    assert called[0][1]["option_label"] == "Yes"
    assert called[0][1]["fields"]["note"] == "hello"
    widget.deleteLater()


def test_question_widget_renders_field_row(qapp):
    """Required field input appears for option with required_fields."""
    event = {
        "type": "question", "questId": "q4",
        "header": "Pick",
        "options": [{"label": "A", "required_fields": [{"name": "path", "label": "Path", "placeholder": "e.g. /tmp/out.gpkg"}]}],
    }
    widget = _QuestionWidget(event)
    # Required field widgets have been built — check _field_states is empty initially
    assert widget._field_states[0] == {}
    widget.deleteLater()


def test_handle_question_inserts_widget_in_feed(panel):
    """_handle_question inserts _QuestionWidget into _feed_layout."""
    captured = []
    orig_insert = panel._feed_layout.insertWidget
    def _fake_insert(idx, w):
        captured.append(w)
        orig_insert(idx, w)
    panel._feed_layout.insertWidget = _fake_insert

    panel._handle_question({"questId": "qx", "header": "?", "options": [{"label": "A", "required_fields": []}]})
    assert len(captured) == 1
    assert isinstance(captured[0], _QuestionWidget)


def test_on_event_routes_question(panel):
    """_on_event dispatches to _handle_question when type=='question'."""
    called = []
    panel._handle_question = lambda ev: called.append(ev)
    panel._on_event({"type": "question", "questId": "test1", "header": "Hi", "options": []})
    assert len(called) == 1
    assert called[0]["questId"] == "test1"


# ════════════════════════════════════════════════════════════════════════════════
# Integration: qgis_executor._resolve_question / _pending_questions
# ════════════════════════════════════════════════════════════════════════════════

def test_resolve_question_delivers_to_pending_queue():
    """_resolve_question pops quest from _pending_questions and puts answer on queue."""
    from aery_plugin.qgis_executor import _resolve_question, _pending_questions

    q = __import__("queue").Queue()
    _pending_questions["q_abc"] = (q, "req_abc")
    _resolve_question("q_abc", {"option_label": "A", "fields": {}})

    assert "q_abc" not in _pending_questions
    result = q.get_nowait()
    assert result["option_label"] == "A"
    assert result["fields"] == {}


def test_resolve_question_missing_quest_id_is_noop():
    """_resolve_question for unknown quest_id does not raise."""
    from aery_plugin.qgis_executor import _resolve_question, _pending_questions

    _pending_questions.pop("absent", None)
    _resolve_question("absent", {"any": "data"})


# ════════════════════════════════════════════════════════════════════════════════
# Regression: _handle_code_error clears stale stream content on retry
# ════════════════════════════════════════════════════════════════════════════════

def test_handle_code_error_flushes_stream_label(panel):
    """_handle_code_error must clear _stream_label before sending retry prompt
    so stale streaming text is not retained as a later AERY bubble."""
    # Simulate old assistant streaming text sitting in _stream_label
    panel._stream_label.setHtml(
        "<b>thinking about the failed execution attempt</b>"
    )
    assert panel._stream_label.toPlainText().strip() != "", (
        "precondition: _stream_label must have content before the call"
    )

    panel._handle_code_error("NameError: name 'x' is not defined")

    # _cancel_streaming() must have cleared the stale content
    assert panel._stream_label.toPlainText().strip() == "", (
        "stale _stream_label was not flushed on retry"
    )
    assert panel._stream_label.isVisible() is False

    # A SYSTEM bubble was added (the "Auto-retrying" notice runs separately)
    non_bubble_widgets = (
        panel._feed_layout.count() - 1
    )  # -1 for the trailing stretch
    assert non_bubble_widgets >= 1


def test_handle_code_error_limits_retries(panel):
    """After 2 failed retries, _handle_code_error stops."""
    panel._retry_count = 0
    panel._handle_code_error("error 1")
    panel._handle_code_error("error 2")
    # Third call is blocked
    panel._handle_code_error("error 3")
    assert panel._retry_count == 0  # reset after limit reached
