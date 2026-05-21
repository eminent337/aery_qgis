"""Simplified Aery QGIS chat panel with settings menu."""
import base64
import json
import os
from datetime import datetime
from typing import Any, Optional


from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QKeyEvent, QPixmap, QImage, QTextOption, QIcon, QPainter
from PyQt6.QtSvg import QSvgRenderer
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from aery_plugin.activity_strip import ActivityStrip
from aery_plugin.input_area import InputArea
from aery_plugin.transcript_view import TranscriptView, MessageBubble, ToolBlock
from aery_plugin.ui_constants import (
    BG_BASE, BG_SURFACE, BG_PANEL, BG_HIGH, BG_CARD,
    ACCENT, ACCENT_DIM, BORDER, TEXT_MAIN, TEXT_DIM,
    TEXT_MUTED, ERROR_COLOR, WARNING_COLOR, SUCCESS_COLOR,
    FONT_SANS, FONT_MONO,
)
from aery_plugin.ui_helpers import SessionState, refresh_layer_cache
from aery_plugin.ui_utils import format_text_html
from aery_plugin.ui_dialogs import InfoDialog, _QuestionWidget

# backward-compat alias (function body lives in ui_helpers)
_refresh_layer_cache = refresh_layer_cache



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


def _svg_pixmap(path: str, size: int) -> QPixmap:
    try:
        dpr = QApplication.primaryScreen().devicePixelRatioF()
    except Exception:
        dpr = 1.0
    target = int(round(size * dpr))

    pix = QPixmap(target, target)
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)

    from PyQt6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(path)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    renderer.render(p)
    p.end()

    return pix



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


