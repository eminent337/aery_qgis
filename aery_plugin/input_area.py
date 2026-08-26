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

class AttachmentChip(QFrame):
    """Individual file/image chip badge with icon, filename, and remove (x) button."""

    def __init__(self, file_path: str, on_remove, parent: Optional[QWidget] = None):
        super().__init__(parent)
        import os
        self.file_path = file_path
        self._on_remove = on_remove
        self.setFixedHeight(28)
        self.setStyleSheet(f"""
            QFrame {{
                background:{BG_HIGH}; border:1px solid {BORDER}; border-radius:14px;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 6, 2)
        layout.setSpacing(6)

        # Determine icon based on file extension
        _, ext = os.path.splitext(file_path.lower())
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            icon_txt = "\U0001f5bc"  # image frame
        elif ext in {".tif", ".tiff", ".img", ".dem"}:
            icon_txt = "\U0001f30d"  # globe/raster
        elif ext in {".gpkg", ".shp", ".geojson", ".kml"}:
            icon_txt = "\u2b21"      # polygon/vector
        else:
            icon_txt = "\U0001f4c4"  # document

        from PyQt6.QtWidgets import QLabel
        icon_lbl = QLabel(icon_txt, self)
        icon_lbl.setStyleSheet(f"color:{ACCENT}; font-size:12px; border:none; background:transparent;")
        layout.addWidget(icon_lbl)

        name = os.path.basename(file_path)
        if len(name) > 20:
            name = name[:10] + "..." + name[-7:]
        name_lbl = QLabel(name, self)
        name_lbl.setToolTip(file_path)
        name_lbl.setStyleSheet(f"color:{TEXT_MAIN}; font-family:{FONT_SANS}; font-size:12px; font-weight:500; border:none; background:transparent;")
        layout.addWidget(name_lbl)

        del_btn = QPushButton("\u00d7", self)
        del_btn.setFixedSize(16, 16)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setToolTip("Remove attachment")
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:none; border-radius:8px;
                color:{TEXT_MUTED}; font-size:13px; font-weight:bold; padding:0px;
            }}
            QPushButton:hover {{ background:{BORDER}; color:{ERROR_COLOR}; }}
        """)
        del_btn.clicked.connect(lambda: self._on_remove(self.file_path))
        layout.addWidget(del_btn)


class InputArea(QFrame):
    """Modern input bar with prompt editor, embedded attachment button, attachment chip preview, and send button."""

    def __init__(self, on_send, on_abort, on_attach=None, on_file_dropped=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QVBoxLayout
        self._attachments: list[str] = []
        self.setStyleSheet(f"background:{BG_SURFACE};border-top:1px solid {BORDER};")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 6, 10, 8)
        main_layout.setSpacing(4)

        # Top: Horizontal layout for active attachment chips
        self._chips_container = QWidget(self)
        self._chips_layout = QHBoxLayout(self._chips_container)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(6)
        self._chips_layout.addStretch(1)
        self._chips_container.setVisible(False)
        main_layout.addWidget(self._chips_container)

        # Bottom: Input Row
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        # Prompt container frame with unified border
        self._box_frame = QFrame(self)
        self._box_frame.setFixedHeight(46)
        self._box_frame.setStyleSheet(f"""
            QFrame {{
                background:{BG_BASE}; border:1px solid {BORDER}; border-radius:6px;
            }}
            QFrame:focus-within {{ border-color:{ACCENT}; }}
        """)
        box_layout = QHBoxLayout(self._box_frame)
        box_layout.setContentsMargins(6, 4, 8, 4)
        box_layout.setSpacing(6)

        # Embedded paperclip button inside the prompt box
        self._attach_btn = QPushButton("\U0001f4ce", self._box_frame)
        self._attach_btn.setFixedSize(26, 26)
        self._attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attach_btn.setToolTip("Attach files or images (GeoPackage, Shapefile, GeoTIFF, PNG, etc.)")
        self._attach_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:none; border-radius:13px;
                color:{TEXT_MUTED}; font-size:14px; padding:0px;
            }}
            QPushButton:hover {{ background:{BG_HIGH}; color:{ACCENT}; }}
        """)
        if on_attach:
            self._attach_btn.clicked.connect(on_attach)
        box_layout.addWidget(self._attach_btn)

        self._input = PromptInput(on_send, on_abort, file_dropped_callback=on_file_dropped, parent=self._box_frame)
        self._input.setPlaceholderText("Message Aery or drag & drop files...")
        self._input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._input.setStyleSheet(f"""
            QTextEdit {{
                background:transparent; border:none;
                color:{TEXT_MAIN}; padding:2px 4px; font-family:{FONT_SANS}; font-size:14px;
                selection-background-color:{ACCENT}; selection-color:{BG_BASE};
            }}
        """)
        box_layout.addWidget(self._input, stretch=1)
        row_layout.addWidget(self._box_frame, stretch=1)

        self._send_btn = QPushButton("\u27a4", self)
        self._send_btn.setFixedSize(34, 34)
        self._send_btn.clicked.connect(on_send)
        self._update_button(streaming=False, has_text=False)
        row_layout.addWidget(self._send_btn)
        main_layout.addLayout(row_layout)

    def add_attachment(self, file_path: str) -> None:
        """Add a file to the active attachment chips row."""
        if file_path in self._attachments:
            return
        self._attachments.append(file_path)
        self._refresh_chips()

    def remove_attachment(self, file_path: str) -> None:
        """Remove a file from the active attachment chips row."""
        if file_path in self._attachments:
            self._attachments.remove(file_path)
            self._refresh_chips()

    def clear_attachments(self) -> None:
        """Clear all active attachment chips."""
        self._attachments.clear()
        self._refresh_chips()

    def get_attachments(self) -> list[str]:
        """Return the list of currently attached file paths."""
        return list(self._attachments)

    def _refresh_chips(self) -> None:
        # Clear existing chip widgets
        while self._chips_layout.count() > 1:
            item = self._chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._attachments:
            self._chips_container.setVisible(False)
            self.setFixedHeight(66)
        else:
            for path in self._attachments:
                chip = AttachmentChip(path, on_remove=self.remove_attachment, parent=self._chips_container)
                self._chips_layout.insertWidget(self._chips_layout.count() - 1, chip)
            self._chips_container.setVisible(True)
            self.setFixedHeight(102)

    @property
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
            has_content = has_text or bool(self._attachments)
            bg = ACCENT if has_content else BG_HIGH
            fg = BG_BASE if has_content else TEXT_MUTED
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