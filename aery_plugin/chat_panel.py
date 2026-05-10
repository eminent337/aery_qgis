"""Chat panel UI for Aery QGIS plugin."""

import json
import re
from datetime import datetime
from typing import Any, Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QTextCursor, QColor, QTextCharFormat, QTextBlockFormat
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
    QApplication,
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
BORDER_SUBTLE = "#3a3a4a"


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

        # Streaming state
        self._is_streaming = False
        self._aborted = False  # blocks stale stream events after abort
        self._assistant_text = ""
        self._thinking_text = ""
        self._tool_args = ""
        self._tool_output = ""
        self._current_tool_name = ""

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

        # Status indicator dot
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {TEXT_DIM}; font-size: 14px;")
        self.status_dot.setFixedWidth(20)

        title = QLabel("Aery")
        title.setStyleSheet(f"color: {TEXT_MAIN}; font-weight: bold; font-size: 11px;")

        header_layout.addWidget(self.status_dot)
        header_layout.addWidget(title)
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

        # ── Streaming label (replaces inline cursor manipulation) ──
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

    def _connect_signals(self):
        """Connect signals."""
        self.input_field.returnPressed.connect(self._send_message)
        self.send_button.clicked.connect(self._toggle_send_stop)

        self.rpc.event_received.connect(self._on_event)
        self.rpc.response_received.connect(self._on_response)
        self.rpc.error_occurred.connect(self._on_error)
        self.rpc.process_exited.connect(self._on_exit)

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

        self._append_message("user", text)
        self.input_field.clear()
        self._show_stop_button()
        self.rpc.prompt(text)

    def _abort(self):
        """Abort current operation."""
        self._aborted = True  # block stale stream events
        self.rpc.abort()
        self._show_send_button()
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
                return  # ignore stale events after abort
            msg = event.get("message", {})
            content = msg.get("content", [])
            # Each message_update carries the FULL content array,
            # so rebuild the accumulated text from all text blocks
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

        elif event_type == "agent_end":
            # Agent has fully finished the entire processing cycle
            # (assistant → tool → continuation → ... → done).
            # Revert the button now.
            self._show_send_button()

        elif event_type == "tool_execution_start":
            tool_name = event.get("toolName", "unknown")
            args = event.get("args", {})
            self._start_tool_call(tool_name, args)

        elif event_type == "tool_execution_update":
            partial = event.get("partialResult", {})
            content = partial.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    self._update_tool_output(block.get("text", ""))

        elif event_type == "tool_execution_end":
            self._finalize_tool_call(event.get("isError", False))

    def _on_response(self, command: str, data: dict[str, Any]):
        """Handle command responses."""
        if not data.get("success"):
            self._append_message("error", f"Command failed: {data.get('error', 'Unknown error')}")
            self._show_send_button()

    def _on_error(self, message: str):
        """Handle errors."""
        self._append_message("error", message)
        self._show_send_button()
        self.status_dot.setStyleSheet(f"color: {TEXT_ERROR}; font-size: 14px;")
        self.status_label.setText(message)

    def _on_exit(self, exit_code: int):
        """Handle process exit."""
        self._append_message("error", f"Aery exited (code {exit_code})")
        self.send_button.setEnabled(False)
        self.status_dot.setStyleSheet(f"color: {TEXT_ERROR}; font-size: 14px;")
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
        self._thinking_text = ""
        self._tool_args = ""
        self._tool_output = ""
        self._current_tool_name = ""
        self._message_has_tool = False

        # Show the streaming label
        self._streaming_label.clear()
        self._streaming_label.setVisible(True)
        self._scroll_to_bottom()

    def _update_assistant_text(self, text: str):
        """Update streaming assistant text (replaces, not appends - events carry full content)."""
        self._assistant_text = text
        self._render_assistant_stream()

    def _update_thinking(self, text: str):
        """Accumulate thinking text silently (not displayed to user)."""
        self._thinking_text += text

    def _render_assistant_stream(self):
        """Render current assistant message with streaming updates."""
        text_html = self._render_text(self._assistant_text) if self._assistant_text else ""

        # Build tool HTML if present
        tool_html = ""
        if self._current_tool_name:
            args_html = self._render_text(json.dumps(self._tool_args, indent=2)) if self._tool_args else ""
            output_html = self._render_text(self._tool_output) if self._tool_output else ""

            tool_html = f"""
            ⚡ {self._current_tool_name}
            Args:
            {args_html}
            Output:
            {output_html}
            """

        display = (text_html + "\n" + tool_html).strip()
        self._streaming_label.setText(display)
        self._scroll_to_bottom()

    def _start_tool_call(self, tool_name: str, args: dict[str, Any]):
        """Start a tool execution display."""
        self._current_tool_name = tool_name
        self._tool_args = args
        self._tool_output = ""
        self._message_has_tool = True
        self._render_assistant_stream()

    def _update_tool_output(self, text: str):
        """Update streaming tool output."""
        self._tool_output += text
        self._render_assistant_stream()

    def _finalize_tool_call(self, is_error: bool):
        """Finalize a tool execution display."""
        # Update border color based on success/error
        if is_error:
            self._tool_border_color = TEXT_ERROR
        # Already rendered via _render_assistant_stream
        self._current_tool_name = ""
        self._tool_args = ""
        self._tool_output = ""

    def _finalize_assistant_message(self):
        """Finalize the current assistant message."""
        if self._assistant_text.strip():
            self._append_message("assistant", self._assistant_text)
        self._assistant_text = ""
        self._thinking_text = ""
        self._streaming_label.clear()
        self._streaming_label.setVisible(False)

    @staticmethod
    def _render_text(text: str) -> str:
        """Convert plain text/markdown to safe HTML."""
        # Escape HTML
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # Code blocks with syntax highlighting hints
        text = re.sub(
            r"```(\w*)\n(.*?)```",
            lambda m: f'<pre style="background:#2a2a35; padding:10px; border-radius:6px; overflow-x:auto; font-size:10px; border:1px solid {BORDER_SUBTLE};"><code>{m.group(2).strip()}</code></pre>',
            text,
            flags=re.DOTALL,
        )

        # Inline code
        text = re.sub(
            r"`([^`]+)`",
            f'<code style="background:#2a2a35; padding:2px 6px; border-radius:3px; font-size:10px;">\\1</code>',
            text,
        )

        # Bold
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)

        # Paragraphs
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
        self.status_dot.setStyleSheet(f"color: {TEXT_SUCCESS}; font-size: 14px;")
        self.status_label.setText("Ready")
        self._append_message(
            "system",
            "Aery agent connected."
        )

    def set_rpc(self, rpc):
        """Replace the RPC bridge (after provider config change)."""
        self.rpc = rpc
        self._connect_signals()
        self.send_button.setEnabled(True)
        self.status_label.setText("Ready")
        self.status_dot.setStyleSheet(f"color: {TEXT_SUCCESS}; font-size: 14px;")

    def _on_settings_clicked(self):
        """Open the provider settings dialog."""
        if self.on_settings:
            self.on_settings()
