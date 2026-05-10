"""Chat panel UI for Aery QGIS plugin."""

import json
import re
import time
from datetime import datetime
from typing import Any, Optional

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# Color scheme
BG_DARK = "#1e1e24"
BG_USER = "#343541"
BG_TOOL = "#282832"
BG_HEADER = "#16161d"
TEXT_MAIN = "#e5e5e7"
TEXT_DIM = "#888888"
TEXT_ACCENT = "#8abeb7"
TEXT_ERROR = "#cc6666"
TEXT_SUCCESS = "#88c088"
TEXT_WORKING = "#e6c866"
BORDER_SUBTLE = "#3a3a4a"

# Activity states
STATE_IDLE = "idle"
STATE_WORKING = "working"
STATE_THINKING = "thinking"
STATE_TOOL = "tool"
STATE_DONE = "done"


class ChatPanel(QDockWidget):
    """Aery chat interface as a QGIS dockable panel."""

    def __init__(self, iface: Any, rpc_bridge, on_settings=None, parent: Optional[QWidget] = None):
        super().__init__("Aery", parent)
        self.iface = iface
        self.rpc = rpc_bridge
        self.on_settings = on_settings

        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setMinimumWidth(280)
        self.setMinimumHeight(300)

        # State
        self._is_streaming = False
        self._aborted = False
        self._activity_state = STATE_IDLE
        self._activity_start_time = 0
        self._current_tool_name = ""
        self._assistant_text = ""
        self._tool_output = ""
        self._queued_messages: list[str] = []  # Queue for messages sent while busy

        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        """Create the chat interface."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──
        header = QFrame()
        header.setStyleSheet(f"background-color: {BG_HEADER}; border-bottom: 1px solid {BORDER_SUBTLE};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 4)

        # Status indicator (star + text)
        self._activity_label = QLabel("●")
        self._activity_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        self._activity_label.setFixedWidth(60)

        self._status_text = QLabel("")
        self._status_text.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px;")

        header_layout.addWidget(self._activity_label)
        header_layout.addWidget(self._status_text)
        header_layout.addStretch()

        # Settings button
        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setFixedSize(QSize(24, 24))
        self._settings_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {TEXT_DIM};
                border: none;
                font-size: 14px;
            }}
            QPushButton:hover {{ color: {TEXT_MAIN}; }}
        """)
        self._settings_btn.setToolTip("Configure AI Provider")
        self._settings_btn.clicked.connect(self._on_settings_clicked)
        header_layout.addWidget(self._settings_btn)

        layout.addWidget(header)

        # ── Message log ──
        self.message_log = QTextEdit()
        self.message_log.setReadOnly(True)
        self.message_log.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        font = QFont("monospace")
        font.setPointSizeF(10.5)
        self.message_log.setFont(font)
        self.message_log.setStyleSheet(f"""
            QTextEdit {{
                background-color: {BG_DARK};
                color: {TEXT_MAIN};
                border: none;
                padding: 12px;
            }}
        """)

        layout.addWidget(self.message_log, 1)

        # ── Streaming area ──
        self._streaming_label = QLabel()
        self._streaming_label.setVisible(False)
        self._streaming_label.setWordWrap(True)
        self._streaming_label.setStyleSheet(f"""
            QLabel {{
                background-color: {BG_DARK};
                color: {TEXT_MAIN};
                padding: 8px 12px;
                font-family: monospace;
                font-size: 10px;
                border-top: 1px solid {TEXT_ACCENT};
            }}
        """)
        layout.addWidget(self._streaming_label)

        # ── Input area ──
        input_frame = QFrame()
        input_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_USER};
                border-top: 1px solid {BORDER_SUBTLE};
                padding: 8px;
            }}
        """)
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(4, 2, 4, 2)
        input_layout.setSpacing(2)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Describe a geospatial task")
        self.input_field.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG_DARK};
                color: {TEXT_MAIN};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 3px;
                padding: 2px 6px;
                font-family: monospace;
                font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {TEXT_ACCENT}; }}
            QLineEdit::placeholder {{ color: {TEXT_DIM}; font-size: 9px; }}
        """)
        font_input = QFont("monospace")
        font_input.setPointSize(12)
        self.input_field.setFont(font_input)
        self.input_field.setMaximumHeight(34)
        self.input_field.setMinimumHeight(34)

        self.send_button = QPushButton("▶")
        self.send_button.setFixedSize(QSize(34, 34))
        self.send_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {TEXT_ACCENT};
                color: {BG_DARK};
                border: none;
                border-radius: 4px;
                font-size: 11px;
            }}
            QPushButton:hover {{ background-color: #9cc8c2; }}
            QPushButton:disabled {{ background-color: {TEXT_DIM}; }}
        """)
        self.send_button.setEnabled(False)
        self.send_button.setToolTip("Send")

        input_layout.addWidget(self.input_field, 1)
        input_layout.addWidget(self.send_button)

        layout.addWidget(input_frame)

        # ── Status bar ──
        self.status_label = QLabel("Connecting...")
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {TEXT_DIM};
                font-size: 9px;
                padding: 2px 8px;
                background-color: {BG_HEADER};
                border-top: 1px solid {BORDER_SUBTLE};
            }}
        """)
        layout.addWidget(self.status_label)

        self.setWidget(container)

        # Activity animation timer
        self._blink_timer = QTimer()
        self._blink_timer.timeout.connect(self._blink_update)
        self._blink_on = False

        # Elapsed time timer
        self._elapsed_timer = QTimer()
        self._elapsed_timer.timeout.connect(self._update_elapsed)

    def _connect_signals(self):
        """Connect signals."""
        self.input_field.returnPressed.connect(self._send_message)
        self.send_button.clicked.connect(self._toggle_send_stop)

        self.rpc.event_received.connect(self._on_event)
        self.rpc.response_received.connect(self._on_response)
        self.rpc.error_occurred.connect(self._on_error)
        self.rpc.process_exited.connect(self._on_exit)

    def _set_activity(self, state: str, info: str = ""):
        """Update the activity indicator."""
        self._activity_state = state

        if state == STATE_IDLE:
            self._activity_label.setText("●")
            self._activity_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
            self._status_text.setText("")
            self._blink_timer.stop()
            self._elapsed_timer.stop()

        elif state == STATE_WORKING:
            self._blink_timer.start(500)  # Blink every 500ms
            self._activity_start_time = time.time()
            self._activity_label.setStyleSheet(f"color: {TEXT_ACCENT}; font-size: 12px;")
            self._status_text.setStyleSheet(f"color: {TEXT_ACCENT}; font-size: 9px;")
            self._elapsed_timer.start(1000)  # Update every second

        elif state == STATE_THINKING:
            self._blink_timer.start(300)
            self._activity_label.setStyleSheet(f"color: {TEXT_WORKING}; font-size: 12px;")
            self._status_text.setStyleSheet(f"color: {TEXT_WORKING}; font-size: 9px;")

        elif state == STATE_TOOL:
            self._blink_timer.start(400)
            tool_text = f" {info}" if info else ""
            self._status_text.setText(f"Running{tool_text}...")
            self._activity_label.setStyleSheet(f"color: {TEXT_SUCCESS}; font-size: 12px;")
            self._status_text.setStyleSheet(f"color: {TEXT_SUCCESS}; font-size: 9px;")

        elif state == STATE_DONE:
            elapsed = self._get_elapsed_str()
            self._activity_label.setText("✓")
            self._activity_label.setStyleSheet(f"color: {TEXT_SUCCESS}; font-size: 12px;")
            self._status_text.setText(f"Done in {elapsed}")
            self._status_text.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px;")
            self._blink_timer.stop()
            self._elapsed_timer.stop()

    def _blink_update(self):
        """Toggle the blink state for activity indicator."""
        self._blink_on = not self._blink_on

        if self._blink_on:
            self._activity_label.setText("✻")
        else:
            self._activity_label.setText("◌")

        # Update status text based on state
        elapsed = self._get_elapsed_str()

        if self._activity_state == STATE_WORKING:
            self._status_text.setText(f"Working{elapsed}")
        elif self._activity_state == STATE_THINKING:
            self._status_text.setText(f"Thinking{elapsed}")
        elif self._activity_state == STATE_TOOL:
            self._status_text.setText(f"Running {self._current_tool_name}{elapsed}")

    def _get_elapsed_str(self) -> str:
        """Get elapsed time string."""
        if self._activity_start_time:
            elapsed = int(time.time() - self._activity_start_time)
            if elapsed < 60:
                return f" {elapsed}s"
            else:
                mins = elapsed // 60
                secs = elapsed % 60
                return f" {mins}m {secs}s"
        return ""

    def _update_elapsed(self):
        """Update elapsed time display."""
        if self._activity_state in (STATE_WORKING, STATE_THINKING, STATE_TOOL):
            elapsed = self._get_elapsed_str()
            if self._activity_state == STATE_WORKING:
                self._status_text.setText(f"Working{elapsed}")
            elif self._activity_state == STATE_THINKING:
                self._status_text.setText(f"Thinking{elapsed}")
            elif self._activity_state == STATE_TOOL:
                self._status_text.setText(f"Running {self._current_tool_name}{elapsed}")

    def _toggle_send_stop(self):
        """Toggle between send and stop based on current state."""
        if self._is_streaming:
            self._abort()
        else:
            self._send_message()

    def _send_message(self):
        """Send user message to Aery."""
        text = self.input_field.text().strip()
        if not text:
            return

        if self._is_streaming or self._queued_messages:
            # Queue the message — agent is processing or messages ahead in queue
            self._queued_messages.append(text)
            self._append_message("system", f"Queued: \"{text[:50]}{'...' if len(text) > 50 else ''}\"")
            return

        self._append_message("user", text)
        self.input_field.clear()
        self._show_stop_button()
        self._set_activity(STATE_WORKING)
        self.rpc.prompt(text)

    def _abort(self):
        """Abort current operation."""
        self._aborted = True
        self.rpc.abort()
        self._show_send_button()
        self._set_activity(STATE_IDLE)
        self._append_message("system", "Operation aborted")

    def _show_send_button(self):
        """Switch button to send mode."""
        self._is_streaming = False
        self._aborted = False
        self.send_button.setText("▶")
        self.send_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {TEXT_ACCENT};
                color: {BG_DARK};
                border: none;
                border-radius: 4px;
                font-size: 11px;
            }}
            QPushButton:hover {{ background-color: #9cc8c2; }}
            QPushButton:disabled {{ background-color: {TEXT_DIM}; }}
        """)
        self.send_button.setToolTip("Send")
        self.send_button.setEnabled(True)

    def _show_stop_button(self):
        """Switch button to stop mode."""
        self._is_streaming = True
        self.send_button.setText("■")
        self.send_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {TEXT_ERROR};
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 11px;
            }}
            QPushButton:hover {{ background-color: #d97777; }}
        """)
        self.send_button.setToolTip("Stop")
        self.send_button.setEnabled(True)

    def _on_event(self, event: dict[str, Any]):
        """Handle streaming events from Aery."""
        event_type = event.get("type")

        if event_type == "message_update":
            if self._aborted:
                return
            msg = event.get("message", {})
            content = msg.get("content", [])
            full_text = ""
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    full_text += block.get("text", "")
            self._update_assistant_text(full_text)

        elif event_type == "message_end":
            self._finalize_assistant_message()

        elif event_type == "message_start":
            msg = event.get("message", {})
            if msg.get("role") == "assistant":
                self._start_assistant_message()
                self._set_activity(STATE_THINKING)

        elif event_type == "agent_end":
            self._set_activity(STATE_DONE)

            if self._queued_messages:
                # Don't revert to send — queued message is about to fire
                # Keeps ■ mode, prevents user typing in the gap
                QTimer.singleShot(100, self._drain_queue)
            else:
                self._show_send_button()

        elif event_type == "tool_execution_start":
            tool_name = event.get("toolName", "unknown")
            args = event.get("args", {})
            self._current_tool_name = tool_name
            self._set_activity(STATE_TOOL, tool_name)
            self._start_tool_call(tool_name, args)

        elif event_type == "tool_execution_update":
            partial = event.get("partialResult", {})
            content = partial.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    self._update_tool_output(block.get("text", ""))

        elif event_type == "tool_execution_end":
            self._finalize_tool_call(event.get("isError", False))

    def _drain_queue(self):
        """Send the next queued message, or drain the queue if multiple are pending."""
        if not self._queued_messages or not self.rpc:
            return
        text = self._queued_messages.pop(0)
        self._append_message("user", text)
        self._show_stop_button()
        self._set_activity(STATE_WORKING)
        self.rpc.prompt(text)

    def _on_response(self, command: str, data: dict[str, Any]):
        """Handle command responses."""
        if not data.get("success"):
            self._append_message("error", f"Command failed: {data.get('error', 'Unknown error')}")
            self._show_send_button()
            self._set_activity(STATE_IDLE)

    def _on_error(self, message: str):
        """Handle errors."""
        self._append_message("error", message)
        self._show_send_button()
        self._set_activity(STATE_IDLE)

    def _on_exit(self, exit_code: int):
        """Handle process exit."""
        self._append_message("error", f"Aery exited (code {exit_code})")
        self.send_button.setEnabled(False)
        self._set_activity(STATE_IDLE)
        self.status_label.setText("Disconnected")

    def _append_message(self, role: str, text: str):
        """Append a formatted message to the log."""
        timestamp = datetime.now().strftime("%H:%M")

        if role == "user":
            bg = BG_USER
            label = "You"
            color = TEXT_ACCENT
        elif role == "error":
            bg = "#2a1a1a"
            label = "Error"
            color = TEXT_ERROR
        elif role == "assistant":
            bg = BG_DARK
            label = "Aery"
            color = TEXT_ACCENT
        else:
            bg = BG_DARK
            label = "System"
            color = TEXT_DIM

        html = f"""
        <div style="margin: 8px 0;">
            <div style="background:{bg}; padding:12px; border-radius:8px; border-left: 3px solid {color};">
                <span style="color:{TEXT_DIM}; font-size:9px;">{timestamp}</span>
                <span style="color:{color}; font-weight:bold; font-size:10px; margin-left:8px;">{label}</span>
                <div style="color:{TEXT_MAIN}; margin-top:8px; line-height:1.5;">{self._render_text(text)}</div>
            </div>
        </div>
        """
        self.message_log.append(html)
        self._scroll_to_bottom()

    def _start_assistant_message(self):
        """Start a new assistant message block."""
        self._assistant_text = ""
        self._tool_output = ""
        self._current_tool_name = ""

        self._streaming_label.clear()
        self._streaming_label.setVisible(True)
        self._scroll_to_bottom()

    def _update_assistant_text(self, text: str):
        """Update streaming assistant text."""
        self._assistant_text = text
        self._render_assistant_stream()

    def _render_assistant_stream(self):
        """Render current assistant message with streaming updates."""
        text_html = self._render_text(self._assistant_text) if self._assistant_text else ""

        tool_html = ""
        if self._current_tool_name:
            output_html = self._render_text(self._tool_output) if self._tool_output else ""
            tool_html = f"\n\n⚡ {self._current_tool_name}\n{output_html}"

        display = (text_html + tool_html).strip()
        self._streaming_label.setText(display)
        self._scroll_to_bottom()

    def _start_tool_call(self, tool_name: str, args: dict[str, Any]):
        """Start a tool execution display."""
        self._current_tool_name = tool_name
        self._tool_output = ""
        self._render_assistant_stream()

    def _update_tool_output(self, text: str):
        """Update streaming tool output."""
        self._tool_output += text
        self._render_assistant_stream()

    def _finalize_tool_call(self, is_error: bool):
        """Finalize a tool execution display."""
        self._current_tool_name = ""
        self._tool_output = ""

    def _finalize_assistant_message(self):
        """Finalize the current assistant message."""
        if self._assistant_text.strip():
            self._append_message("assistant", self._assistant_text)
        self._assistant_text = ""
        self._streaming_label.clear()
        self._streaming_label.setVisible(False)

    @staticmethod
    def _render_text(text: str) -> str:
        """Convert plain text/markdown to safe HTML."""
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        text = re.sub(
            r"```(\w*)\n(.*?)```",
            lambda m: f'<pre style="background:#2a2a35; padding:10px; border-radius:6px; overflow-x:auto; font-size:10px; border:1px solid {BORDER_SUBTLE};"><code>{m.group(2).strip()}</code></pre>',
            text,
            flags=re.DOTALL,
        )

        text = re.sub(
            r"`([^`]+)`",
            f'<code style="background:#2a2a35; padding:2px 6px; border-radius:3px; font-size:10px;">\\1</code>',
            text,
        )

        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)

        text = re.sub(r"\n{2,}", "</p><p>", text)
        text = f"<p>{text}</p>"
        text = text.replace("\n", "<br>")

        return text

    def _scroll_to_bottom(self):
        """Scroll to bottom."""
        scrollbar = self.message_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_ready(self):
        """Called when Aery is ready."""
        self.send_button.setEnabled(True)
        self._set_activity(STATE_IDLE)
        self.status_label.setText("Ready")
        self._append_message("system", "Aery agent connected.")

    def set_rpc(self, rpc):
        """Replace the RPC bridge (after provider config change)."""
        self.rpc = rpc
        self._connect_signals()
        self.send_button.setEnabled(True)
        self.status_label.setText("Ready")
        self._set_activity(STATE_IDLE)

    def _on_settings_clicked(self):
        """Open the provider settings dialog."""
        if self.on_settings:
            self.on_settings()
