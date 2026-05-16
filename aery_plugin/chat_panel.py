"""Simplified Aery QGIS chat panel with settings menu."""

import base64
import json
import os
import re
from datetime import datetime
from typing import Any, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeyEvent, QPixmap, QImage, QTextOption
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


BG_BASE = "#0D0E15"
BG_SURFACE = "#12131A"
BG_PANEL = "#1A1B22"
BG_HIGH = "#292931"
BG_CARD = "#1E1F26"
ACCENT = "#57F1DB"
ACCENT_DIM = "#2DD4BF"
BORDER = "#3C4A46"
TEXT_MAIN = "#E3E1EC"
TEXT_DIM = "#BACAC5"
TEXT_MUTED = "#859490"
ERROR_COLOR = "#FFB4AB"
WARNING_COLOR = "#FFD1AA"
SUCCESS_COLOR = "#8EE7A8"
FONT_SANS = "Inter, Aptos, Segoe UI, sans-serif"
FONT_MONO = "JetBrains Mono, Consolas, monospace"


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Cache of layer names for linkification — refreshed on project/layer change
_layer_name_cache: list[str] = []


def _refresh_layer_cache() -> None:
    global _layer_name_cache
    try:
        from qgis.core import QgsProject
        _layer_name_cache = [lyr.name() for lyr in QgsProject.instance().mapLayers().values()]
    except Exception:
        _layer_name_cache = []


def _format_text_html(text: str) -> str:
    """Convert agent text into compact readable HTML, linkifying layer names."""
    if not text:
        return ""
    html = _escape_html(text)
    html = re.sub(
        r"```(\w*)\n?(.*?)```",
        lambda m: (
            f"<pre style='background:{BG_BASE};border:1px solid {BORDER};"
            f"border-radius:4px;padding:8px;font-family:{FONT_MONO};"
            f"font-size:11px;line-height:1.45;color:{ACCENT};white-space:pre-wrap;'>"
            f"{m.group(2).strip()}</pre>"
        ),
        html,
        flags=re.DOTALL,
    )
    html = re.sub(
        r"`([^`]+)`",
        lambda m: (
            f"<code style='background:{BG_HIGH};padding:1px 5px;border-radius:3px;"
            f"font-family:{FONT_MONO};color:{ACCENT};'>{m.group(1)}</code>"
        ),
        html,
    )
    html = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", html)
    # Linkify layer names using cache (no QGIS API call per render)
    for name in _layer_name_cache:
        esc = _escape_html(name)
        if esc in html:
            html = html.replace(
                esc,
                f"<a href='layer://{esc}' style='color:{ACCENT_DIM};text-decoration:underline;'>{esc}</a>",
                1,
            )
    return html.replace("\n", "<br>")


def _now_stamp() -> str:
    return datetime.now().strftime("%H:%M")


def _style_button(btn: QPushButton, active: bool = False, danger: bool = False) -> None:
    fg = ERROR_COLOR if danger else (BG_BASE if active else TEXT_DIM)
    bg = ACCENT if active else "transparent"
    border_acc = ACCENT if active else BORDER
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(f"""
        QPushButton {{
            background: {bg};
            border: 1px solid {border_acc};
            border-radius: 4px;
            color: {fg};
            font-family: {FONT_MONO};
            font-size: 9px;
            font-weight: 700;
            padding: 5px 6px;
        }}
        QPushButton:hover {{
            background: {BG_HIGH};
            color: {ERROR_COLOR if danger else ACCENT};
            border-color: {ERROR_COLOR if danger else ACCENT};
        }}
    """)


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

        colors = {
            "assistant": ACCENT,
            "user": TEXT_DIM,
            "error": ERROR_COLOR,
            "system": TEXT_MUTED,
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
        time_lbl = QLabel(_now_stamp())
        time_lbl.setStyleSheet(
            f"color:{TEXT_MUTED};font-family:{FONT_MONO};font-size:9px;background:transparent;"
        )
        header.addWidget(time_lbl)
        layout.addLayout(header)

        body = QLabel(_format_text_html(text))
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        body.setOpenExternalLinks(False)
        body.linkActivated.connect(self.on_link)
        body.setStyleSheet(
            f"color:{TEXT_MAIN};font-family:{FONT_SANS};font-size:14px;"
            "line-height:1.6;background:transparent;"
        )
        layout.addWidget(body)

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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)


class ToolBlock(QFrame):
    """Compact tool trace card."""

    def __init__(
        self,
        name: str,
        status: str = "running",
        details: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("toolBlock")
        status_color = {
            "running": ACCENT,
            "done": SUCCESS_COLOR,
            "error": ERROR_COLOR,
        }.get(status, TEXT_MUTED)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        nm = QLabel(name.upper())
        nm.setStyleSheet(
            f"color:{WARNING_COLOR};font-family:{FONT_MONO};font-size:10px;"
            "font-weight:800;letter-spacing:0.07em;background:transparent;"
        )
        row.addWidget(nm)
        row.addStretch()
        st = QLabel(status.upper())
        st.setStyleSheet(
            f"color:{status_color};font-family:{FONT_MONO};font-size:9px;"
            "font-weight:800;background:transparent;"
        )
        row.addWidget(st)
        layout.addLayout(row)
        if details:
            body = QLabel(_escape_html(details))
            body.setWordWrap(True)
            body.setStyleSheet(
                f"color:{TEXT_DIM};font-family:{FONT_MONO};font-size:10px;background:transparent;"
            )
            layout.addWidget(body)
            # Copy button
            copy_btn = QPushButton("COPY")
            copy_btn.setFixedHeight(20)
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{TEXT_MUTED}; border:1px solid {BORDER};"
                f" border-radius:2px; font-size:7px; font-weight:700; padding:0 6px; }}"
                f" QPushButton:hover {{ color:{ACCENT}; border-color:{ACCENT}; }}"
            )
            _details = details
            copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(_details))
            row.addWidget(copy_btn)
        self.setStyleSheet(f"""
            #toolBlock {{
                background: {BG_PANEL};
                border: 1px solid {BORDER};
                border-left: 3px solid {WARNING_COLOR};
                border-radius: 6px;
            }}
        """)


