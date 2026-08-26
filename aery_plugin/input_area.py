"""Input area widget for Aery chat panel."""

from typing import Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QKeyEvent, QTextOption
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QTextEdit, QWidget

from aery_plugin.ui_constants import (
    ACCENT, BG_BASE, BG_HIGH, BG_SURFACE, BORDER, ERROR_COLOR, TEXT_MAIN,
    TEXT_MUTED, FONT_SANS,
)


class PromptInput(QTextEdit):
    """Prompt editor with submit/newline/abort/history and drag-and-drop file behavior."""

    def __init__(self, submit_callback, abort_callback, file_dropped_callback=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._submit_callback = submit_callback
        self._abort_callback = abort_callback
        self._file_dropped_callback = file_dropped_callback
        self._history: list[str] = []
        self._history_idx = -1
        self._saved_draft = ""
        self.setAcceptDrops(True)

    def set_history(self, history: list[str]) -> None:
        self._history = history

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
            if paths and self._file_dropped_callback:
                self._file_dropped_callback(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

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

class InputArea(QFrame):
    """Input bar with prompt editor, attachment/browse button, and send/abort button."""

    def __init__(self, on_send, on_abort, on_attach=None, on_file_dropped=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(66)
        self.setStyleSheet(f"background:{BG_SURFACE};border-top:1px solid {BORDER};")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Attachment / Clip button
        self._attach_btn = QPushButton("\U0001f4ce")  # paperclip icon
        self._attach_btn.setFixedSize(34, 34)
        self._attach_btn.setToolTip("Attach or drop vector, raster, or image files (GeoPackage, Shapefile, GeoTIFF, PNG, etc.)")
        self._attach_btn.setStyleSheet(f"""
            QPushButton {{
                background:{BG_HIGH}; border:1px solid {BORDER}; border-radius:17px;
                color:{TEXT_MUTED}; font-size:14px;
            }}
            QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
        """)
        if on_attach:
            self._attach_btn.clicked.connect(on_attach)
        layout.addWidget(self._attach_btn)

        self._input = PromptInput(on_send, on_abort, file_dropped_callback=on_file_dropped)
        self._input.setFixedHeight(46)
        self._input.setMinimumHeight(46)
        self._input.setMaximumHeight(140)
        self._input.setPlaceholderText("Enter geospatial command or drag & drop files here...")
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

        self._send_btn = QPushButton("\u27a4")
        self._send_btn.setFixedSize(34, 34)
        self._send_btn.clicked.connect(on_send)
        self._update_button(streaming=False, has_text=False)
        layout.addWidget(self._send_btn)
    def input(self) -> PromptInput:
        return self._input

    @property
    def send_btn(self) -> QPushButton:
        return self._send_btn

    def update_button(self, streaming: bool, has_text: bool) -> None:
        self._update_button(streaming, has_text)

    def _update_button(self, streaming: bool, has_text: bool) -> None:
        if streaming:
            self._send_btn.setText("\u25a0")
            self._send_btn.setStyleSheet(f"""
                QPushButton {{
                    background:{BG_HIGH}; border:1px solid {ERROR_COLOR};
                    border-radius:17px; color:{ERROR_COLOR}; font-size:10px; font-weight:900;
                }}
                QPushButton:hover {{ background:{ERROR_COLOR}; color:{BG_BASE}; }}
            """)
        else:
            bg = ACCENT if has_text else BG_HIGH
            fg = BG_BASE if has_text else TEXT_MUTED
            self._send_btn.setText("\u27a4")
            self._send_btn.setStyleSheet(f"""
                QPushButton {{
                    background:{bg}; border:1px solid {BORDER}; border-radius:17px;
                    color:{fg}; font-size:12px; font-weight:900;
                }}
                QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
            """)

    def get_text(self) -> str:
        return self._input.toPlainText().strip()

    def clear(self) -> None:
        self._input.clear()

    def set_history(self, history: list[str]) -> None:
        self._input.set_history(history)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 66)