class _QuestionWidget(QFrame):
    """Inline question card embedded in the chat feed."""

    BG        = "#0D0E15"
    SURFACE   = "#12131A"
    ACCENT    = "#57F1DB"
    BORDER    = "#3C4A46"
    TEXT_MAIN = "#E3E1EC"
    TEXT_DIM  = "#BACAC5"
    TEXT_MUTED = "#859490"
    WARN      = "#FFD1AA"

    def __init__(
        self,
        event: dict,
        parent: Optional[QWidget] = None,
        feed_container: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._event     = event
        self._quest_id  = event.get("questId", "")
        self._options   = event.get("options", [])
        self._header    = event.get("header", "Question")
        self._body      = event.get("description", "")
        self._feed      = feed_container
        self._field_states: list[dict] = [{} for _ in self._options]
        self._option_frames: list[Optional[QFrame]] = [None for _ in self._options]
        self._selected: int = -1
        self._resolve_callback = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background:{self.SURFACE}; border:1px solid {self.BORDER}; border-radius:6px; padding:0; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(6)

        hdr = QLabel(self._header)
        hdr.setStyleSheet(f"color:{self.ACCENT};font-weight:700;font-size:13px;background:transparent;")
        root.addWidget(hdr)

        if self._body:
            body_lbl = QLabel(self._body)
            body_lbl.setWordWrap(True)
            body_lbl.setStyleSheet(f"color:{self.TEXT_DIM};font-size:12px;background:transparent;")
            root.addWidget(body_lbl)

        for idx, option in enumerate(self._options):
            opt_frame = QFrame()
            opt_frame.setObjectName(f"qopt_{idx}")
            opt_frame.setStyleSheet(
                f"QFrame {{ background:{self.BG}; border:1px solid {self.BORDER}; border-radius:4px; }}"
            )
            opt_lay = QVBoxLayout(opt_frame)
            opt_lay.setContentsMargins(10, 8, 10, 8)
            opt_lay.setSpacing(4)

            row = QHBoxLayout()
            row.setSpacing(6)
            dot = QLabel("\u25cb")
            dot.setFixedWidth(16)
            dot.setStyleSheet(f"color:{self.TEXT_MUTED};font-size:12px;background:transparent;")
            row.addWidget(dot)

            opt_label = QLabel(option.get("label", f"Option {idx + 1}"))
            opt_label.setWordWrap(True)
            opt_label.setStyleSheet(
                f"color:{self.TEXT_MAIN};font-weight:600;font-size:12px;background:transparent;"
            )
            row.addWidget(opt_label, 1)

            opt_desc = option.get("description", "")
            if opt_desc:
                d_lbl = QLabel(opt_desc)
                d_lbl.setWordWrap(True)
                d_lbl.setStyleSheet(f"color:{self.TEXT_MUTED};font-size:11px;background:transparent;")
                row.addWidget(d_lbl, 1)
            opt_lay.addLayout(row)
            opt_frame.setLayout(opt_lay)
            self._option_frames[idx] = opt_frame
            root.addWidget(opt_frame)

            rfields = option.get("required_fields", [])
            for f_def in rfields:
                fname  = f_def.get("name", "")
                flabel = f_def.get("label", fname)
                f_wrap = QWidget()
                fl = QHBoxLayout(f_wrap)
                fl.setContentsMargins(0, 2, 0, 2)
                fl.setSpacing(8)

                lbl = QLabel(f"  {flabel}:")
                lbl.setStyleSheet(f"color:{self.TEXT_MUTED};font-size:10px;background:transparent;")
                lbl.setFixedWidth(120)
                fl.addWidget(lbl)

                inp = QLineEdit()
                inp.setPlaceholderText(f_def.get("placeholder", ""))
                inp.setStyleSheet(
                    f"QLineEdit {{ background:{self.BG}; color:{self.TEXT_MAIN}; "
                    f"border:1px solid {self.BORDER}; border-radius:3px; padding:4px 6px; font-size:11px; }}"
                    f"QLineEdit:focus {{ border-color:{self.ACCENT}; }}"
                )
                fl.addWidget(inp, 1)

                def _on_change(_t, _fi=idx, _fn=fname, _in=inp):
                    self._field_states[_fi][_fn] = _in.text().strip()
                    self._update_submit()

                inp.textChanged.connect(_on_change)
                opt_lay.addWidget(f_wrap)

            arrow = QLabel("\u25b6")
            arrow.setObjectName(f"arrow_{idx}")
            arrow.setAlignment(Qt.AlignmentFlag.AlignTop)
            arrow.setFixedWidth(14)
            arrow.setStyleSheet(f"color:{self.TEXT_MUTED};font-size:9px;background:transparent;")
            opt_frame.layout().addWidget(arrow)
            opt_frame.layout().addSpacing(2)

        for idx, frame in enumerate(self._option_frames):
            if frame is None:
                continue
            prev_ref = [None]

            def _select(event=None, _idx=idx, _f=frame, _pr=prev_ref):
                if _pr[0]:
                    _pr[0].setStyleSheet(
                        f"QFrame {{ background:{self.BG}; border:1px solid {self.BORDER}; border-radius:4px; }}"
                    )
                    a = _pr[0].findChild(QLabel, f"arrow_{_pr[0].objectName().replace('qopt_', '')}")
                    if a:
                        a.setText("\u25b6")
                        a.setStyleSheet(f"color:{self.TEXT_MUTED};font-size:9px;background:transparent;")
                self._selected = _idx
                prev_ref[0] = _f
                _f.setStyleSheet(
                    f"QFrame {{ background:{'#1E2936'}; border:1px solid {self.ACCENT}; border-radius:4px; }}"
                )
                a = _f.findChild(QLabel, f"arrow_{_idx}")
                if a:
                    a.setText("\u25bc")
                    a.setStyleSheet(f"color:{self.ACCENT};font-size:9px;background:transparent;")
                self._update_submit()

            frame.mousePressEvent = _select

        self._submit_btn = QPushButton("Submit")
        self._submit_btn.setEnabled(False)
        self._submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._submit_btn.setStyleSheet(
            f"QPushButton {{ background:{self.ACCENT}; color:{self.BG}; border:none; "
            f"border-radius:4px; font-weight:700; font-size:12px; padding:6px 16px; }}"
            f"QPushButton:disabled {{ background:{'#3C4A46'}; color:{'#859490'}; }}"
            f"QPushButton:hover {{ background:{'#45D8C8'}; }}"
        )
        self._submit_btn.clicked.connect(self._on_submit)
        root.addWidget(self._submit_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _on_submit(self) -> None:
        idx = self._selected
        if idx < 0:
            return
        option   = self._options[idx]
        fields   = dict(self._field_states[idx])
        missing = [
            f.get("name")
            for f in option.get("required_fields", [])
            if not fields.get(f.get("name", ""), "").strip()
        ]
        if missing:
            self._submit_btn.setText(f"Fill required: {', '.join(missing)}")
            QTimer.singleShot(2000, lambda: self._submit_btn.setText("Submit"))
            return
        answer = {"option_label": option.get("label", ""), "fields": fields}
        if self._resolve_callback:
            self._resolve_callback(self._quest_id, answer)
        try:
            self.setParent(None)
            self.deleteLater()
        except Exception as e:
            print(f"Aery: widget cleanup error: {e}")

    def _update_submit(self) -> None:
        self._submit_btn.setEnabled(self._selected >= 0)

    def _resolve_answer(self, quest_id: str, answer: dict) -> None:
        try:
            from aery_plugin.qgis_executor import _resolve_question
            _resolve_question(quest_id, answer)
        except Exception as e:
            print(f"Aery: question resolve error: {e}")
        try:
            self.setParent(None)
            self.deleteLater()
        except Exception as e:
            print(f"Aery: widget cleanup error: {e}")


class ChatPanel(QDockWidget):
    """Simplified QGIS AI agent panel with settings menu."""

    def __init__(
        self,
        iface: Any,
        agent,
        on_config: Optional[callable] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__("Aery", parent)
        self.setTitleBarWidget(QWidget())

        self.iface = iface
        self.agent = agent
        self.on_config = on_config
        self._session_state = SessionState.IDLE
        self._history: list[str] = []
        self._history_idx = -1
        self._ready = False
        self._last_context: dict[str, Any] = {}
        self._dialogs: list[QDialog] = []
        self._active_stream_role: str = ""
        self._discard_stale_events = False
        self._allow_next_assistant_stream = False
        self._local_prompt_queue: list[str] = []
        self._session_context_injected = False
        self._retry_count = 0
        self._got_assistant_event = False
        self._streamlined_mode: bool = True
        self._stream_label = QTextBrowser()
        self._stream_label.setMaximumHeight(36)
        self._stream_label.setMinimumHeight(0)
        self._stream_label.setFrameShape(QFrame.Shape.NoFrame)
        self._stream_label.setStyleSheet(f"background:{BG_SURFACE};border:none;padding:4px 8px;font-size:12px;color:{TEXT_DIM};")

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.setMinimumWidth(320)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )

        self._transcript = TranscriptView(self)
        self._activity = ActivityStrip(self)
        self._input_area = InputArea(self._on_send, self._abort, self)

        self._transcript.set_save_callback(self._save_session)
        self._input_area.input.textChanged.connect(self._on_input_changed)
        self._input_area.input.textChanged.connect(self._autosize_input)
        self._input_area.send_btn.clicked.connect(self._on_send_button)

        self._build_ui()
        self.resize(320, 760)
        self.topLevelChanged.connect(self._sync_dock_button)
        self._apply_global_styles()
        self._sync_dock_button()
        self.setAcceptDrops(True)
        try:
            from qgis.core import QgsProject
            QgsProject.instance().layersAdded.connect(_refresh_layer_cache)
            QgsProject.instance().layersRemoved.connect(_refresh_layer_cache)
        except Exception as _e:
            print(f"Aery: could not connect layer-change signals: {_e}")

    @property
    def session_state(self) -> SessionState:
        return self._session_state

    @property
    def _is_streaming(self) -> bool:
        """Backward-compat alias (ChatPanel refactored to session_state enum)."""
        return self._session_state == SessionState.RUNNING

    @_is_streaming.setter
    def _is_streaming(self, value: bool) -> None:
        if value:
            self._set_session_state(SessionState.RUNNING)
        else:
            self._set_session_state(SessionState.IDLE)

    @property
    def _feed_layout(self):
        """Backward-compat alias (feed_layout moved to TranscriptView)."""
        return self._transcript.feed_layout

    @property
    def _input(self):
        """Backward-compat alias (input moved to InputArea)."""
        return self._input_area.input

    @property
    def _send_btn(self):
        """Backward-compat alias (send_btn moved to InputArea)."""
        return self._input_area.send_btn

    @property
    def _activity_frame(self):
        """Backward-compat alias (activity_frame replaced by ActivityStrip)."""
        return self._activity

    @_activity_frame.setter
    def _activity_frame(self, value):
        self._activity.setVisible(value)

    @property
    def _input_bar(self):
        """Backward-compat alias (input area widget)."""
        return self._input_area

    @property
    def _activity_star(self):
        """Backward-compat alias (star moved to ActivityStrip)."""
        return self._activity.star

    @property
    def _activity_label(self):
        """Backward-compat alias (label moved to ActivityStrip)."""
        return self._activity.label

    def _set_session_state(self, state: SessionState) -> None:
        if self._session_state == state:
            return
        self._session_state = state
        if state == SessionState.RUNNING:
            self._activity.set_active("working...")
            self._update_send_btn(streaming=True)
            self._status_dot.setStyleSheet(
                f"color:{ACCENT};font-size:9px;background:transparent;"
            )
        elif state == SessionState.IDLE:
            self._activity.set_idle()
            self._update_send_btn(streaming=False)
            self._status_dot.setStyleSheet(
                f"color:{SUCCESS_COLOR};font-size:9px;background:transparent;"
            )
        elif state == SessionState.REQUIRES_ACTION:
            self._activity.set_active("action required")
            self._update_send_btn(streaming=False)
            self._status_dot.setStyleSheet(
                f"color:{WARNING_COLOR};font-size:9px;background:transparent;"
            )

    def _build_ui(self) -> None:
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._transcript, stretch=1)
        root.addWidget(self._activity)
        root.addWidget(self._input_area)

        self.setWidget(container)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setFixedHeight(50)
        header.setStyleSheet(f"background:{BG_SURFACE};border-bottom:1px solid {BORDER};")
        outer = QHBoxLayout(header)
        outer.setContentsMargins(8, 4, 6, 4)
        outer.setSpacing(0)

        brand_col = QVBoxLayout()
        brand_col.setContentsMargins(0, 0, 0, 0)
        brand_col.setSpacing(0)

        top_row = QHBoxLayout()
        top_row.setSpacing(5)
        top_row.setContentsMargins(0, 0, 0, 0)

        icon_lbl = QLabel()
        svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icons", "aery.svg")
        icon_lbl.setPixmap(_svg_pixmap(svg_path, 28))
        icon_lbl.setFixedSize(28, 28)
        icon_lbl.setStyleSheet("background:transparent;")
        top_row.addWidget(icon_lbl)

        name_row = QHBoxLayout()
        name_row.setSpacing(0)
        name_row.setContentsMargins(0, 0, 0, 0)

        brand = QLabel("AERY")
        brand.setStyleSheet(
            f"color:{ACCENT};font-family:{FONT_SANS};font-size:17px;"
            "font-weight:700;letter-spacing:0.05em;background:transparent;"
        )
        name_row.addWidget(brand)

        geo = QLabel("Geospatial Agent")
        geo.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:9px;font-family:{FONT_SANS};"
            "background:transparent;margin-left:6px;"
        )
        name_row.addWidget(geo)
        name_row.addStretch(1)
        top_row.addLayout(name_row)

        brand_col.addLayout(top_row)

        self._provider_lbl = QLabel("")
        self._provider_lbl.setStyleSheet(
            f"color:{ACCENT_DIM};font-size:9px;font-weight:600;"
            "background:transparent;letter-spacing:0.03em;margin-top:2px;"
        )
        brand_col.addWidget(self._provider_lbl)
        self._refresh_provider_label()

        brand_col.addStretch(1)
        outer.addLayout(brand_col)

        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 2, 0, 0)
        right_col.setSpacing(0)

        status_row = QHBoxLayout()
        status_row.setSpacing(5)
        status_row.setContentsMargins(0, 0, 0, 0)
        self._status_dot = QLabel("\u25cf")
        self._status_dot.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:10px;background:transparent;"
        )
        status_row.addWidget(self._status_dot)
        status_row.addStretch()
        right_col.addLayout(status_row)

        control_row = QHBoxLayout()
        control_row.setSpacing(4)
        control_row.setContentsMargins(0, 2, 0, 1)
        control_row.addStretch()

        self._dock_btn = QToolButton()
        self._dock_btn.setToolTip("Dock / Undock")
        self._dock_btn.setAutoRaise(True)
        self._dock_btn.setFixedSize(18, 18)
        self._dock_btn.setText("\u21f1")
        self._dock_btn.setStyleSheet(
            f"QToolButton {{ color:{TEXT_DIM}; background:transparent; border:none; font-size:11px; }}"
            f"QToolButton:hover {{ color:{ACCENT}; background:{BG_HIGH}; border-radius:3px; }}"
        )
        self._dock_btn.clicked.connect(self._toggle_floating)
        control_row.addWidget(self._dock_btn)

        self._gear_btn = QToolButton()
        self._gear_btn.setToolTip("Settings")
        self._gear_btn.setAutoRaise(True)
        self._gear_btn.setFixedSize(18, 18)
        self._gear_btn.setText("\u2699")
        self._gear_btn.setStyleSheet(
            f"QToolButton {{ color:{TEXT_DIM}; background:transparent; border:none; font-size:11px; }}"
            f"QToolButton:hover {{ color:{ACCENT}; background:{BG_HIGH}; border-radius:3px; }}"
        )
        self._gear_btn.clicked.connect(self._show_settings_menu)
        control_row.addWidget(self._gear_btn)

        right_col.addLayout(control_row)
        outer.addLayout(right_col)

        return header

    def _refresh_provider_label(self) -> None:
        try:
            from aery_plugin import oauth_helper
            active = oauth_helper.get_active_provider()
            if active:
                model = active.get("model", "")
                short_model = model.split("/")[-1] if "/" in model else model
                self._provider_lbl.setText(f"\u25cf {active['name']}  {short_model}".strip())
            else:
                self._provider_lbl.setText("\u25cf no provider")
        except Exception:
            self._provider_lbl.setText("\u25cf no provider")

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

    def _set_activity(self, text: str, active: bool = True, detail: str = "") -> None:
        if active:
            if self._session_state != SessionState.RUNNING:
                self._set_session_state(SessionState.RUNNING)
            self._activity.set_active(text, detail)
        else:
            if self._session_state != SessionState.IDLE:
                self._set_session_state(SessionState.IDLE)
            self._activity.set_idle()

    def _update_send_btn(self, streaming: bool) -> None:
        has_text = bool(self._input_area.get_text())
        self._input_area.update_button(streaming, has_text)

    def _on_input_changed(self, *_args) -> None:
        self._update_send_btn(streaming=self._session_state == SessionState.RUNNING)

    def _on_send_button(self) -> None:
        if self._session_state == SessionState.RUNNING:
            self._abort()
            return
        self._on_send()

    def _on_send(self) -> None:
        text = self._input_area.get_text()
        if not text:
            return
        self._history.append(text)
        self._history_idx = -1
        self._transcript.add_bubble("YOU", text, "user")
        self._input_area.clear()
        self._autosize_input()
        if self._session_state == SessionState.RUNNING:
            self._local_prompt_queue.append(text)
            self._set_activity(f"{len(self._local_prompt_queue)} queued", active=True)
            return
        if self._check_project_guard(text):
            return
        self._dispatch_prompt(text)

    def _check_project_guard(self, text: str) -> bool:
        try:
            from qgis.core import QgsProject
            if QgsProject.instance().fileName():
                return False
        except Exception:
            return False

        def on_ready(project_path: str):
            self._transcript.add_bubble("SYSTEM", f"Project ready: {project_path}", "system")
            self._refresh_provider_label()
            self._dispatch_prompt(text)

        self._transcript.show_project_guard(text, on_ready)
        QTimer.singleShot(50, self._transcript.scroll_to_bottom)
        return True

    def _build_qgis_env_context(self) -> str:
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

            providers = []
            try:
                for p in QgsApplication.processingRegistry().providers():
                    providers.append(p.id())
            except Exception as _e:
                pass

            canvas_info = ""
            try:
                canvas = self.iface.mapCanvas() if hasattr(self, "iface") and self.iface else None
                if canvas:
                    crs = canvas.mapSettings().destinationCrs().authid()
                    scale = int(canvas.scale())
                    canvas_info = f"Canvas CRS: {crs}, Scale: 1:{scale:,}"
            except Exception as _e:
                pass

            lines = [
                "=== QGIS ENVIRONMENT ===",
                f"Project: {project_path or '(unsaved)'}",
                f"Project dir: {project_dir}",
                f"QGIS Python: {sys.executable}",
                f"Layers ({len(layers)}):",
            ] + (layers if layers else ["  (none)"])

            try:
                from aery_plugin.graph_engine import record_layer
                for _lyr in proj.mapLayers().values():
                    record_layer(project_dir, _lyr.name(), _lyr.type().name,
                                 _lyr.crs().authid() if _lyr.crs() else "",
                                 _lyr.source())
            except Exception as _e:
                pass

            try:
                from aery_plugin.graph_engine import auto_detect_spatial_relationships
                auto_detect_spatial_relationships(project_dir)
            except Exception as _e:
                pass

            _crs_groups: dict[str, list[str]] = {}
            for _lyr in proj.mapLayers().values():
                _auth = _lyr.crs().authid() if _lyr.crs() else "unknown"
                _crs_groups.setdefault(_auth, []).append(_lyr.name())
            if len(_crs_groups) > 1:
                _warn = ["\u26a0\ufe0f  CRS MISMATCH: project has " + str(len(_crs_groups)) + " different CRS systems:"]
                for _crs, _names in sorted(_crs_groups.items()):
                    _preview = ", ".join(_names[:3]) + ("..." if len(_names) > 3 else "")
                    _warn.append(f"  {_crs} \u2014 {len(_names)} layers ({_preview})")
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
        if not text:
            return
        self._set_activity("thinking...", active=True)
        self._allow_next_assistant_stream = True
        self._got_assistant_event = False

        try:
            from qgis.core import QgsProject
            import os
            path = QgsProject.instance().fileName()
            if path:
                from aery_plugin.graph_engine import record_prompt
                record_prompt(os.path.dirname(path), text, [], [])
        except Exception as e:
            print(f"Aery: record prompt error: {e}")

        # Wire per-turn callbacks so ChatPanel receives QThread worker signals
        self.agent._on_worker_finished = self._on_agent_response
        self.agent._on_worker_error   = self._on_agent_error

        self.agent.start(text)

    @pyqtSlot(dict)
    def _on_agent_event(self, event: dict) -> None:
        event_type = event.get("type", "")

        if event_type == "user":
            pass

        elif event_type == "tool_use_summary":
            summary = event.get("summary", "")
            if summary and self._transcript.active_tool_block:
                self._transcript.active_tool_block.update_status("done", details=summary)
                self._transcript.active_tool_block = None

        elif event_type == "system":
            subtype = event.get("subtype", "")
            if subtype == "api_retry":
                attempt = event.get("attempt", 1)
                max_retries = event.get("max_retries", 3)
                self._activity.label.setText(f"Retrying ({attempt}/{max_retries})...")
            elif subtype == "post_turn_summary":
                status = event.get("status_category", "")
                if self._streamlined_mode:
                    title = event.get("title", "")
                    if title and status == "completed":
                        self._activity.label.setText(title)
                elif status == "requires_action":
                    self._set_session_state(SessionState.REQUIRES_ACTION)

        elif event_type == "thinking":
            self._set_session_state(SessionState.RUNNING)
            self._set_activity("thinking...", active=True)

        elif event_type == "tool_error":
            tool = event.get("tool", "")
            error = event.get("error", "")
            self._transcript.add_bubble("ERROR", f"{tool}: {error}", "error")

        elif event_type == "tool_start":
            tool = event.get("tool", "")
            if tool:
                label = self._activity.activity_for_tool(tool) or tool.replace("_", " ")
                self._set_activity(label, active=True)

        elif event_type == "rate_limit_event":
            rate_info = event.get("rate_limit_info", {})
            utilization = rate_info.get("utilization", 0)
            if utilization > 0.8:
                status = rate_info.get("status", "allowed")
                if status == "allowed_warning":
                    self._transcript.add_bubble("SYSTEM", f"Rate limit warning: {int(utilization * 100)}% utilized", "system")

        elif event_type == "stream_event":
            inner_event = event.get("event", {})
            inner_type = inner_event.get("type", "")

            if inner_type == "status":
                status = inner_event.get("status", "")
                if status == "thinking":
                    self._set_activity("thinking...", active=True)

            elif inner_type == "text":
                text = inner_event.get("text", "")
                if text:
                    if self._transcript.streaming_bubble is None:
                        self._transcript._streaming_text = ""
                        bubble = MessageBubble("AERY", "", "assistant")
                        self._transcript.feed_layout.insertWidget(self._transcript.feed_layout.count() - 1, bubble)
                        self._transcript._streaming_bubble = bubble
                    self._transcript._streaming_text += text
                    self._transcript.streaming_bubble.update_text(self._transcript._streaming_text)
                    QTimer.singleShot(10, self._transcript.scroll_to_bottom)

            elif inner_type == "tool_error":
                tool = inner_event.get("tool", "")
                error = inner_event.get("error", "")
                if self._transcript.active_tool_block:
                    self._transcript.active_tool_block.update_status("error", details=error)
                    self._transcript.active_tool_block = None
                else:
                    self._transcript.add_bubble("ERROR", f"{tool}: {error}", "error")
                self._transcript.pending_tool_code = ""

            elif inner_type == "tool_done":
                tool = inner_event.get("tool", "")
                result = inner_event.get("result", "")
                if self._streamlined_mode:
                    summary = self._agent.tools._summarize_tool_result(tool, result) if hasattr(self._agent.tools, "_summarize_tool_result") else f"{tool} completed"
                    if self._transcript.active_tool_block:
                        self._transcript.active_tool_block.update_status("done", details=summary)
                        self._transcript.active_tool_block = None
                else:
                    if self._transcript.active_tool_block:
                        self._transcript.active_tool_block.update_status("done", details=result[:500])
                        self._transcript.active_tool_block = None
                self._transcript.pending_tool_code = ""

        elif event_type == "tool_progress":
            tool = event.get("tool_name", "")
            self._set_activity(self._activity.activity_for_tool(tool), active=True, detail=tool)
            self._finalize_streaming_bubble()
            block = self._transcript.add_tool_block(tool, "running", code=self._transcript.pending_tool_code)
            self._transcript.active_tool_block = block

        elif event_type == "assistant":
            self._got_assistant_event = True
            message = event.get("message", {})
            content = message.get("content", "")
            thinking_text = ""
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "thinking":
                            thinking_text = block.get("thinking", "")
                content = "\n".join(text_parts)
            display_text = thinking_text + "\n\n" + content if thinking_text else content
            if display_text:
                if self._transcript.streaming_bubble is not None:
                    self._transcript.streaming_bubble.update_text(display_text)
                    self._finalize_streaming_bubble()
                else:
                    self._transcript.add_bubble("AERY", display_text, "assistant")

        elif event_type == "permission_request":
            self._handle_permission_request(event)

        elif event_type == "streamlined_text":
            text = event.get("text", "")
            if text and self._streamlined_mode:
                self._transcript.add_bubble("AERY", text, "assistant")

        elif event_type == "streamlined_tool_use_summary":
            summary = event.get("summary", "")
            if summary and self._streamlined_mode:
                self._transcript.add_bubble("SYSTEM", summary, "system")

        elif event_type == "result":
            subtype = event.get("subtype", "")
            if subtype == "success":
                self._set_activity("ready", active=False)
                self._allow_next_assistant_stream = False
            elif subtype.startswith("error"):
                errors = event.get("errors", [])
                if errors:
                    self._transcript.add_bubble("ERROR", errors[0], "error")
                self._set_activity("ready", active=False)
                self._allow_next_assistant_stream = False

    @pyqtSlot(str)
    def _on_agent_response(self, response: str) -> None:
        if self._transcript.streaming_bubble is not None:
            self._finalize_streaming_bubble()
        elif response and not self._got_assistant_event:
            self._transcript.add_bubble("AERY", response, "assistant")
        self._set_activity("ready", active=False)
        self._set_session_state(SessionState.IDLE)
        self._allow_next_assistant_stream = False
        self._got_assistant_event = False

    @pyqtSlot(str)
    def _on_agent_error(self, error: str) -> None:
        if "429" in error and "RESOURCE_EXHAUSTED" in error:
            msg = ("Rate limit exceeded. The free tier has a daily quota. "
                   "Wait a few minutes and try again, or switch to a different provider.")
        elif "401" in error and "UNAUTHENTICATED" in error:
            msg = "Authentication expired. Please re-login via Settings."
        elif "VALIDATION_REQUIRED" in error:
            msg = ("Google requires account verification to use Cloud Code Assist.\n"
                   "Open this link in your browser to verify:\n"
                   "https://developers.google.com/gemini-code-assist/auth/auth_success_gemini\n\n"
                   "After verification, try your message again.")
        elif "403" in error and "PERMISSION_DENIED" in error:
            msg = "Permission denied. Check your account has access to this service."
        elif "404" in error and "NOT_FOUND" in error:
            msg = "Model not found. Try switching to a different model via /model."
        else:
            msg = error
        self._transcript.add_bubble("ERROR", msg, "error")
        self._set_activity("ready", active=False)
        self._set_session_state(SessionState.IDLE)
        self._allow_next_assistant_stream = False
        self._got_assistant_event = False

    def _finalize_streaming_bubble(self) -> None:
        self._transcript.finalize_streaming()

    def _build_graph_context(self, prompt: str = "") -> str:
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
        self._got_assistant_event = False
        self._cancel_streaming()
        self._set_session_state(SessionState.IDLE)
        self._transcript.add_bubble("SYSTEM", "Operation aborted.", "system")

    def _cancel_streaming(self) -> None:
        self._stream_label.clear()
        self._stream_label.setVisible(False)
        self._set_activity("ready", active=False)

    def _end_streaming(self) -> None:
        final_text = self._stream_label.toPlainText()
        self._stream_label.clear()
        self._stream_label.setVisible(False)
        if final_text.strip():
            self._transcript.add_bubble("AERY", final_text, "assistant")
        if self._local_prompt_queue and not self._discard_stale_events:
            next_text = self._local_prompt_queue.pop(0)
            qlen = len(self._local_prompt_queue)
            self._set_activity(str(qlen) + " queued" if qlen else "thinking...", active=True)
            self._dispatch_prompt(next_text)
        else:
            self._set_activity("ready", active=False)

    def _sync_dock_button(self) -> None:
        self._dock_btn.setText("\u21f2" if self.isFloating() else "\u21f1")

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
                color:{TEXT_MAIN};
            }}
            QMenu::item {{
                padding:6px 14px;
                border-radius:2px;
                font-size:11px;
            }}
            QMenu::item:selected {{
                background:{BG_HIGH};
                color:{ACCENT};
            }}
            QMenu::separator {{
                height:1px;
                background:{BORDER};
                margin:4px 10px;
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
        try:
            self.agent.reinitialize()
            self._refresh_provider_label()
        except Exception as e:
            print(f"Aery: agent reinitialize error: {e}")

    def _show_model_switcher(self) -> None:
        try:
            from aery_plugin.provider_settings import ModelSwitcherDialog
            dlg = ModelSwitcherDialog(self)
            self._dialogs.append(dlg)
            dlg.exec()
            self._dialogs.remove(dlg)
            self._refresh_provider_label()
            try:
                self.agent.reinitialize()
            except Exception as _e:
                pass
        except Exception as e:
            self._transcript.add_bubble("ERROR", f"Model switcher: {e}", "error")

    def _show_scopes_dialog(self) -> None:
        try:
            from aery_plugin.provider_settings import ScopesDialog
            dlg = ScopesDialog(self)
            self._dialogs.append(dlg)
            dlg.exec()
            self._dialogs.remove(dlg)
            self._refresh_provider_label()
            try:
                self.agent.reinitialize()
            except Exception as _e:
                pass
        except Exception as e:
            self._transcript.add_bubble("ERROR", f"Scopes dialog: {e}", "error")

    def _autosize_input(self) -> None:
        doc_height = int(self._input_area.input.document().size().height()) + 16
        input_height = max(46, min(140, doc_height))
        self._input_area.input.setFixedHeight(input_height)
        self._input_area.setFixedHeight(max(66, input_height + 20))

    def _on_clear_clicked(self) -> None:
        self._transcript.clear()
        try:
            self.agent.reset()
        except Exception as e:
            print(f"Aery: agent reset error: {e}")
        self._set_activity("ready", active=False)

    def _clear_feed(self) -> None:
        """Remove all transcripts from feed layout (backward-compat alias for TranscriptView.clear)."""
        self._transcript.clear()

    def _add_bubble(self, sender: str, text: str, msg_type: str = "assistant") -> None:
        self._transcript.add_bubble(sender, text, msg_type)

    def _add_canvas_image(self, b64_data: str) -> None:
        self._transcript.add_canvas_image(b64_data)

    def _record_tool_output(self, tool_name: str, output_path: str) -> None:
        try:
            from qgis.core import QgsProject
            path = QgsProject.instance().fileName()
            if path:
                _pd = os.path.dirname(path)
                from aery_plugin.graph_engine import record_code_execution
                record_code_execution(_pd, tool_name, "", "", [], [output_path], True)
                try:
                    from qgis.core import QgsVectorLayer
                    from aery_plugin.graph_engine import record_field
                    _vl = QgsVectorLayer(output_path, "_tmp", "ogr")
                    if _vl.isValid():
                        for _f in _vl.fields():
                            record_field(_pd, _vl.name(), _f.name(),
                                         _f.typeName(), tool_name)
                except Exception as e:
                    print(f"Aery: tool effect tracking: {e}")
        except Exception as e2:
            print(f"Aery: tool effects save: {e2}")

    def _handle_code_error(self, error_msg: str) -> None:
        if self._retry_count >= 2:
            self._retry_count = 0
            self._transcript.add_bubble("SYSTEM", "Auto-retry limit reached (2). Please review the error.", "system")
            return
        self._retry_count += 1
        self._cancel_streaming()
        retry_prompt = (
            f"The previous code execution failed with this error:\n{error_msg}\n\n"
            f"Fix the error and retry. Attempt {self._retry_count}/2."
        )
        self._transcript.add_bubble("SYSTEM", f"Auto-retrying ({self._retry_count}/2)\u2026", "system")

    def _add_tool_block(self, name: str, status: str, details: str = "") -> None:
        if status == "running":
            self._set_activity(self._activity.activity_for_tool(name), active=True)
        elif status == "error":
            self._set_activity("tool failed", active=True, detail=str(name))
        else:
            self._set_activity("thinking...", active=True)

    def _handle_permission_request(self, event: dict) -> None:
        tool_name = event.get("tool_name", "")
        description = event.get("description", "")
        risk_level = event.get("risk_level", "medium")
        request_id = event.get("request_id", "")
        tool_use_id = event.get("tool_use_id", "")
        risk_color = {
            "high": ERROR_COLOR,
            "medium": WARNING_COLOR,
            "low": SUCCESS_COLOR,
        }.get(risk_level, TEXT_MUTED)
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Permission Request")
        dialog.setText(f"Tool: {tool_name}")
        dialog.setInformativeText(f"{description}\n\nRisk level: {risk_level.upper()}")
        dialog.setIcon(QMessageBox.Icon.Warning)
        allow_btn = dialog.addButton("Allow Once", QMessageBox.ButtonRole.AcceptRole)
        always_btn = dialog.addButton("Always Allow", QMessageBox.ButtonRole.ActionRole)
        deny_btn = dialog.addButton("Deny", QMessageBox.ButtonRole.RejectRole)
        dialog.setStyleSheet(f"""
            QMessageBox {{ background:{BG_SURFACE}; color:{TEXT_MAIN}; }}
            QLabel {{ color:{TEXT_DIM}; font-family:{FONT_SANS}; font-size:12px; }}
            QPushButton {{ background:{BG_HIGH}; color:{TEXT_DIM}; border:1px solid {BORDER};
                border-radius:4px; padding:6px 14px; font-size:11px; }}
            QPushButton:hover {{ color:{ACCENT}; border-color:{ACCENT}; }}
        """)
        result = dialog.exec()
        if result == QMessageBox.ButtonRole.AcceptRole or dialog.clickedButton() == allow_btn:
            self._permission_granted(request_id, tool_use_id, False)
        elif dialog.clickedButton() == always_btn:
            self.agent.tools.set_permission_mode("bypassPermissions")
            self._permission_granted(request_id, tool_use_id, True)
        else:
            self._permission_denied(request_id, tool_use_id)

    def _permission_granted(self, request_id: str, tool_use_id: str, always: bool) -> None:
        self._transcript.add_bubble("SYSTEM", f"Permission granted for tool execution", "system")
        self.agent.resolve_permission(approved=True, always=always)

    def _permission_denied(self, request_id: str, tool_use_id: str) -> None:
        self._transcript.add_bubble("SYSTEM", f"Permission denied \u2014 tool execution skipped", "system")
        self.agent.resolve_permission(approved=False)

    def show_error(self, message: str) -> None:
        self._transcript.add_bubble("ERROR", message, "error")

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
                    self._stream_label.setHtml(format_text_html(initial))
                self._set_activity("thinking...", active=True)
        elif etype == "message_update":
            if self._discard_stale_events:
                return
            if self._active_stream_role != "assistant" and self._event_role(event) != "assistant":
                return
            text = self._extract_text(event)
            if text and not self._is_background_noise(text):
                self._stream_label.setVisible(True)
                self._stream_label.setHtml(format_text_html(text))
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
                    self._stream_label.setHtml(format_text_html(final_text))
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
                if "run_qgis" in str(name).lower() or "qgis_code" in str(name).lower():
                    self._handle_code_error(str(err))
            else:
                detail = result if isinstance(result, str) else json.dumps(result, indent=2) if result else ""
                if isinstance(result, str) and result.startswith("iVBORw0KGgo") and len(result) > 100:
                    self._add_canvas_image(result)
                else:
                    self._add_tool_block(str(name), "done", detail[:500] if detail else "")
                if isinstance(result, dict) and "OUTPUT" in result:
                    self._record_tool_output(str(name), result["OUTPUT"])
        elif etype == "agent_end":
            if self._discard_stale_events:
                return
            self._active_stream_role = ""
            self._end_streaming()
        elif etype == "question":
            self._handle_question(event)

    def _extract_text(self, event: dict) -> str:
        message = event.get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(parts)
        return str(content) if content else ""

    def _event_role(self, event: dict) -> str:
        message = event.get("message", {})
        return message.get("role", "")

    def _is_background_noise(self, text: str) -> bool:
        return len(text.strip()) < 2

    def _handle_question(self, event: dict) -> None:
        quest_id = event.get("questId", "")
        if not quest_id:
            return
        card = _QuestionWidget(event, self._transcript.feed_container, self)
        card._resolve_callback = self._resolve_question
        self._transcript.feed_layout.insertWidget(self._transcript.feed_layout.count() - 1, card)
        QTimer.singleShot(50, self._transcript.scroll_to_bottom)

    def _resolve_question(self, quest_id: str, answer: dict) -> None:
        try:
            from aery_plugin.qgis_executor import _pending_questions, _resolve_question
            _resolve_question(quest_id, answer)
        except Exception as e:
            print(f"Aery: resolve question error: {e}")

    def append_message(self, sender: str, text: str, msg_type: str = "assistant") -> None:
        self._transcript.add_bubble(sender, text, msg_type)

    def set_ready(self) -> None:
        if not self._ready:
            self._ready = True
            self._set_activity("ready", active=False)
            _refresh_layer_cache()
            self._load_session()

    def on_project_changed(self) -> None:
        self._session_context_injected = False
        self._refresh_provider_label()
        _refresh_layer_cache()
        try:
            from qgis.core import QgsProject
            path = QgsProject.instance().fileName()
            if path:
                self._transcript.add_bubble("SYSTEM", f"Project changed: {path}", "system")
                self._save_session()
                self._transcript.set_session_messages([])
                self._load_session()
        except (RuntimeError, AttributeError) as e:
            print(f"Aery: on_project_changed warning: {e}")

    def notify_layer_added(self, name: str, layer_type: str) -> None:
        _refresh_layer_cache()
        self._transcript.add_bubble("SYSTEM", f"Layer added: {name} [{layer_type}]", "system")
        try:
            from qgis.core import QgsProject
            from aery_plugin.graph_engine import record_layer, auto_detect_spatial_relationships
            path = QgsProject.instance().fileName() or ""
            lyr = next((l for l in QgsProject.instance().mapLayers().values()
                        if l.name() == name), None)
            if lyr:
                record_layer(os.path.dirname(path) if path else ".", name, layer_type,
                             lyr.crs().authid() if lyr.crs() else "", lyr.source())
                auto_detect_spatial_relationships(os.path.dirname(path) if path else ".")
        except (RuntimeError, AttributeError) as e:
            print(f"Aery: notify_layer_added warning for '{name}': {e}")

    def notify_layers_removed(self, count: int) -> None:
        _refresh_layer_cache()
        self._transcript.add_bubble("SYSTEM", f"{count} layer(s) removed from project.", "system")

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
        except Exception as e:
            print(f"Aery: session path error: {e}")
        return ""

    def _save_session(self) -> None:
        path = self._session_path()
        if not path:
            return
        try:
            import json
            with open(path, "w") as f:
                json.dump(self._transcript.get_session_messages()[-200:], f, indent=2)
        except Exception as e:
            print(f"Aery: session save error: {e}")

    def _load_session(self, show_resume_msg: bool = False) -> None:
        path = self._session_path()
        if not path or not os.path.exists(path):
            return
        try:
            import json
            with open(path) as f:
                msgs = json.load(f)
            if msgs:
                self._transcript.set_session_messages(msgs)
                if show_resume_msg:
                    self._transcript.add_bubble("SYSTEM", f"Resumed session ({len(msgs)} messages)", "system")
        except Exception as e:
            print(f"Aery: session load error: {e}")

    def _export_html_report(self) -> None:
        try:
            from qgis.core import QgsProject
            import os, json
            path = QgsProject.instance().fileName()
            if not path:
                self._transcript.add_bubble("SYSTEM", "Save your project first before exporting a report.", "system")
                return
            report_dir = os.path.join(os.path.dirname(path), ".aery")
            os.makedirs(report_dir, exist_ok=True)
            report_path = os.path.join(report_dir, "report.html")
            rows = ""
            for msg in self._transcript.get_session_messages():
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
            self._transcript.add_bubble("SYSTEM", f"Report exported: {report_path}", "system")
        except Exception as e:
            self._transcript.add_bubble("ERROR", f"Export failed: {e}", "error")

    def _show_session_summary(self) -> None:
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

            lines = [f"SESSION SUMMARY \u2014 {len(prompts)} prompts", ""]
            if prompts:
                lines.append("WHAT YOU ASKED:")
                for p in prompts[-10:]:
                    lines.append(f"  \u2022 {p['label']}")
            if tools_used:
                lines += ["", "TOOLS USED:"]
                for t in sorted(tools_used):
                    lines.append(f"  \u2022 {t}")
            if outputs:
                lines += ["", "FILES PRODUCED:"]
                for o in outputs[:15]:
                    lines.append(f"  \u2022 {o['label']}  {o.get('path','')}")

            from aery_plugin.graph_engine import EDGE_OVERLAPS, EDGE_CONTAINS
            spatial = [e for e in g._edges if e["rel"] in (EDGE_OVERLAPS, EDGE_CONTAINS)]
            if spatial:
                lines += ["", "SPATIAL RELATIONSHIPS FOUND:"]
                for e in spatial[:8]:
                    src = g._nodes.get(e["src"], {}).get("label", e["src"])
                    dst = g._nodes.get(e["dst"], {}).get("label", e["dst"])
                    lines.append(f"  \u2022 {src} {e['rel']} {dst} (confidence {e.get('weight', 0):.0%})")

            self._show_dialog("What Did You Do?", "\n".join(lines))
        except Exception as e:
            self._show_dialog("Session Summary", f"Error: {e}")

    def _show_graph_window(self) -> None:
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
        self._show_audit_dialog("Audit Trail", body, audit_path)

    def _show_audit_dialog(self, title: str, body: str, audit_path: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(520, 380)
        dialog.setStyleSheet(f"""
            QDialog {{ background:{BG_SURFACE}; color:{TEXT_MAIN}; }}
            QLabel {{ color:{TEXT_DIM}; font-family:{FONT_SANS}; }}
            QTextEdit {{
                background:{BG_BASE}; color:{TEXT_MAIN}; border:1px solid {BORDER};
                border-radius:6px; font-family:{FONT_MONO}; font-size:11px;
            }}
        """)
        layout = QVBoxLayout(dialog)
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
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        clear_btn = QPushButton("CLEAR")
        _style_button(clear_btn)
        clear_btn.clicked.connect(lambda: self._clear_audit(dialog, text, audit_path))
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        close_btn = QPushButton("CLOSE")
        _style_button(close_btn, active=True)
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        self._dialogs.append(dialog)
        dialog.show()

    def _clear_audit(self, dialog, text_widget, audit_path: str) -> None:
        try:
            if os.path.exists(audit_path):
                confirm = QMessageBox.question(
                    dialog,
                    "Clear Audit Trail",
                    f"Delete all audit logs?\n\n{audit_path}\n\nThis cannot be undone.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if confirm != QMessageBox.StandardButton.Yes:
                    return
                os.remove(audit_path)
            text_widget.setPlainText("Audit trail cleared.")
        except Exception as e:
            text_widget.setPlainText(f"Failed to clear: {e}")

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
        dlg = ToolRegistryDialog(parent=self)
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