class PromptTextEdit(QTextEdit):
    """Prompt editor with submit/newline/abort/history keyboard behavior."""

    def __init__(self, submit_callback, abort_callback, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._submit_callback = submit_callback
        self._abort_callback = abort_callback
        self._history: list[str] = []
        self._history_idx = -1
        self._saved_draft = ""

    def set_history(self, history: list[str]) -> None:
        self._history = history

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            if event.modifiers() == Qt.KeyboardModifier.NoModifier:
                self._submit_callback()
                self._history_idx = -1
                event.accept()
                return
        if event.key() == Qt.Key.Key_C and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._abort_callback()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Up and not self._history:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Up:
            if self._history_idx == -1:
                self._saved_draft = self.toPlainText()
                self._history_idx = len(self._history) - 1
            elif self._history_idx > 0:
                self._history_idx -= 1
            self.setPlainText(self._history[self._history_idx])
            event.accept()
            return
        if event.key() == Qt.Key.Key_Down:
            if self._history_idx == -1:
                super().keyPressEvent(event)
                return
            if self._history_idx < len(self._history) - 1:
                self._history_idx += 1
                self.setPlainText(self._history[self._history_idx])
            else:
                self._history_idx = -1
                self.setPlainText(self._saved_draft)
            event.accept()
            return
        super().keyPressEvent(event)


class InfoDialog(QDialog):
    """Utility window for settings menu items."""

    def __init__(self, title: str, body: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 380)
        self.setStyleSheet(f"""
            QDialog {{ background:{BG_SURFACE}; color:{TEXT_MAIN}; }}
            QLabel {{ color:{TEXT_DIM}; font-family:{FONT_SANS}; }}
            QTextEdit {{
                background:{BG_BASE}; color:{TEXT_MAIN}; border:1px solid {BORDER};
                border-radius:6px; font-family:{FONT_MONO}; font-size:11px;
            }}
        """)
        layout = QVBoxLayout(self)
        heading = QLabel(title.upper())
        heading.setStyleSheet(
            f"color:{ACCENT};font-family:{FONT_MONO};font-size:12px;"
            "font-weight:900;letter-spacing:0.12em;"
        )
        layout.addWidget(heading)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(body)
        layout.addWidget(text)
        close = QPushButton("CLOSE")
        _style_button(close, active=True)
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)


