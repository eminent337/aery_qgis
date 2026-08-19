"""Transcript view widget for Aery chat panel."""

import base64
import os
import re
from datetime import datetime
from typing import Any, Callable, Optional, Union

from PyQt6.QtCore import Qt, QTimer, QSize, Q_ARG, QMetaObject
from PyQt6.QtGui import QPixmap, QImage, QTextOption, QIcon, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from aery_plugin.ui_constants import (
    ACCENT, ACCENT_DIM, BG_BASE, BG_CARD, BG_HIGH, BG_PANEL, BG_SURFACE,
    BORDER, ERROR_COLOR, FONT_MONO, FONT_SANS, SUCCESS_COLOR, TEXT_DIM,
    TEXT_MAIN, TEXT_MUTED, WARNING_COLOR,
)
from aery_plugin.ui_utils import escape_html, format_text_html, format_thinking_html, now_stamp


def _format_thinking_html(text: str) -> str:
    if not text:
        return ""
    html = escape_html(text)
    html = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", html)
    return html.replace("\n", "<br>")


class MessageBubble(QFrame):
    """Structured transcript card for one message."""

    def __init__(
        self,
        sender: str,
        text: str,
        msg_type: str = "assistant",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName(f"msg_{msg_type}")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        # Exact structured styling from Aerynel desktop assistant:
        # YOU (User message)      -> #1E1F26 card with #3C4A46 border
        # AERY (Assistant reply)  -> #1A1B22 card with #3C4A46 border
        # SYSTEM (System notices) -> #1E1F26 card with #3C4A46 border
        # ERROR                   -> border-rose-500 with rose tint
        card_bg = BG_PANEL if msg_type == "assistant" else BG_CARD if msg_type in ("user", "system") else BG_SURFACE
        border_color = ERROR_COLOR if msg_type == "error" else BORDER
        self.setStyleSheet(f"""
            QFrame#msg_{msg_type} {{
                background: {card_bg};
                border: 1px solid {border_color};
                border-radius: 6px;
            }}
        """)
        colors = {
            "assistant": ACCENT,
            "user": TEXT_DIM,
            "error": ERROR_COLOR,
            "system": "#7DD3FC",
            "tool": WARNING_COLOR,
        }
        border = colors.get(msg_type, TEXT_DIM)
        title = {
            "assistant": "AERY",
            "user": "YOU",
            "error": "ERROR",
            "system": "SYSTEM",
            "tool": "TOOL",
        }.get(msg_type, sender.upper())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setStyleSheet(
            f"color:{border};font-family:{FONT_MONO};font-size:10px;"
            "font-weight:800;letter-spacing:0.08em;background:transparent;"
        )
        header.addWidget(label)
        header.addStretch()
        time_lbl = QLabel(now_stamp())
        time_lbl.setStyleSheet(
            f"color:{TEXT_MUTED};font-family:{FONT_MONO};font-size:9px;background:transparent;"
        )
        header.addWidget(time_lbl)
        layout.addLayout(header)

        self._thinking_content = ""
        self._thinking_expanded = False
        self._thinking_toggle = None
        self._thinking_widget = None

        text, thinking = self._split_thinking(text)
        if thinking:
            self._thinking_content = thinking
            self._thinking_toggle = QPushButton("Reasoning \u25b8")
            self._thinking_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            self._thinking_toggle.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{TEXT_MUTED}; border:none;"
                f" font-family:{FONT_MONO}; font-size:10px; padding:4px 0; text-align:left; }}"
                f"QPushButton:hover {{ color:{TEXT_DIM}; }}"
            )
            self._thinking_toggle.clicked.connect(self._toggle_thinking)
            layout.addWidget(self._thinking_toggle)

            self._thinking_widget = QLabel(_format_thinking_html(thinking))
            self._thinking_widget.setWordWrap(True)
            self._thinking_widget.setTextFormat(Qt.TextFormat.RichText)
            self._thinking_widget.setStyleSheet(
                f"color:{TEXT_MUTED};font-family:{FONT_SANS};font-size:13px;"
                f"font-style:italic;line-height:1.5;background:transparent;"
                f"border-left:2px solid {BORDER};padding-left:8px;margin:4px 0;"
            )
            self._thinking_widget.setVisible(False)
            layout.addWidget(self._thinking_widget)

        self._body = QLabel(format_text_html(text))
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.TextFormat.RichText)
        self._body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._body.setOpenExternalLinks(False)
        self._body.linkActivated.connect(self.on_link)
        self._body.setStyleSheet(
            f"color:{TEXT_MAIN};font-family:{FONT_SANS};font-size:14px;"
            "line-height:1.6;background:transparent;"
        )
        self._body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        layout.addWidget(self._body)

    def _split_thinking(self, text: str) -> tuple[str, str]:
        thinking = ""
        for pattern in [r"<thinking>(.*?)</thinking>", r"\[reasoning\](.*?)\[/reasoning\]"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                thinking = match.group(1).strip()
                text = re.sub(pattern, "", text, flags=re.DOTALL).strip()
                break
        return text, thinking

    def _toggle_thinking(self):
        self._thinking_expanded = not self._thinking_expanded
        self._thinking_widget.setVisible(self._thinking_expanded)
        arrow = "\u25be" if self._thinking_expanded else "\u25b8"
        self._thinking_toggle.setText(f"Reasoning {arrow}")

    def on_link(self, url: str) -> None:
        if url.startswith("layer://"):
            layer_name = url[8:]
            try:
                from qgis.core import QgsProject
                from qgis.utils import iface as _iface
                for lyr in QgsProject.instance().mapLayers().values():
                    if lyr.name() == layer_name:
                        _iface.setActiveLayer(lyr)
                        _iface.layerTreeView().setCurrentLayer(lyr)
                        break
            except Exception:
                pass

    def update_text(self, text: str) -> None:
        cleaned = re.sub(r"```[\w]*\n.*?```", r"[code executed in tool]", text, flags=re.DOTALL)
        self._body.setText(format_text_html(cleaned))
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

class ToolBlock(QFrame):
    """Collapsible tool execution trace card."""

    def __init__(
        self,
        name: str,
        status: str = "running",
        details: str = "",
        code: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("toolBlock")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self._expanded = False
        self._details = details
        self._code = code
        self._name = name
        # Structured TOOL card matching Aerynel desktop assistant
        self.setStyleSheet(f"""
            QFrame#toolBlock {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 6px;
            }}
        """)
        status_color = {
            "running": ACCENT,
            "done": SUCCESS_COLOR,
            "error": ERROR_COLOR,
        }.get(status, TEXT_MUTED)
        status_icon = {
            "running": "\u25cc",
            "done": "\u2713",
            "error": "\u2717",
        }.get(status, "\u00b7")
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(10, 8, 10, 8)
        self._root_layout.setSpacing(4)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        tag = QLabel("TOOL")
        tag.setStyleSheet(
            f"color:{WARNING_COLOR};font-family:{FONT_MONO};font-size:10px;"
            "font-weight:900;letter-spacing:0.08em;background:transparent;"
        )
        header.addWidget(tag)
        nm = QLabel(name)
        nm.setStyleSheet(
            f"color:{TEXT_MAIN};font-family:{FONT_MONO};font-size:11px;"
            "font-weight:700;background:transparent;"
        )
        nm.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        nm.setWordWrap(True)
        header.addWidget(nm, stretch=1)
        header.addStretch()

        self._status_lbl = QLabel(f"{status_icon} {status.upper()}")
        self._status_lbl.setStyleSheet(
            f"color:{status_color};font-family:{FONT_MONO};font-size:9px;"
            "font-weight:800;background:transparent;"
        )
        header.addWidget(self._status_lbl)

        self._arrow = QLabel("\u25b6")
        self._arrow.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:9px;background:transparent;"
        )
        header.addWidget(self._arrow)

        self._root_layout.addLayout(header)

        self._detail_widget = QWidget()
        self._detail_layout = QVBoxLayout(self._detail_widget)
        self._detail_layout.setContentsMargins(0, 4, 0, 0)
        self._detail_layout.setSpacing(4)
        self._detail_widget.setVisible(False)

        if details:
            result_lbl = QLabel(escape_html(details[:500]))
            result_lbl.setWordWrap(True)
            result_lbl.setStyleSheet(
                f"color:{TEXT_DIM};font-family:{FONT_MONO};font-size:10px;background:transparent;"
            )
            self._detail_layout.addWidget(result_lbl)

        if code:
            code_box = QFrame()
            code_box.setStyleSheet(
                f"background:{BG_BASE}; border:1px solid {BORDER}; border-radius:4px;"
            )
            code_lay = QVBoxLayout(code_box)
            code_lay.setContentsMargins(8, 6, 8, 6)
            code_lay.setSpacing(4)

            code_lbl = QLabel(escape_html(code))
            code_lbl.setWordWrap(True)
            code_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            code_lbl.setStyleSheet(
                f"color:{ACCENT};font-family:{FONT_MONO};font-size:10px;background:transparent;"
            )
            code_lay.addWidget(code_lbl)

            copy_btn = QPushButton("COPY")
            copy_btn.setFixedHeight(20)
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{TEXT_MUTED}; border:1px solid {BORDER};"
                f" border-radius:2px; font-size:7px; font-weight:700; padding:0 6px; }}"
                f" QPushButton:hover {{ color:{ACCENT}; border-color:{ACCENT}; }}"
            )
            copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(code))
            code_lay.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignRight)
            self._detail_layout.addWidget(code_box)

        self._root_layout.addWidget(self._detail_widget)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_border(status)

        if status == "error":
            self.toggle()

    def _update_border(self, status: str) -> None:
        left_color = {
            "running": ACCENT,
            "done": SUCCESS_COLOR,
            "error": ERROR_COLOR,
        }.get(status, WARNING_COLOR)
        self.setStyleSheet(f"""
            #toolBlock {{
                background: {BG_PANEL};
                border: 1px solid {BORDER};
                border-left: 3px solid {left_color};
                border-radius: 6px;
            }}
        """)

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._detail_widget.setVisible(self._expanded)
        self._arrow.setText("\u25bc" if self._expanded else "\u25b6")

    def mousePressEvent(self, event) -> None:
        self.toggle()
        super().mousePressEvent(event)

    def update_status(self, status: str, details: str = "") -> None:
        self._details = details
        status_color = {
            "running": ACCENT,
            "done": SUCCESS_COLOR,
            "error": ERROR_COLOR,
        }.get(status, TEXT_MUTED)
        status_icon = {
            "running": "\u25cc",
            "done": "\u2713",
            "error": "\u2717",
        }.get(status, "\u00b7")
        self._status_lbl.setText(f"{status_icon} {status.upper()}")
        self._status_lbl.setStyleSheet(
            f"color:{status_color};font-family:{FONT_MONO};font-size:9px;"
            "font-weight:800;background:transparent;"
        )
        self._update_border(status)

        while self._detail_layout.count():
            item = self._detail_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if details:
            result_lbl = QLabel(escape_html(details[:500]))
            result_lbl.setWordWrap(True)
            result_lbl.setStyleSheet(
                f"color:{TEXT_DIM};font-family:{FONT_MONO};font-size:10px;background:transparent;"
            )
            self._detail_layout.addWidget(result_lbl)

        if self._code:
            code_box = QFrame()
            code_box.setStyleSheet(
                f"background:{BG_BASE}; border:1px solid {BORDER}; border-radius:4px;"
            )
            code_lay = QVBoxLayout(code_box)
            code_lay.setContentsMargins(8, 6, 8, 6)
            code_lay.setSpacing(4)

            code_lbl = QLabel(escape_html(self._code))
            code_lbl.setWordWrap(True)
            code_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            code_lbl.setStyleSheet(
                f"color:{ACCENT};font-family:{FONT_MONO};font-size:10px;background:transparent;"
            )
            code_lay.addWidget(code_lbl)

            copy_btn = QPushButton("COPY")
            copy_btn.setFixedHeight(20)
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{TEXT_MUTED}; border:1px solid {BORDER};"
                f" border-radius:2px; font-size:7px; font-weight:700; padding:0 6px; }}"
                f" QPushButton:hover {{ color:{ACCENT}; border-color:{ACCENT}; }}"
            )
            copy_btn.clicked.connect(lambda c=self._code: QApplication.clipboard().setText(c))
            code_lay.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignRight)
            self._detail_layout.addWidget(code_box)

        if status == "error" and not self._expanded:
            self.toggle()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)
