"""Tests for ChatPanel."""

from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from aery_plugin.chat_panel import ChatPanel


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

    # Connect signals to avoid crashes
    rpc_bridge.event_received.connect = MagicMock()
    rpc_bridge.response_received.connect = MagicMock()
    rpc_bridge.error_occurred.connect = MagicMock()
    rpc_bridge.process_exited.connect = MagicMock()

    p = ChatPanel(iface, rpc_bridge)
    yield p
    p.close()


def test_panel_created(panel):
    """Panel is a QDockWidget with correct title."""
    assert panel.windowTitle() == "Aery"
    assert panel.minimumWidth() <= 400


def test_input_field_exists(panel):
    """Panel has an input field."""
    assert panel.input_field is not None
    assert panel.input_field.placeholderText() == "Describe a geospatial task"


def test_send_button_disabled_initially(panel):
    """Send button is disabled until Aery is ready."""
    assert not panel.send_button.isEnabled()


def test_set_ready_enables_send(panel):
    """After set_ready(), send button is enabled."""
    panel.set_ready()
    assert panel.send_button.isEnabled()


def test_append_user_message(panel):
    """Appending a user message adds to the log."""
    panel._append_message("user", "hello")
    html = panel.message_log.toHtml()
    assert "hello" in html
    assert "You" in html


def test_append_error_message(panel):
    """Appending an error message shows error styling."""
    panel._append_message("error", "something broke")
    html = panel.message_log.toHtml()
    assert "something broke" in html
    assert "Error" in html


def test_send_message_clears_input(panel):
    """After send, the input field is cleared."""
    panel.set_ready()
    panel.input_field.setText("test message")
    panel._send_message()
    assert panel.input_field.text() == ""


def test_send_message_disables_send(panel):
    """During processing, send button toggles to stop mode."""
    panel.set_ready()
    panel.input_field.setText("test")
    panel._send_message()
    # Button changes to stop mode (■) and remains enabled
    assert panel.send_button.isEnabled()
    assert panel.send_button.text() == "■"
    assert panel._is_streaming


def test_send_button_toggles_to_stop_during_send(panel):
    """Send button changes to stop (■) during message processing."""
    panel.show()
    panel.set_ready()
    panel.input_field.setText("test")
    assert not panel._is_streaming
    panel._send_message()
    assert panel.send_button.text() == "■"
    assert panel._is_streaming


def test_stop_toggles_back_to_send(panel):
    """After abort, button reverts to send (▶) after a short debounce."""
    panel.set_ready()
    panel.input_field.setText("test")
    panel._send_message()
    assert panel._is_streaming
    panel._abort()
    # Button stays ■ while abort propagates (500ms timer)
    assert panel.send_button.text() == "■"
    assert panel._is_streaming  # still streaming until timer fires
    # Advance the abort debounce timer
    panel._abort_debounce.timeout.emit()
    assert panel.send_button.text() == "▶"
    assert not panel._is_streaming
    assert panel.send_button.isEnabled()


def test_on_error_shows_send_button(panel):
    """After an error, button reverts to send mode."""
    panel._is_streaming = True
    panel.send_button.setText("■")
    panel._on_error("test error")
    assert panel.send_button.text() == "▶"
    assert not panel._is_streaming


def test_on_exit_disables_send(panel):
    """After Aery exits, send button is disabled."""
    panel.set_ready()
    panel._on_exit(1)
    assert not panel.send_button.isEnabled()
    assert "Disconnected" in panel.status_label.text()


def test_render_text_escapes_html(panel):
    """_render_text escapes HTML tags."""
    result = panel._render_text("<script>alert('xss')</script>")
    assert "&lt;script&gt;" in result
    assert "<script>" not in result


def test_render_text_code_blocks(panel):
    """_render_text converts code blocks to pre tags."""
    result = panel._render_text("```\nprint('hello')\n```")
    assert "<pre" in result
    assert "print" in result


def test_render_text_inline_code(panel):
    """_render_text converts inline code to code tags."""
    result = panel._render_text("Use the `buffer` function")
    assert "<code" in result
    assert "buffer" in result


def test_render_text_newlines(panel):
    """_render_text converts newlines to <br>."""
    result = panel._render_text("line1\nline2")
    assert "<br>" in result


def test_status_label_updates(panel):
    """Status label can be updated."""
    assert "Connecting" in panel.status_label.text()
    panel.set_ready()
    assert "Ready" in panel.status_label.text()