class ProjectGuardWidget(QFrame):
    """Inline card shown when no QGIS project is saved.
    Lets the user create a new project or load an existing one,
    then fires the queued prompt automatically.
    """

    def __init__(self, queued_prompt: str, on_ready, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._queued_prompt = queued_prompt
        self._on_ready = on_ready  # callable(project_path: str)

        self.setStyleSheet(
            f"QFrame {{ background:{BG_PANEL}; border:1px solid {ACCENT};"
            f" border-radius:6px; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # Header
        hdr = QLabel("⚠  NO PROJECT OPEN")
        hdr.setStyleSheet(
            f"color:{ACCENT};font-family:{FONT_MONO};font-size:10px;"
            "font-weight:900;letter-spacing:0.1em;background:transparent;"
        )
        root.addWidget(hdr)

        sub = QLabel("Save your work to a project before running the agent.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;background:transparent;")
        root.addWidget(sub)

        # Project name row
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

        # Directory row
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
        browse_btn = QPushButton("…")
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

        # Action buttons
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


class ChatPanel(QDockWidget):
    """Simplified QGIS AI agent panel with settings menu."""

    def __init__(
        self,
        iface: Any,
        rpc_bridge,
        on_config: Optional[callable] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__("Aery", parent)
        self.iface = iface
        self.rpc = rpc_bridge
        self.on_config = on_config
        self._is_streaming = False
        self._history: list[str] = []
        self._history_idx = -1
        self._ready = False
        self._blink_on = True
        self._last_context: dict[str, Any] = {}
        self._dialogs: list[QDialog] = []
        self._active_stream_role: str = ""
        self._discard_stale_events = False
        self._allow_next_assistant_stream = False
        self._local_prompt_queue: list[str] = []
        self._session_context_injected = False  # inject QGIS env on first prompt
        self._session_messages: list[dict] = []  # persistent session memory
        self._retry_count = 0  # auto-correction retry counter

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.setMinimumWidth(260)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )

        self._build_ui()
        self.resize(300, 760)
        self.topLevelChanged.connect(self._sync_dock_button)
        self._apply_global_styles()
        self._activity_timer = QTimer(self)
        self._activity_timer.timeout.connect(self._tick_activity)
        self._activity_timer.start(650)
        self._sync_dock_button()
        self.setAcceptDrops(True)
        # Wire layer cache invalidation to QGIS layer signals
        try:
            from qgis.core import QgsProject
            QgsProject.instance().layersAdded.connect(_refresh_layer_cache)
            QgsProject.instance().layersRemoved.connect(_refresh_layer_cache)
        except Exception:
            pass

    def _build_ui(self) -> None:
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_transcript(), stretch=1)
        self._stream_label = QTextBrowser()
        self._stream_label.setVisible(False)
        self._stream_label.setOpenExternalLinks(False)
        self._stream_label.setMaximumHeight(200)
        self._stream_label.setStyleSheet(
            f"background:{BG_PANEL};color:{ACCENT};border:none;border-top:1px solid {BORDER};"
            f"padding:8px 12px;font-family:{FONT_SANS};font-size:13px;"
        )
        root.addWidget(self._stream_label)
        root.addWidget(self._build_activity_strip())
        root.addWidget(self._build_input())

        self.setWidget(container)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setFixedHeight(40)
        header.setStyleSheet(f"background:{BG_SURFACE};border-bottom:1px solid {BORDER};")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 0, 10, 0)

        self._status_dot = QLabel("\u25cf")
        self._status_dot.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:9px;background:transparent;"
        )
        layout.addWidget(self._status_dot)

        status = QLabel("GEOSPATIAL AGENT")
        status.setStyleSheet(
            f"color:{TEXT_MUTED};font-family:{FONT_MONO};font-size:9px;"
            "letter-spacing:0.08em;background:transparent;"
        )
        layout.addWidget(status)

        layout.addStretch()

        self._provider_lbl = QLabel("")
        self._provider_lbl.setStyleSheet(
            f"color:{ACCENT_DIM};font-family:{FONT_MONO};font-size:8px;"
            "font-weight:700;background:transparent;letter-spacing:0.04em;"
        )
        layout.addWidget(self._provider_lbl)
        self._refresh_provider_label()

        self._dock_btn = QToolButton()
        self._dock_btn.setToolTip("Dock / Undock")
        self._dock_btn.setAutoRaise(True)
        self._dock_btn.setText("⇱")
        self._dock_btn.setStyleSheet(
            f"QToolButton {{ color:{TEXT_DIM}; background:transparent; border:none; font-size:14px; padding:4px; }}"
            f"QToolButton:hover {{ color:{ACCENT}; background:{BG_HIGH}; border-radius:4px; }}"
        )
        self._dock_btn.clicked.connect(self._toggle_floating)
        layout.addWidget(self._dock_btn)

        self._gear_btn = QToolButton()
        self._gear_btn.setToolTip("Settings")
        self._gear_btn.setAutoRaise(True)
        self._gear_btn.setText("⚙")
        self._gear_btn.setStyleSheet(
            f"QToolButton {{ color:{TEXT_DIM}; background:transparent; border:none; font-size:14px; padding:4px; }}"
            f"QToolButton:hover {{ color:{ACCENT}; background:{BG_HIGH}; border-radius:4px; }}"
        )
        self._gear_btn.clicked.connect(self._show_settings_menu)
        layout.addWidget(self._gear_btn)

        return header

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if not path:
                continue
            ext = os.path.splitext(path)[1].lower()
            try:
                from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer
                raster_exts = {".tif", ".tiff", ".img", ".vrt", ".nc", ".hdf", ".h5"}
                name = os.path.splitext(os.path.basename(path))[0]
                if ext in raster_exts:
                    lyr = QgsRasterLayer(path, name)
                else:
                    lyr = QgsVectorLayer(path, name, "ogr")
                if lyr.isValid():
                    QgsProject.instance().addMapLayer(lyr)
                    self._add_bubble("SYSTEM", f"Loaded: {name}", "system")
                    self._dispatch_prompt(f"I've loaded '{name}' ({ext} file). Describe it and suggest what I can do with it.")
                else:
                    self._add_bubble("ERROR", f"Could not load: {path}", "error")
            except Exception as e:
                self._add_bubble("ERROR", str(e), "error")

    def _refresh_provider_label(self) -> None:
        try:
            from aery_plugin import oauth_helper
            active = oauth_helper.get_active_provider()
            if active:
                model = active.get("model", "")
                short_model = model.split("/")[-1] if "/" in model else model
                self._provider_lbl.setText(f"● {active['name']}  {short_model}".strip())
            else:
                self._provider_lbl.setText("● no provider")
        except Exception:
            self._provider_lbl.setText("● no provider")

    def _build_transcript(self) -> QScrollArea:
        self._feed_container = QWidget()
        self._feed_layout = QVBoxLayout(self._feed_container)
        self._feed_layout.setContentsMargins(12, 12, 12, 12)
        self._feed_layout.setSpacing(10)
        self._feed_layout.addStretch()
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._feed_container)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ background:{BG_BASE}; border:none; }}
            QScrollBar:vertical {{ width:5px; background:{BG_BASE}; }}
            QScrollBar::handle:vertical {{ background:{BORDER}; border-radius:2px; }}
        """)
        return self._scroll

    def _build_activity_strip(self) -> QFrame:
        self._activity_frame = QFrame()
        self._activity_frame.setFixedHeight(40)
        self._activity_frame.setVisible(False)
        self._activity_frame.setStyleSheet(
            f"background:{BG_SURFACE};border-top:1px solid {BORDER};"
        )
        layout = QHBoxLayout(self._activity_frame)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)
        self._activity_star = QLabel("\u273b")
        self._activity_star.setStyleSheet(
            f"color:{ACCENT};font-size:20px;font-family:{FONT_MONO};background:transparent;"
        )
        layout.addWidget(self._activity_star)
        self._activity_label = QLabel("ready")
        self._activity_label.setStyleSheet(
            f"color:{TEXT_DIM};font-family:{FONT_MONO};font-size:11px;"
            "font-weight:700;background:transparent;"
        )
        layout.addWidget(self._activity_label)
        layout.addStretch()
        self._activity_detail = QLabel("")
        self._activity_detail.setStyleSheet(
            f"color:{TEXT_MUTED};font-family:{FONT_MONO};font-size:10px;background:transparent;"
        )
        layout.addWidget(self._activity_detail)
        return self._activity_frame

    def _build_input(self) -> QFrame:
        bar = QFrame()
        self._input_bar = bar
        bar.setFixedHeight(66)
        bar.setStyleSheet(f"background:{BG_SURFACE};border-top:1px solid {BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self._input = PromptTextEdit(self._on_send, self._abort)
        self._input.set_history(self._history)
        self._input.setFixedHeight(46)
        self._input.setMinimumHeight(46)
        self._input.setMaximumHeight(140)
        self._input.setPlaceholderText("Enter geospatial command...")
        self._input.textChanged.connect(self._on_input_changed)
        self._input.textChanged.connect(self._autosize_input)
        self._input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._input.setStyleSheet(f"""
            QTextEdit {{
                background:{BG_BASE}; border:1px solid {BORDER}; border-radius:6px;
                color:{TEXT_MAIN}; padding:6px 12px; font-family:{FONT_SANS}; font-size:14px;
                selection-background-color:{ACCENT}; selection-color:{BG_BASE};
            }}
            QTextEdit:focus {{ border-color:{ACCENT}; }}
        """)
        layout.addWidget(self._input, stretch=1)
        self._send_btn = QPushButton("➤")
        self._send_btn.setFixedSize(34, 34)
        self._send_btn.clicked.connect(self._on_send_button)
        self._update_send_btn(streaming=False)
        layout.addWidget(self._send_btn)
        return bar

    def _apply_global_styles(self) -> None:
        self.setStyleSheet(f"""
            QDockWidget {{
                background:{BG_BASE};
                border:none;
                titlebar-close-icon:none;
                titlebar-normal-icon:none;
            }}
            QDockWidget::title {{
                background:{BG_SURFACE};
                height:0;
                font-size:0;
            }}
        """)

    def _tick_activity(self) -> None:
        self._blink_on = not self._blink_on
        color = ACCENT if self._blink_on or not self._is_streaming else TEXT_MUTED
        self._activity_star.setStyleSheet(
            f"color:{color};font-size:20px;font-family:{FONT_MONO};background:transparent;"
        )

    def _set_activity(self, text: str, active: bool = True, detail: str = "") -> None:
        self._is_streaming = active
        self._activity_label.setText(text)
        self._activity_detail.setText(detail if detail else "")
        self._activity_frame.setVisible(active)
        self._activity_star.setVisible(active)
        self._update_send_btn(streaming=active)
        dot_color = ACCENT if active else SUCCESS_COLOR
        self._status_dot.setStyleSheet(
            f"color:{dot_color};font-size:9px;background:transparent;"
        )

    def _activity_for_tool(self, name: str) -> str:
        normalized = name.lower()
        # QGIS execution tools
        if normalized in ("run_qgis_code", "qgis_code"):
            return "running QGIS code..."
        if normalized in ("run_processing", "run_processing_algorithm"):
            return "running processing..."
        if normalized in ("list_processing_algorithms",):
            return "listing algorithms..."
        if normalized in ("describe_processing_algorithm",):
            return "reading algorithm details..."
        if normalized in ("validate_processing_runtime",):
            return "checking processing runtime..."
        # Layer / data tools
        if "add_layer" in normalized:
            return "adding layer..."
        if "get_layer_info" in normalized:
            return "reading layer info..."
        if "export_layer" in normalized:
            return "exporting layer..."
        if "select_by_attribute" in normalized or "select_by_location" in normalized:
            return "selecting features..."
        # Context / validation
        if "get_project_context" in normalized:
            return "reading project context..."
        if "validate_project" in normalized:
            return "validating project..."
        # Capture
        if "capture" in normalized or "canvas" in normalized:
            return "capturing canvas..."
        # Web / search
        if "web_search" in normalized or normalized == "search":
            return "searching web..."
        if "web_fetch" in normalized or "fetch" in normalized:
            return "fetching web page..."
        # Code / file tools
        if normalized in ("read",):
            return "reading file..."
        if normalized in ("write", "edit"):
            return "writing file..."
        if "bash" in normalized:
            return "running command..."
        if "grep" in normalized or "search_files" in normalized:
            return "searching files..."
        if "find" in normalized:
            return "finding files..."
        if normalized in ("glob", "ls"):
            return "listing files..."
        # GEE
        if "gee" in normalized or "earth_engine" in normalized:
            return "running Earth Engine..."
        # User interaction
        if "ask_user" in normalized:
            return "asking you..."
        if "confirm_action" in normalized:
            return "waiting for confirmation..."
        # Tool registry
        if "register_tool" in normalized:
            return "registering tool..."
        if "list_registered_tools" in normalized or "list_tools" in normalized:
            return "listing tools..."
        if "remove_registered_tool" in normalized:
            return "removing tool..."
        # Audit
        if "audit" in normalized:
            return "reading audit trail..."
        # Fallback: strip common prefixes and underscores, make it a clean verb phrase
        stripped = normalized.replace("_", " ").replace("-", " ")
        return f"using {stripped}..."

    def _update_send_btn(self, streaming: bool) -> None:
        self._send_btn.setText("■" if streaming else "➤")
        if streaming:
            self._send_btn.setStyleSheet(f"""
                QPushButton {{
                    background:{BG_HIGH}; border:1px solid {ERROR_COLOR};
                    border-radius:17px; color:{ERROR_COLOR}; font-size:10px; font-weight:900;
                }}
                QPushButton:hover {{ background:{ERROR_COLOR}; color:{BG_BASE}; }}
            """)
        else:
            has_text = bool(getattr(self, "_input", None) and self._input.toPlainText().strip())
            bg = ACCENT if has_text else BG_HIGH
            fg = BG_BASE if has_text else TEXT_MUTED
            self._send_btn.setStyleSheet(f"""
                QPushButton {{
                    background:{bg}; border:1px solid {BORDER}; border-radius:17px;
                    color:{fg}; font-size:12px; font-weight:900;
                }}
                QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
            """)

    def _on_input_changed(self, *_args) -> None:
        self._update_send_btn(streaming=self._is_streaming)

    def _on_send_button(self) -> None:
        if self._is_streaming:
            self._abort()
            return
        self._on_send()

    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._history.append(text)
        self._history_idx = -1
        self._add_bubble("YOU", text, "user")
        self._input.clear()
        self._autosize_input()
        if self._is_streaming:
            self._local_prompt_queue.append(text)
            self._set_activity(f"{len(self._local_prompt_queue)} queued", active=True)
            return
        # ── Project guard ──
        if self._check_project_guard(text):
            return  # guard widget shown; prompt will be replayed after project setup
        self._dispatch_prompt(text)

    def _check_project_guard(self, text: str) -> bool:
        """Return True (and show guard widget) if no project is saved yet."""
        try:
            from qgis.core import QgsProject
            if QgsProject.instance().fileName():
                return False  # project is saved — all good
        except Exception:
            return False

        def on_ready(project_path: str):
            self._add_bubble("SYSTEM", f"Project ready: {project_path}", "system")
            self._refresh_provider_label()
            self._dispatch_prompt(text)

        guard = ProjectGuardWidget(text, on_ready, self._feed_container)
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, guard)
        QTimer.singleShot(50, self._scroll_to_bottom)
        return True

    def _build_qgis_env_context(self) -> str:
        """Build a rich QGIS environment snapshot to inject into the first prompt."""
        try:
            from qgis.core import QgsProject, QgsApplication
            import os, sys
            proj = QgsProject.instance()
            project_path = proj.fileName()
            project_dir = os.path.dirname(project_path) if project_path else os.path.expanduser("~")
            layers = []
            for lyr in proj.mapLayers().values():
                info = f"  - {lyr.name()} [{lyr.type().name}, {lyr.crs().authid() if lyr.crs() else 'no CRS'}]"
                if hasattr(lyr, "featureCount"):
                    info += f" {lyr.featureCount()} features"
                if hasattr(lyr, "bandCount"):
                    info += f" {lyr.bandCount()} bands"
                layers.append(info)

            # Processing providers
            providers = []
            try:
                for p in QgsApplication.processingRegistry().providers():
                    providers.append(p.id())
            except Exception:
                pass

            # Canvas state
            canvas_info = ""
            try:
                canvas = self.iface.mapCanvas() if hasattr(self, "iface") and self.iface else None
                if canvas:
                    crs = canvas.mapSettings().destinationCrs().authid()
                    scale = int(canvas.scale())
                    canvas_info = f"Canvas CRS: {crs}, Scale: 1:{scale:,}"
            except Exception:
                pass

            lines = [
                "=== QGIS ENVIRONMENT ===",
                f"Project: {project_path or '(unsaved)'}",
                f"Project dir: {project_dir}",
                f"QGIS Python: {sys.executable}",
                f"Layers ({len(layers)}):",
            ] + (layers if layers else ["  (none)"])

            # ── Record all existing layers in the graph ──────────────────
            try:
                from aery_plugin.graph_engine import record_layer
                for _lyr in proj.mapLayers().values():
                    record_layer(project_dir, _lyr.name(), _lyr.type().name,
                                 _lyr.crs().authid() if _lyr.crs() else "",
                                 _lyr.source())
            except Exception:
                pass

            # ── Detect spatial relationships between same-CRS layers ──────
            try:
                from aery_plugin.graph_engine import auto_detect_spatial_relationships
                auto_detect_spatial_relationships(project_dir)
            except Exception:
                pass

            # ── CRS health summary ───────────────────────────────────────
            _crs_groups: dict[str, list[str]] = {}
            for _lyr in proj.mapLayers().values():
                _auth = _lyr.crs().authid() if _lyr.crs() else "unknown"
                _crs_groups.setdefault(_auth, []).append(_lyr.name())
            if len(_crs_groups) > 1:
                _warn = ["⚠️  CRS MISMATCH: project has " + str(len(_crs_groups)) + " different CRS systems:"]
                for _crs, _names in sorted(_crs_groups.items()):
                    _preview = ", ".join(_names[:3]) + ("..." if len(_names) > 3 else "")
                    _warn.append(f"  {_crs} — {len(_names)} layers ({_preview})")
                _warn.append('Run "Reproject all layers to a common CRS" before spatial operations.')
                lines += _warn

            lines += [
                f"Processing providers: {', '.join(providers) if providers else 'unknown'}",
            ]
            if canvas_info:
                lines.append(canvas_info)
            lines.append("=== END ENVIRONMENT ===")
            return "\n".join(lines)
        except Exception:
            return ""

    def _dispatch_prompt(self, text: str) -> None:
        """Send prompt to agent, injecting QGIS env + graph context on the first call."""
        if self._discard_stale_events:
            self._allow_next_assistant_stream = True
        if not self.rpc:
            return
        self._set_activity("thinking...", active=True)

        # Record prompt in session graph
        try:
            from qgis.core import QgsProject
            import os
            path = QgsProject.instance().fileName()
            if path:
                from aery_plugin.graph_engine import record_prompt
                record_prompt(os.path.dirname(path), text, [], [])
        except Exception:
            pass

        if not self._session_context_injected:
            self._session_context_injected = True
            env_ctx = self._build_qgis_env_context()
            graph_ctx = self._build_graph_context(text)
            parts = [p for p in [env_ctx, graph_ctx] if p]
            full_prompt = "\n\n".join(parts) + f"\n\nUser request: {text}" if parts else text
            self.rpc.prompt(full_prompt)
        else:
            graph_ctx = self._build_graph_context(text)
            self.rpc.prompt(f"{graph_ctx}\n\nUser request: {text}" if graph_ctx else text)

    def _build_graph_context(self, prompt: str = "") -> str:
        """Return compact graph context string filtered by prompt keywords."""
        try:
            from qgis.core import QgsProject
            path = QgsProject.instance().fileName()
            if not path:
                return ""
            from aery_plugin.graph_engine import get_context_for_prompt
            return get_context_for_prompt(os.path.dirname(path), prompt)
        except Exception:
            return ""

    def _abort(self) -> None:
        self._discard_stale_events = True
        self._allow_next_assistant_stream = False
        self._active_stream_role = ""
        self._local_prompt_queue.clear()
        self._retry_count = 0
        if self.rpc:
            self.rpc.abort()
        self._cancel_streaming()
        self._add_bubble("SYSTEM", "Operation aborted.", "system")

    def _cancel_streaming(self) -> None:
        self._stream_label.clear()
        self._stream_label.setVisible(False)
        self._set_activity("ready", active=False)

    def _end_streaming(self) -> None:
        final_text = self._stream_label.toPlainText()
        self._stream_label.clear()
        self._stream_label.setVisible(False)
        if final_text.strip():
            self._add_bubble("AERY", final_text, "assistant")
        if self._local_prompt_queue and self.rpc and not self._discard_stale_events:
            next_text = self._local_prompt_queue.pop(0)
            qlen = len(self._local_prompt_queue)
            self._set_activity(str(qlen) + " queued" if qlen else "thinking...", active=True)
            self._dispatch_prompt(next_text)
        else:
            self._set_activity("ready", active=False)

    def _sync_dock_button(self) -> None:
        self._dock_btn.setText("⇲" if self.isFloating() else "⇱")

    def _toggle_floating(self) -> None:
        self.setFloating(not self.isFloating())
        self._sync_dock_button()

    def _show_settings_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background:{BG_SURFACE};
                border:1px solid {BORDER};
                padding:4px;
                font-family:{FONT_MONO};
                font-size:10px;
                color:{TEXT_MAIN};
            }}
            QMenu::item {{
                padding:6px 14px;
                border-radius:2px;
            }}
            QMenu::item:selected {{
                background:{BG_HIGH};
                color:{ACCENT};
            }}
            QMenu::separator {{
                height:1px;
                background:{BORDER};
                margin:4px 8px;
            }}
        """)
        his = menu.addAction("SESSION HISTORY")
        his.triggered.connect(self._show_history_window)
        aud = menu.addAction("AUDIT TRAIL")
        aud.triggered.connect(self._show_audit_window)
        lay = menu.addAction("LAYERS")
        lay.triggered.connect(self._show_layers_window)
        reg = menu.addAction("TOOL REGISTRY")
        reg.triggered.connect(self._show_tool_registry)
        menu.addSeparator()
        cfg = menu.addAction("AERY CONFIGURATION")
        cfg.triggered.connect(self._on_cfg_clicked)
        ref = menu.addAction("INTERFACE REFERENCES")
        ref.triggered.connect(self._show_references_window)
        menu.addSeparator()
        exp = menu.addAction("EXPORT REPORT")
        exp.triggered.connect(self._export_html_report)
        grp = menu.addAction("KNOWLEDGE GRAPH")
        grp.triggered.connect(self._show_graph_window)
        wdy = menu.addAction("WHAT DID YOU DO?")
        wdy.triggered.connect(self._show_session_summary)
        menu.addSeparator()
        mod = menu.addAction("MODEL")
        mod.triggered.connect(self._show_model_switcher)
        scp = menu.addAction("SCOPES MODEL")
        scp.triggered.connect(self._show_scopes_dialog)
        menu.addSeparator()
        cls = menu.addAction("CLEAR CHAT")
        cls.triggered.connect(self._on_clear_clicked)

        btn = self.sender()
        if btn:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            menu.exec(self.mapToGlobal(self.rect().topRight()))

    def _on_cfg_clicked(self) -> None:
        if self.on_config:
            self.on_config()

    def _show_model_switcher(self) -> None:
        try:
            from aery_plugin.provider_settings import ModelSwitcherDialog
            dlg = ModelSwitcherDialog(self)
            self._dialogs.append(dlg)
            dlg.exec()
            self._dialogs.remove(dlg)
            self._refresh_provider_label()
        except Exception as e:
            self._add_bubble("ERROR", f"Model switcher: {e}", "error")

    def _show_scopes_dialog(self) -> None:
        try:
            from aery_plugin.provider_settings import ScopesDialog
            dlg = ScopesDialog(self)
            self._dialogs.append(dlg)
            dlg.exec()
            self._dialogs.remove(dlg)
            self._refresh_provider_label()
        except Exception as e:
            self._add_bubble("ERROR", f"Scopes dialog: {e}", "error")

    def _autosize_input(self) -> None:
        doc_height = int(self._input.document().size().height()) + 16
        input_height = max(46, min(140, doc_height))
        self._input.setFixedHeight(input_height)
        if hasattr(self, "_input_bar"):
            self._input_bar.setFixedHeight(max(66, input_height + 20))

    def _on_clear_clicked(self) -> None:
        self._clear_feed()
        self._set_activity("ready", active=False)

    def _clear_feed(self) -> None:
        while self._feed_layout.count() > 1:
            item = self._feed_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _add_bubble(self, sender: str, text: str, msg_type: str = "assistant") -> None:
        bubble = MessageBubble(sender, text, msg_type)
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, bubble)
        QTimer.singleShot(50, self._scroll_to_bottom)
        # Persist to session
        self._session_messages.append({"role": msg_type, "text": text, "time": _now_stamp()})
        if len(self._session_messages) > 200:
            self._session_messages = self._session_messages[-200:]
        # Debounce: schedule save 2s after last bubble, cancel any pending save
        if hasattr(self, "_save_timer") and self._save_timer:
            self._save_timer.stop()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_session)
        self._save_timer.start(2000)

    def _add_canvas_image(self, b64_data: str) -> None:
        """Render a base64 PNG inline in the chat feed."""
        frame = QFrame()
        frame.setStyleSheet(f"background:{BG_CARD}; border:1px solid {BORDER}; border-radius:6px;")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(8, 8, 8, 8)
        hdr = QLabel("CANVAS CAPTURE")
        hdr.setStyleSheet(f"color:{ACCENT};font-family:{FONT_MONO};font-size:9px;font-weight:800;background:transparent;")
        lay.addWidget(hdr)
        img_lbl = QLabel()
        try:
            raw = base64.b64decode(b64_data)
            qimg = QImage.fromData(raw)
            pix = QPixmap.fromImage(qimg).scaledToWidth(460, Qt.TransformationMode.SmoothTransformation)
            img_lbl.setPixmap(pix)
        except Exception:
            img_lbl.setText("[image render failed]")
        img_lbl.setStyleSheet("background:transparent;")
        lay.addWidget(img_lbl)
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, frame)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _record_tool_output(self, tool_name: str, output_path: str) -> None:
        try:
            from qgis.core import QgsProject
            path = QgsProject.instance().fileName()
            if path:
                _pd = os.path.dirname(path)
                from aery_plugin.graph_engine import record_code_execution
                record_code_execution(_pd, tool_name, "", "", [], [output_path], True)
                # ── Field registry: if output is a vector, record its fields ──
                try:
                    from qgis.core import QgsVectorLayer
                    from aery_plugin.graph_engine import record_field
                    _vl = QgsVectorLayer(output_path, "_tmp", "ogr")
                    if _vl.isValid():
                        for _f in _vl.fields():
                            record_field(_pd, _vl.name(), _f.name(),
                                         _f.typeName(), tool_name)
                except Exception:
                    pass
        except Exception:
            pass

    def _handle_code_error(self, error_msg: str) -> None:
        """Auto-retry run_qgis_code on failure, up to 2 times."""
        if self._retry_count >= 2:
            self._retry_count = 0
            self._add_bubble("SYSTEM", "Auto-retry limit reached (2). Please review the error.", "system")
            return
        self._retry_count += 1
        retry_prompt = (
            f"The previous code execution failed with this error:\n{error_msg}\n\n"
            f"Fix the error and retry. Attempt {self._retry_count}/2."
        )
        self._add_bubble("SYSTEM", f"Auto-retrying ({self._retry_count}/2)…", "system")
        if self.rpc:
            self.rpc.prompt(retry_prompt)

    def _add_tool_block(self, name: str, status: str, details: str = "") -> None:
        if status == "running":
            self._set_activity(self._activity_for_tool(name), active=True)
        elif status == "error":
            self._set_activity("tool failed", active=True, detail=str(name))
        else:
            self._set_activity("thinking...", active=True)

    def _scroll_to_bottom(self) -> None:
        scrollbar = self._scroll.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())

    def connect_rpc(self) -> None:
        if not self.rpc:
            return
        self.rpc.event_received.connect(self._on_event)
        self.rpc.response_received.connect(self._on_response)
        self.rpc.error_occurred.connect(self._on_error)
        self.rpc.process_exited.connect(self._on_exit)

    def disconnect_rpc(self) -> None:
        if not self.rpc:
            return
        for sig_name in ("event_received", "response_received", "error_occurred", "process_exited"):
            try:
                sig = getattr(self.rpc, sig_name, None)
                if sig:
                    sig.disconnect()
            except TypeError:
                pass

    def set_rpc(self, rpc) -> None:
        self.disconnect_rpc()
        self.rpc = rpc
        self.connect_rpc()
        self._refresh_provider_label()

    def _extract_text(self, event: dict) -> str:
        blocks = []
        for key in ("message", "partial"):
            msg = event.get(key)
            if isinstance(msg, dict):
                content = msg.get("content", [])
                if isinstance(content, list):
                    blocks.extend(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                if not blocks and isinstance(msg.get("errorMessage"), str):
                    blocks.append(msg.get("errorMessage", ""))
        return "".join(blocks)

    def _event_role(self, event: dict) -> str:
        for key in ("message", "partial"):
            msg = event.get(key)
            if isinstance(msg, dict) and isinstance(msg.get("role"), str):
                return msg["role"]
        return ""

    def _is_background_noise(self, msg: str) -> bool:
        text = (msg or "").strip()
        if not text:
            return True
        lowered = text.lower()
        if "qgisinterface" in lowered and "has no attribute 'project'" in lowered:
            return True
        if "name 'os' is not defined" in lowered:
            return True
        if text.startswith("{") and text.endswith("}"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and {"project_path", "project_dir", "layers", "crs"}.issubset(data.keys()):
                self._last_context = data
                return True
        return False

    def _on_event(self, event: dict) -> None:
        etype = event.get("type", "")
        if etype == "message_start":
            role = event.get("message", {}).get("role", "")
            role = role if isinstance(role, str) else ""
            if self._discard_stale_events:
                if role == "assistant" and self._allow_next_assistant_stream:
                    self._discard_stale_events = False
                    self._allow_next_assistant_stream = False
                else:
                    return
            self._active_stream_role = role
            if self._active_stream_role == "assistant":
                self._stream_label.clear()
                self._stream_label.setVisible(True)
                initial = self._extract_text(event)
                if initial and not self._is_background_noise(initial):
                    self._stream_label.setHtml(_format_text_html(initial))
                self._set_activity("thinking...", active=True)
        elif etype == "message_update":
            if self._discard_stale_events:
                return
            if self._active_stream_role != "assistant" and self._event_role(event) != "assistant":
                return
            text = self._extract_text(event)
            if text and not self._is_background_noise(text):
                self._stream_label.setVisible(True)
                self._stream_label.setHtml(_format_text_html(text))
        elif etype == "message_end":
            if self._discard_stale_events:
                self._active_stream_role = ""
                return
            event_role = self._event_role(event)
            if event_role == "user":
                self._active_stream_role = ""
                return
            if self._active_stream_role not in {"", "assistant"} and event_role != "assistant":
                self._active_stream_role = ""
                return
            if not self._stream_label.toPlainText().strip():
                final_text = self._extract_text(event)
                if final_text and not self._is_background_noise(final_text):
                    self._stream_label.setHtml(_format_text_html(final_text))
            self._active_stream_role = ""
            self._end_streaming()
        elif etype == "tool_execution_start":
            if self._discard_stale_events:
                return
            name = event.get("toolName", event.get("tool", event.get("name", "tool")))
            self._add_tool_block(str(name), "running")
        elif etype == "tool_execution_end":
            if self._discard_stale_events:
                return
            name = event.get("toolName", event.get("tool", event.get("name", "tool")))
            result = event.get("result", "")
            err = event.get("error")
            if str(name).lower() == "get_project_context" and isinstance(result, dict):
                self._last_context = result
            if err:
                self._add_tool_block(str(name), "error", str(err))
                # Error self-correction: retry run_qgis_code up to 2x
                if "run_qgis" in str(name).lower() or "qgis_code" in str(name).lower():
                    self._handle_code_error(str(err))
            else:
                detail = result if isinstance(result, str) else json.dumps(result, indent=2) if result else ""
                # Inline image for canvas captures
                if isinstance(result, str) and result.startswith("iVBORw0KGgo") and len(result) > 100:
                    self._add_canvas_image(result)
                else:
                    self._add_tool_block(str(name), "done", detail[:500] if detail else "")
                # Extract OUTPUT path and record in graph
                if isinstance(result, dict) and "OUTPUT" in result:
                    self._record_tool_output(str(name), result["OUTPUT"])
        elif etype == "agent_end":
            if self._discard_stale_events:
                return
            self._active_stream_role = ""
            self._end_streaming()

    def _on_response(self, _command: str, data: dict) -> None:
        if not data.get("success"):
            self._add_bubble("ERROR", str(data.get("error", "Engine error")), "error")
            self._end_streaming()

    def _on_error(self, msg: str) -> None:
        text = str(msg)
        if self._is_background_noise(text):
            self._set_activity("thinking..." if self._is_streaming else "ready", active=self._is_streaming)
            return
        self._add_bubble("ERROR", text, "error")
        self._end_streaming()

    def _on_exit(self, code: int) -> None:
        self._add_bubble("SYSTEM", f"Engine disconnected [{code}]", "error")
        self._set_activity("engine disconnected", active=False, detail=f"exit {code}")
        self._status_dot.setStyleSheet(
            f"color:{ERROR_COLOR};font-size:9px;background:transparent;"
        )
        # Show restart button in the activity strip
        if not hasattr(self, "_restart_btn"):
            self._restart_btn = QPushButton("RESTART ENGINE")
            self._restart_btn.setFixedHeight(24)
            self._restart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._restart_btn.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{ERROR_COLOR}; border:1px solid {ERROR_COLOR};"
                f" border-radius:3px; font-size:8px; font-weight:700; padding:0 10px; }}"
                f" QPushButton:hover {{ background:{ERROR_COLOR}; color:{BG_BASE}; }}"
            )
            self._restart_btn.clicked.connect(self._restart_engine)
            self._activity_frame.layout().addWidget(self._restart_btn)
        self._activity_frame.setVisible(True)
        self._restart_btn.setVisible(True)

    def _restart_engine(self) -> None:
        if hasattr(self, "_restart_btn"):
            self._restart_btn.setVisible(False)
        if self.rpc and not getattr(self, "_restarting", False):
            self._restarting = True
            try:
                self.rpc.shutdown()
                self.rpc.spawn()
                self._set_activity("restarting...", active=True)
                self._add_bubble("SYSTEM", "Engine restarting…", "system")
            finally:
                self._restarting = False

    def append_message(self, sender: str, text: str, msg_type: str = "assistant") -> None:
        self._add_bubble(sender, text, msg_type)

    def set_ready(self) -> None:
        if not self._ready:
            self._ready = True
            self.connect_rpc()
            self._set_activity("ready", active=False)
            _refresh_layer_cache()
            self._load_session()
            self._health_check()

    def on_project_changed(self) -> None:
        """Called when user opens/saves a different project."""
        self._session_context_injected = False
        self._refresh_provider_label()
        _refresh_layer_cache()
        try:
            from qgis.core import QgsProject
            path = QgsProject.instance().fileName()
            if path:
                self._add_bubble("SYSTEM", f"Project changed: {path}", "system")
                self._save_session()
                self._session_messages.clear()
                self._load_session()
        except Exception:
            pass

    def notify_layer_added(self, name: str, layer_type: str) -> None:
        _refresh_layer_cache()
        self._add_bubble("SYSTEM", f"Layer added: {name} [{layer_type}]", "system")
        # ── Record the new layer and re-detect spatial relationships ────
        try:
            from qgis.core import QgsProject
            from aery_plugin.graph_engine import record_layer, auto_detect_spatial_relationships
            import os
            path = QgsProject.instance().fileName() or ""
            lyr = next((l for l in QgsProject.instance().mapLayers().values()
                        if l.name() == name), None)
            if lyr:
                record_layer(os.path.dirname(path) if path else ".", name, layer_type,
                             lyr.crs().authid() if lyr.crs() else "", lyr.source())
                auto_detect_spatial_relationships(os.path.dirname(path) if path else ".")
        except Exception:
            pass

    def notify_layers_removed(self, count: int) -> None:
        _refresh_layer_cache()
        self._add_bubble("SYSTEM", f"{count} layer(s) removed from project.", "system")

    def _show_dialog(self, title: str, body: str) -> None:
        dialog = InfoDialog(title, body, self)
        self._dialogs.append(dialog)
        dialog.show()

    def _session_path(self) -> str:
        try:
            from qgis.core import QgsProject
            path = QgsProject.instance().fileName()
            if path:
                import os
                d = os.path.join(os.path.dirname(path), ".aery")
                os.makedirs(d, exist_ok=True)
                return os.path.join(d, "session.json")
        except Exception:
            pass
        return ""

    def _save_session(self) -> None:
        path = self._session_path()
        if not path:
            return
        try:
            import json
            with open(path, "w") as f:
                json.dump(self._session_messages[-200:], f, indent=2)
        except Exception:
            pass

    def _load_session(self) -> None:
        path = self._session_path()
        if not path or not os.path.exists(path):
            return
        try:
            import json
            with open(path) as f:
                msgs = json.load(f)
            if msgs:
                self._session_messages = msgs
                self._add_bubble("SYSTEM", f"Resumed session ({len(msgs)} messages)", "system")
        except Exception:
            pass

    def _health_check(self) -> None:
        """Check binary exists and agent dir is writable on startup."""
        import os
        from aery_plugin.rpc_bridge import _find_aery_binary, _get_agent_dir
        issues = []
        binary = _find_aery_binary()
        if not os.path.isfile(binary) and binary != "aery":
            issues.append(f"Binary not found: {binary}")
        agent_dir = _get_agent_dir()
        try:
            os.makedirs(agent_dir, exist_ok=True)
            test = os.path.join(agent_dir, ".write_test")
            with open(test, "w") as f:
                f.write("ok")
            os.unlink(test)
        except Exception as e:
            issues.append(f"Agent dir not writable: {e}")
        if issues:
            self._add_bubble("ERROR", "Plugin health check failed:\n" + "\n".join(issues), "error")

    def _export_html_report(self) -> None:
        """Export the current session as an HTML report to project_dir/.aery/report.html."""
        try:
            from qgis.core import QgsProject
            import os, json
            path = QgsProject.instance().fileName()
            if not path:
                self._add_bubble("SYSTEM", "Save your project first before exporting a report.", "system")
                return
            report_dir = os.path.join(os.path.dirname(path), ".aery")
            os.makedirs(report_dir, exist_ok=True)
            report_path = os.path.join(report_dir, "report.html")
            rows = ""
            for msg in self._session_messages:
                role = msg.get("role", "system")
                text = msg.get("text", "")
                color = {"user": "#57F1DB", "assistant": "#8EE7A8", "error": "#FFB4AB"}.get(role, "#859490")
                rows += (
                    f'<div style="margin:12px 0;padding:10px 14px;border-left:3px solid {color};'
                    f'background:#12131A;border-radius:4px;">'
                    f'<div style="color:{color};font-size:10px;font-weight:700;margin-bottom:6px;">'
                    f'{role.upper()}  <span style="color:#52525b">{msg.get("time","")}</span></div>'
                    f'<div style="color:#e4e4e7;font-size:13px;white-space:pre-wrap">{text}</div></div>'
                )
            html = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<title>Aery Session Report</title>"
                "<style>body{background:#09090b;color:#e4e4e7;font-family:Inter,sans-serif;max-width:860px;margin:40px auto;padding:0 20px}"
                "h1{color:#8abeb7;font-size:16px;letter-spacing:.1em}</style></head>"
                f"<body><h1>AERY SESSION REPORT</h1><p style='color:#52525b;font-size:11px'>{path}</p>"
                f"{rows}</body></html>"
            )
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(html)
            self._add_bubble("SYSTEM", f"Report exported: {report_path}", "system")
        except Exception as e:
            self._add_bubble("ERROR", f"Export failed: {e}", "error")

    def _show_session_summary(self) -> None:
        """Summarize what the agent did this session using the graph."""
        try:
            from qgis.core import QgsProject
            path = QgsProject.instance().fileName()
            if not path:
                self._show_dialog("Session Summary", "No project open.")
                return
            from aery_plugin.graph_engine import get_graph, NODE_OUTPUT, NODE_TOOL, NODE_PROMPT, EDGE_TRIGGERED
            g = get_graph(os.path.dirname(path))

            prompts = sorted(g.nodes_by_type(NODE_PROMPT), key=lambda x: x.get("ts", 0))
            outputs = sorted(g.nodes_by_type(NODE_OUTPUT), key=lambda x: x.get("ts", 0), reverse=True)
            tools_used = {n["label"] for n in g.nodes_by_type(NODE_TOOL)
                         if any(e["dst"] == n["id"] for e in g._edges)}

            lines = [f"SESSION SUMMARY — {len(prompts)} prompts", ""]
            if prompts:
                lines.append("WHAT YOU ASKED:")
                for p in prompts[-10:]:
                    lines.append(f"  • {p['label']}")
            if tools_used:
                lines += ["", "TOOLS USED:"]
                for t in sorted(tools_used):
                    lines.append(f"  • {t}")
            if outputs:
                lines += ["", "FILES PRODUCED:"]
                for o in outputs[:15]:
                    lines.append(f"  • {o['label']}  {o.get('path','')}")

            # Spatial relationships discovered
            from aery_plugin.graph_engine import EDGE_OVERLAPS, EDGE_CONTAINS
            spatial = [e for e in g._edges if e["rel"] in (EDGE_OVERLAPS, EDGE_CONTAINS)]
            if spatial:
                lines += ["", "SPATIAL RELATIONSHIPS FOUND:"]
                for e in spatial[:8]:
                    src = g._nodes.get(e["src"], {}).get("label", e["src"])
                    dst = g._nodes.get(e["dst"], {}).get("label", e["dst"])
                    lines.append(f"  • {src} {e['rel']} {dst} (confidence {e.get('weight', 0):.0%})")

            self._show_dialog("What Did You Do?", "\n".join(lines))
        except Exception as e:
            self._show_dialog("Session Summary", f"Error: {e}")

    def _show_graph_window(self) -> None:
        """Show knowledge graph stats and provenance query."""
        try:
            from qgis.core import QgsProject
            path = QgsProject.instance().fileName()
            if not path:
                self._show_dialog("Knowledge Graph", "No project open. Save a project first.")
                return
            from aery_plugin.graph_engine import get_graph
            g = get_graph(os.path.dirname(path))
            s = g.stats()
            lines = [
                f"Nodes: {s['nodes']}  Edges: {s['edges']}",
                "",
                "Node types:",
            ]
            for t, c in sorted(s["node_types"].items()):
                lines.append(f"  {t}: {c}")
            lines += ["", "Edge types:"]
            for r, c in sorted(s["edge_types"].items()):
                lines.append(f"  {r}: {c}")
            lines += ["", "--- RECENT OUTPUTS ---"]
            from aery_plugin.graph_engine import NODE_OUTPUT
            outputs = sorted(g.nodes_by_type(NODE_OUTPUT), key=lambda x: x.get("ts", 0), reverse=True)[:10]
            for o in outputs:
                lines.append(f"  {o['label']} ({o.get('path','')})")
            lines += ["", "--- LAYER PROVENANCE ---"]
            from aery_plugin.graph_engine import NODE_LAYER, query_provenance
            for lyr in g.nodes_by_type(NODE_LAYER)[:15]:
                lines.append(f"  {query_provenance(os.path.dirname(path), lyr['label'])}")
            self._show_dialog("Knowledge Graph", "\n".join(lines))
        except Exception as e:
            self._show_dialog("Knowledge Graph", f"Error: {e}")

    def _show_history_window(self) -> None:
        body = "\n".join(self._history[-50:]) or "No prompts in this panel yet."
        self._show_dialog("Session History", body)

    def _show_audit_window(self) -> None:
        project_dir = ""
        if self._last_context:
            project_dir = str(self._last_context.get("project_dir", ""))
        audit_path = os.path.join(
            project_dir or os.path.expanduser("~"), ".aery", "operations.jsonl"
        )
        try:
            with open(audit_path, "r", encoding="utf-8") as f:
                body = "".join(f.readlines()[-30:])
        except OSError:
            body = f"No audit trail found at:\n{audit_path}"
        self._show_dialog("Audit Trail", body)

    def _show_layers_window(self) -> None:
        layers = self._last_context.get("layers", [])
        if layers:
            body = "\n".join(
                f"- {layer.get('name', 'unknown')} | {layer.get('type', '')} | {layer.get('crs', '')}"
                for layer in layers
                if isinstance(layer, dict)
            )
        else:
            body = "No project context loaded yet.\nAsk Aery: 'validate this project'."
        self._show_dialog("Layers", body)

    def _show_tool_registry(self) -> None:
        from aery_plugin.tool_registry import ToolRegistryDialog
        dlg = ToolRegistryDialog(parent=self, rpc=self.rpc)
        self._dialogs.append(dlg)
        dlg.exec()
        self._dialogs.remove(dlg)

    def _show_references_window(self) -> None:
        body = (
            "=== AERY GEOSPATIAL AGENT ===\n\n"
            "A natural-language interface to QGIS.\n"
            "Describe geospatial tasks and the agent executes them.\n\n"
            "--- COMMANDS ---\n"
            "Type any geospatial request in the input bar.\n"
            "Press Enter or click \u2191 to send.\n"
            "Press \u25a0 to abort a running operation.\n\n"
            "--- KEYBOARD ---\n"
            "Enter: Send message\n"
            "Up/Down: Cycle through message history\n\n"
            "--- CAPABILITIES ---\n"
            "- Run QGIS Processing algorithms\n"
            "- Execute custom Python code in QGIS\n"
            "- Load and analyze spatial data\n"
            "- Capture canvas screenshots\n"
            "- Search the web for GIS data/docs\n"
            "- Run Google Earth Engine code\n"
            "- Read/write GeoJSON, Shapefile, GeoTIFF, GeoPackage\n\n"
            "--- OUTPUT ---\n"
            "All generated files go to your QGIS project directory.\n"
        )
        self._show_dialog("Interface References", body)