class ProjectGuardWidget(QFrame):
    """Inline card shown when no QGIS project is saved."""

    def __init__(self, queued_prompt: str, on_ready, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._queued_prompt = queued_prompt
        self._on_ready = on_ready

        self.setStyleSheet(
            f"QFrame {{ background:{BG_PANEL}; border:1px solid {ACCENT};"
            f" border-radius:6px; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        hdr = QLabel("\u26a0  NO PROJECT OPEN")
        hdr.setStyleSheet(
            f"color:{ACCENT};font-family:{FONT_MONO};font-size:10px;"
            "font-weight:900;letter-spacing:0.1em;background:transparent;"
        )
        root.addWidget(hdr)

        sub = QLabel("Save your work to a project before running the agent.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;background:transparent;")
        root.addWidget(sub)

        name_row = QHBoxLayout()
        name_lbl = QLabel("Name")
        name_lbl.setFixedWidth(40)
        name_lbl.setStyleSheet(f"color:{TEXT_MUTED};font-size:9px;font-weight:700;background:transparent;")
        name_row.addWidget(name_lbl)
        self._name_input = QLineEdit("my_project")
        self._name_input.setStyleSheet(
            f"QLineEdit {{ background:{BG_BASE}; color:{TEXT_MAIN}; border:1px solid {BORDER};"
            f" border-radius:3px; padding:4px 8px; font-size:11px; }}"
            f" QLineEdit:focus {{ border-color:{ACCENT}; }}"
        )
        name_row.addWidget(self._name_input)
        root.addLayout(name_row)

        dir_row = QHBoxLayout()
        dir_lbl = QLabel("Dir")
        dir_lbl.setFixedWidth(40)
        dir_lbl.setStyleSheet(f"color:{TEXT_MUTED};font-size:9px;font-weight:700;background:transparent;")
        dir_row.addWidget(dir_lbl)
        self._dir_input = QLineEdit(os.path.expanduser("~/Documents"))
        self._dir_input.setStyleSheet(
            f"QLineEdit {{ background:{BG_BASE}; color:{TEXT_MAIN}; border:1px solid {BORDER};"
            f" border-radius:3px; padding:4px 8px; font-size:11px; }}"
            f" QLineEdit:focus {{ border-color:{ACCENT}; }}"
        )
        dir_row.addWidget(self._dir_input)
        browse_btn = QPushButton("\u2026")
        browse_btn.setFixedSize(28, 28)
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.setStyleSheet(
            f"QPushButton {{ background:{BG_HIGH}; color:{TEXT_DIM}; border:1px solid {BORDER};"
            f" border-radius:3px; font-size:12px; }}"
            f" QPushButton:hover {{ color:{ACCENT}; border-color:{ACCENT}; }}"
        )
        browse_btn.clicked.connect(self._browse)
        dir_row.addWidget(browse_btn)
        root.addLayout(dir_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        create_btn = QPushButton("CREATE PROJECT")
        create_btn.setFixedHeight(30)
        create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_btn.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:{BG_BASE}; border:none;"
            f" border-radius:3px; font-size:9px; font-weight:900; padding:0 14px; }}"
            f" QPushButton:hover {{ background:#9ecec7; }}"
        )
        create_btn.clicked.connect(self._create_project)
        btn_row.addWidget(create_btn)

        load_btn = QPushButton("LOAD EXISTING")
        load_btn.setFixedHeight(30)
        load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        load_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{ACCENT}; border:1px solid {ACCENT};"
            f" border-radius:3px; font-size:9px; font-weight:700; padding:0 14px; }}"
            f" QPushButton:hover {{ background:{ACCENT}; color:{BG_BASE}; }}"
        )
        load_btn.clicked.connect(self._load_project)
        btn_row.addWidget(load_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Project Directory", self._dir_input.text())
        if path:
            self._dir_input.setText(path)

    def _create_project(self):
        from qgis.core import QgsProject
        name = self._name_input.text().strip() or "my_project"
        base_dir = self._dir_input.text().strip() or os.path.expanduser("~/Documents")
        project_dir = os.path.join(base_dir, name)
        os.makedirs(project_dir, exist_ok=True)
        project_path = os.path.join(project_dir, f"{name}.qgz")
        proj = QgsProject.instance()
        proj.setFileName(project_path)
        proj.write()
        self._on_ready(project_path)
        self.setVisible(False)
        self.deleteLater()

    def _load_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open QGIS Project", os.path.expanduser("~"),
            "QGIS Projects (*.qgz *.qgs)"
        )
        if path:
            from qgis.core import QgsProject
            QgsProject.instance().read(path)
            self._on_ready(path)
            self.setVisible(False)
            self.deleteLater()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)


