"""Dialog widgets extracted from chat_panel.py.

- `InfoDialog`            — generic read-only info window
- `_QuestionWidget`       — inline question card with optional fields
"""
import os
from typing import Any, Optional

from PyQt6.QtCore import Qt, QTimer, Q_ARG, QMetaObject
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from aery_plugin.ui_constants import (
    ACCENT, BG_BASE, BG_HIGH, BG_SURFACE, BORDER, ERROR_COLOR, FONT_MONO, FONT_SANS,
    SUCCESS_COLOR, TEXT_DIM, TEXT_MAIN, TEXT_MUTED, WARNING_COLOR,
)


# ── Helpers ────────────────────────────────────────────────────────────────────
def _style_button(btn: QPushButton, active: bool = False, danger: bool = False) -> None:
    fg   = ERROR_COLOR if danger else (BG_BASE if active else TEXT_DIM)
    bg   = ACCENT   if active else "transparent"
    bor  = ACCENT   if active else BORDER
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(f"""
        QPushButton {{
            background:{bg}; border:1px solid {bor}; border-radius:4px;
            color:{fg}; font-family:{FONT_MONO}; font-size:9px; font-weight:700;
            padding:5px 6px;
        }}
        QPushButton:hover {{
            background:{BG_HIGH};
            color:{ERROR_COLOR if danger else ACCENT};
            border-color:{ERROR_COLOR if danger else ACCENT};
        }}
    """)