class TranscriptView(QScrollArea):
    """Scrollable transcript feed for messages, tool blocks, and interactive widgets."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        class FeedContainer(QWidget):
            def minimumSizeHint(self) -> QSize:
                return QSize(0, 0)
        self._feed_container = FeedContainer()
        self._feed_container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.MinimumExpanding)
        self._feed_layout = QVBoxLayout(self._feed_container)
        self._feed_layout.setContentsMargins(12, 12, 12, 12)
        self._feed_layout.setSpacing(10)
        self._feed_layout.addStretch()
        # Constrain content width so the dock never auto-expands
        # ChatPanel default width ~340; allow room up to 500 for comfortable viewing
        self._feed_container.setMaximumWidth(500)
        self._feed_container.setMinimumWidth(280)
        self.setWidget(self._feed_container)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(f"""
            QScrollArea {{ background:{BG_BASE}; border:none; }}
            QScrollBar:vertical {{ width:5px; background:{BG_BASE}; }}
            QScrollBar::handle:vertical {{ background:{BORDER}; border-radius:2px; }}
        """)

        self._streaming_bubble: Optional[MessageBubble] = None
        self._streaming_text: str = ""
        self._active_tool_block: Optional[ToolBlock] = None
        self._session_messages: list[dict] = []
        self._save_timer = None
        self._save_callback: Optional[Callable] = None

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def sizeHint(self) -> QSize:
        return QSize(340, 400)
    @property
    def feed_layout(self):
        return self._feed_layout

    @property
    def feed_container(self):
        return self._feed_container

    @property
    def streaming_bubble(self) -> Optional[MessageBubble]:
        return self._streaming_bubble

    @property
    def active_tool_block(self) -> Optional[ToolBlock]:
        return self._active_tool_block

    @active_tool_block.setter
    def active_tool_block(self, block: Optional[ToolBlock]) -> None:
        self._active_tool_block = block

    @property
    def pending_tool_code(self) -> str:
        return getattr(self, "_pending_tool_code", "")

    @pending_tool_code.setter
    def pending_tool_code(self, code: str) -> None:
        self._pending_tool_code = code

    def set_save_callback(self, callback: Callable) -> None:
        self._save_callback = callback

    def add_bubble(self, sender: str, text: str, msg_type: str = "assistant") -> None:
        bubble = MessageBubble(sender, text, msg_type)
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, bubble)
        QTimer.singleShot(50, self.scroll_to_bottom)
        self._session_messages.append({"role": msg_type, "text": text, "time": now_stamp()})
        if len(self._session_messages) > 200:
            self._session_messages = self._session_messages[-200:]
        self._schedule_save()

    def add_tool_block(self, name: str, status: str, details: str = "", code: str = "") -> ToolBlock:
        block = ToolBlock(name, status, details, code)
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, block)
        QTimer.singleShot(10, self.scroll_to_bottom)
        return block

    def add_image_card(
        self,
        source: Union[str, bytes],
        title: str = "IMAGE PREVIEW",
        caption: str = "",
        details: str = "",
    ) -> None:
        """Render a rich image preview card in the chat transcript (Aery standard).
        Accepts base64 strings, file paths (PNG/JPG/WEBP/TIF/SVG), or raw bytes.
        """
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background:{BG_CARD}; border:1px solid {BORDER}; border-radius:6px; }}"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        hdr = QLabel(title.upper())
        hdr.setStyleSheet(
            f"color:{ACCENT};font-family:{FONT_MONO};font-size:10px;font-weight:800;background:transparent;"
        )
        header.addWidget(hdr)
        header.addStretch()
        time_lbl = QLabel(now_stamp())
        time_lbl.setStyleSheet(
            f"color:{TEXT_MUTED};font-family:{FONT_MONO};font-size:9px;background:transparent;"
        )
        header.addWidget(time_lbl)
        lay.addLayout(header)

        qimg = QImage()
        file_path = ""
        try:
            if isinstance(source, bytes):
                qimg.loadFromData(source)
            elif isinstance(source, str):
                if os.path.exists(source) and os.path.isfile(source):
                    file_path = source
                    qimg.load(source)
                elif source.startswith("data:image/") and ";base64," in source:
                    b64 = source.split(";base64,")[1]
                    qimg.loadFromData(base64.b64decode(b64))
                elif source.startswith("iVBORw0KGgo") or len(source) > 80:
                    qimg.loadFromData(base64.b64decode(source))
        except Exception as e:
            logger.debug(f"[Aery Image] Failed to load image: {e}")

        if not qimg.isNull():
            w, h = qimg.width(), qimg.height()
            pix = QPixmap.fromImage(qimg).scaledToWidth(
                260, Qt.TransformationMode.SmoothTransformation
            )
            img_lbl = QLabel()
            img_lbl.setPixmap(pix)
            img_lbl.setMaximumWidth(260)
            img_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
            img_lbl.setStyleSheet("background:transparent; border-radius:4px;")
            lay.addWidget(img_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

            meta_info = f"{w}×{h} px"
            if file_path:
                meta_info += f" · {os.path.basename(file_path)}"
            if details:
                meta_info += f" · {details}"

            meta_lbl = QLabel(meta_info)
            meta_lbl.setStyleSheet(
                f"color:{TEXT_MUTED};font-family:{FONT_MONO};font-size:9px;background:transparent;"
            )
            lay.addWidget(meta_lbl)
        else:
            err_lbl = QLabel(f"[Unable to display image: {caption or 'invalid format'}]")
            err_lbl.setStyleSheet(f"color:{ERROR_COLOR};font-size:10px;background:transparent;")
            lay.addWidget(err_lbl)

        if caption:
            cap_lbl = QLabel(escape_html(caption))
            cap_lbl.setWordWrap(True)
            cap_lbl.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;background:transparent;")
            lay.addWidget(cap_lbl)

        self._feed_layout.insertWidget(self._feed_layout.count() - 1, frame)
        QTimer.singleShot(50, self.scroll_to_bottom)

    def add_canvas_image(self, b64_data: str) -> None:
        self.add_image_card(b64_data, title="CANVAS CAPTURE", caption="Live map canvas snapshot")
    def show_project_guard(self, queued_prompt: str, on_ready) -> None:
        guard = ProjectGuardWidget(queued_prompt, on_ready, self._feed_container)
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, guard)
        QTimer.singleShot(50, self.scroll_to_bottom)

    def clear(self) -> None:
        while self._feed_layout.count() > 1:
            item = self._feed_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._streaming_bubble = None
        self._streaming_text = ""
        self._active_tool_block = None

    def scroll_to_bottom(self) -> None:
        scrollbar = self.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())

    def finalize_streaming(self) -> None:
        self._streaming_bubble = None
        self._streaming_text = ""
        self._active_tool_block = None
        self._pending_tool_code = ""

    def find_last_assistant_bubble(self) -> Optional[MessageBubble]:
        for i in range(self._feed_layout.count() - 2, -1, -1):
            item = self._feed_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                if isinstance(w, MessageBubble) and w.objectName().startswith("msg_assistant"):
                    return w
        return None

    def get_session_messages(self) -> list[dict]:
        return self._session_messages

    def set_session_messages(self, msgs: list[dict]) -> None:
        self._session_messages = msgs

    def _schedule_save(self) -> None:
        if self._save_callback:
            self._save_callback()