class InfoDialog(QDialog):
    """Generic read-only information window."""
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
            f"color:{ACCENT}; font-family:{FONT_MONO}; font-size:12px;"
            "font-weight:900; letter-spacing:0.12em; background:transparent;"
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
            f"QFrame {{ background:{self.SURFACE}; border:1px solid {self.BORDER}; "
            f"border-radius:6px; padding:0; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(6)

        hdr = QLabel(self._header)
        hdr.setStyleSheet(f"color:{self.ACCENT}; font-weight:700; font-size:13px; background:transparent;")
        root.addWidget(hdr)
        if self._body:
            body_lbl = QLabel(self._body)
            body_lbl.setWordWrap(True)
            body_lbl.setStyleSheet(f"color:{self.TEXT_DIM}; font-size:12px; background:transparent;")
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
            dot.setStyleSheet(f"color:{self.TEXT_MUTED}; font-size:12px; background:transparent;")
            row.addWidget(dot)
            opt_label = QLabel(option.get("label", f"Option {idx + 1}"))
            opt_label.setWordWrap(True)
            opt_label.setStyleSheet(
                f"color:{self.TEXT_MAIN}; font-weight:600; font-size:12px; background:transparent;"
            )
            row.addWidget(opt_label, 1)
            opt_desc = option.get("description", "")
            if opt_desc:
                d_lbl = QLabel(opt_desc)
                d_lbl.setWordWrap(True)
                d_lbl.setStyleSheet(f"color:{self.TEXT_MUTED}; font-size:11px; background:transparent;")
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
                lbl.setStyleSheet(f"color:{self.TEXT_MUTED}; font-size:10px; background:transparent;")
                lbl.setFixedWidth(120)
                fl.addWidget(lbl)
                inp = QLineEdit()
                inp.setPlaceholderText(f_def.get("placeholder", ""))
                inp.setStyleSheet(
                    f"QLineEdit {{ background:{self.BG}; color:{self.TEXT_MAIN}; "
                    f"border:1px solid {self.BORDER}; border-radius:3px; "
                    f"padding:4px 6px; font-size:11px; }}"
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
            arrow.setStyleSheet(f"color:{self.TEXT_MUTED}; font-size:9px; background:transparent;")
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
                    a = _pr[0].findChild(QLabel, f"arrow_{_pr[0].objectName().replace('qopt_','')}")
                    if a:
                        a.setText("\u25b6")
                        a.setStyleSheet(f"color:{self.TEXT_MUTED}; font-size:9px; background:transparent;")
                self._selected = _idx
                prev_ref[0] = _f
                _f.setStyleSheet(
                    f"QFrame {{ background:{'#1E2936'}; border:1px solid {self.ACCENT}; border-radius:4px; }}"
                )
                a = _f.findChild(QLabel, f"arrow_{_idx}")
                if a:
                    a.setText("\u25bc")
                    a.setStyleSheet(f"color:{self.ACCENT}; font-size:9px; background:transparent;")
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
        option = self._options[idx]
        fields  = dict(self._field_states[idx])
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
        except Exception:
            pass

    def _update_submit(self) -> None:
        self._submit_btn.setEnabled(self._selected >= 0)

    def _resolve_answer(self, quest_id: str, answer: dict) -> None:
        try:
            from aery_plugin.qgis_executor import _resolve_question as _rq
            _rq(quest_id, answer)
        except Exception:
            pass
        try:
            self.setParent(None)
            self.deleteLater()
        except Exception:
            pass

class _PermissionWidget(QFrame):
    """Inline, non-modal permission card embedded in the chat feed.

    Mirrors _QuestionWidget: the card is inserted into the transcript and the
    user resolves it with Allow Once / Always Allow / Deny without freezing
    the QGIS UI. Multiple queued permission requests each get their own card,
    so the agent can dispatch several tools in one turn and the user resolves
    them independently (GeoLibre AssistantPanel codeQueueRef pattern).
    """

    BG        = "#0D0E15"
    SURFACE   = "#12131A"
    ACCENT    = "#57F1DB"
    BORDER    = "#3C4A46"
    TEXT_MAIN = "#E3E1EC"
    TEXT_DIM  = "#BACAC5"
    TEXT_MUTED = "#859490"
    WARN      = "#FFD1AA"
    DANGER    = "#FF7B72"

    def __init__(
        self,
        event: dict,
        parent: Optional[QWidget] = None,
        feed_container: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._event = event
        self._request_id = event.get("request_id", "")
        self._tool_name = event.get("tool_name", "")
        self._description = event.get("description", "")
        self._risk_level = event.get("risk_level", "medium")
        self._tool_use_id = event.get("tool_use_id", "")
        self._feed = feed_container
        self._resolve_callback = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background:{self.SURFACE}; border:1px solid {self.BORDER}; "
            f"border-radius:6px; padding:0; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(6)

        hdr = QLabel("Permission Request")
        hdr.setStyleSheet(
            f"color:{self.WARN}; font-weight:700; font-size:13px; background:transparent;"
        )
        root.addWidget(hdr)

        tool_lbl = QLabel(f"Tool: {self._tool_name}")
        tool_lbl.setStyleSheet(
            f"color:{self.ACCENT}; font-weight:600; font-size:12px; background:transparent;"
        )
        root.addWidget(tool_lbl)

        if self._description:
            desc_lbl = QLabel(self._description)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet(
                f"color:{self.TEXT_DIM}; font-size:12px; background:transparent;"
            )
            root.addWidget(desc_lbl)

        risk_color = {
            "high": self.DANGER,
            "medium": self.WARN,
            "low": self.ACCENT,
        }.get(self._risk_level, self.TEXT_MUTED)
        risk_lbl = QLabel(f"Risk level: {self._risk_level.upper()}")
        risk_lbl.setStyleSheet(
            f"color:{risk_color}; font-size:10px; font-weight:600; background:transparent;"
        )
        root.addWidget(risk_lbl)

        btns = QHBoxLayout()
        btns.setSpacing(8)

        allow_btn = QPushButton("Allow Once")
        allow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        allow_btn.setStyleSheet(
            f"QPushButton {{ background:{self.ACCENT}; color:{self.BG}; border:none; "
            f"border-radius:4px; font-weight:700; font-size:11px; padding:6px 14px; }}"
            f"QPushButton:hover {{ background:#45D8C8; }}"
        )
        allow_btn.clicked.connect(lambda: self._resolve(True, False))
        btns.addWidget(allow_btn)

        always_btn = QPushButton("Always Allow")
        always_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        always_btn.setStyleSheet(
            f"QPushButton {{ background:{self.SURFACE}; color:{self.TEXT_MAIN}; "
            f"border:1px solid {self.ACCENT}; border-radius:4px; "
            f"font-weight:600; font-size:11px; padding:6px 14px; }}"
            f"QPushButton:hover {{ border-color:#45D8C8; color:#45D8C8; }}"
        )
        always_btn.clicked.connect(lambda: self._resolve(True, True))
        btns.addWidget(always_btn)

        deny_btn = QPushButton("Deny")
        deny_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        deny_btn.setStyleSheet(
            f"QPushButton {{ background:{self.SURFACE}; color:{self.DANGER}; "
            f"border:1px solid {self.DANGER}; border-radius:4px; "
            f"font-weight:600; font-size:11px; padding:6px 14px; }}"
            f"QPushButton:hover {{ background:{self.DANGER}; color:{self.BG}; }}"
        )
        deny_btn.clicked.connect(lambda: self._resolve(False, False))
        btns.addWidget(deny_btn)

        btns.addStretch(1)
        root.addLayout(btns)

    def _resolve(self, approved: bool, always: bool) -> None:
        if self._resolve_callback:
            self._resolve_callback(self._request_id, self._tool_use_id, approved, always)
        try:
            self.setParent(None)
            self.deleteLater()
        except Exception:
            pass
