#!/usr/bin/env python3
"""Code Review UI - DiffWidget with side-by-side view for Aery QGIS Plugin."""

from __future__ import annotations

import difflib
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from aery_plugin.ui_constants import ACCENT, BG_BASE, BG_CARD, BORDER, FONT_MONO, TEXT, F_S, F_H


class DiffTextEdit(QTextEdit):
    """Text edit widget that supports line highlighting for diffs."""
    
    def __init__(self, parent=None, read_only=True):
        super().__init__(parent)
        self.setReadOnly(read_only)
        self.setFont(QFont(FONT_MONO, 9))
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {BG_BASE};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 8px;
                font-family: {FONT_MONO};
                font-size: 9pt;
            }}
        """)


class DiffWidget(QWidget):
    """Side-by-side diff viewer with inline highlighting."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # Header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        title = QLabel("CODE REVIEW")
        title.setStyleSheet(f"font-size:{_fs(F_S)}; font-weight:800; color:{ACCENT}; letter-spacing:0.1em;")
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        # Stats labels
        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(f"font-size:{_fs(F_S)}; color:{TEXT}; font-family:{FONT_MONO};")
        header_layout.addWidget(self._stats_label)
        
        layout.addLayout(header_layout)
        
        # Diff content area - side by side
        diff_layout = QHBoxLayout()
        diff_layout.setSpacing(4)
        
        # Left side (original/old)
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        
        left_header = QLabel("ORIGINAL")
        left_header.setStyleSheet(f"font-size:{_fs(F_S)}; font-weight:700; color:{ACCENT}; background:{BG_CARD}; border:1px solid {BORDER}; border-radius:3px; padding:4px;")
        left_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(left_header)
        
        self._left_text = DiffTextEdit()
        left_layout.addWidget(self._left_text, 1)
        
        # Right side (new/modified)
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        
        right_header = QLabel("MODIFIED")
        right_header.setStyleSheet(f"font-size:{_fs(F_S)}; font-weight:700; color:{ACCENT}; background:{BG_CARD}; border:1px solid {BORDER}; border-radius:3px; padding:4px;")
        right_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(right_header)
        
        self._right_text = DiffTextEdit()
        right_layout.addWidget(self._right_text, 1)
        
        diff_layout.addWidget(left_container, 1)
        diff_layout.addWidget(right_container, 1)
        
        layout.addLayout(diff_layout, 1)
        
        # Store diff data for navigation
        self._diff_lines = []
        self._current_diff_index = -1
    
    def set_diff(self, old_text: str, new_text: str):
        """Set the diff content from old and new text."""
        self._left_text.clear()
        self._right_text.clear()
        self._diff_lines = []
        
        if not old_text and not new_text:
            self._stats_label.setText("No changes")
            return
        
        old_lines = old_text.splitlines(keepends=True) if old_text else []
        new_lines = new_text.splitlines(keepends=True) if new_text else []
        
        # Use difflib to compute diff
        differ = difflib.SequenceMatcher(None, old_lines, new_lines)
        
        left_parts = []
        right_parts = []
        
        additions = 0
        deletions = 0
        modifications = 0
        
        for tag, i1, i2, j1, j2 in differ.get_opcodes():
            if tag == "equal":
                # Same lines - add to both sides with normal formatting
                for line in old_lines[i1:i2]:
                    left_parts.append(("normal", line))
                    right_parts.append(("normal", line))
            
            elif tag == "delete":
                # Lines only in old (deleted)
                for line in old_lines[i1:i2]:
                    left_parts.append(("delete", line))
                    right_parts.append(("empty", ""))
                deletions += i2 - i1
            
            elif tag == "insert":
                # Lines only in new (added)
                for line in new_lines[j1:j2]:
                    left_parts.append(("empty", ""))
                    right_parts.append(("insert", line))
                additions += j2 - j1
            
            elif tag == "replace":
                # Lines changed
                for line in old_lines[i1:i2]:
                    left_parts.append(("replace", line))
                for line in new_lines[j1:j2]:
                    right_parts.append(("replace", line))
                modifications += max(i2 - i1, j2 - j1)
        
        # Render to text edits with highlighting
        self._render_diff(left_parts, self._left_text)
        self._render_diff(right_parts, self._right_text)
        
        # Update stats
        stats_parts = []
        if additions:
            stats_parts.append(f"+{additions}")
        if deletions:
            stats_parts.append(f"-{deletions}")
        if modifications:
            stats_parts.append(f"~{modifications}")
        self._stats_label.setText(" ".join(stats_parts) if stats_parts else "No changes")
        
        # Store diff for navigation
        self._diff_lines = self._compute_diff_positions(left_parts, right_parts)
        self._current_diff_index = -1
    
    def _compute_diff_positions(self, left_parts: list, right_parts: list) -> list:
        """Compute positions of diffs for navigation."""
        positions = []
        left_line = 0
        right_line = 0
        
        for i, (left_type, left_line_text) in enumerate(left_parts):
            if left_type in ("delete", "replace"):
                positions.append({
                    "type": left_type,
                    "left_line": left_line,
                    "right_line": right_line if right_parts and right_line < len(right_parts) else -1,
                })
            left_line += 1 if left_type != "empty" else 0
            if left_type != "empty":
                right_line += 1
        
        for i, (right_type, right_line_text) in enumerate(right_parts):
            if right_type == "insert":
                positions.append({
                    "type": "insert",
                    "left_line": -1,
                    "right_line": right_line,
                })
            right_line += 1
        
        return positions
    
    def _render_diff(self, parts: list, text_edit: DiffTextEdit):
        """Render diff parts to a text edit with syntax highlighting."""
        cursor = text_edit.textCursor()
        
        # Define formats
        normal_format = cursor.charFormat()
        normal_format.setForeground(QColor(TEXT))
        
        delete_format = cursor.charFormat()
        delete_format.setForeground(QColor("#ff6b6b"))
        delete_format.setFontStrikeOut(True)
        
        insert_format = cursor.charFormat()
        insert_format.setForeground(QColor("#69db7c"))
        
        replace_format = cursor.charFormat()
        replace_format.setForeground(QColor("#ffa500"))
        
        empty_format = cursor.charFormat()
        empty_format.setForeground(QColor("#555555"))
        
        formats = {
            "normal": normal_format,
            "delete": delete_format,
            "insert": insert_format,
            "replace": replace_format,
            "empty": empty_format,
        }
        
        for part_type, line_text in parts:
            fmt = formats.get(part_type, normal_format)
            cursor.setCharFormat(fmt)
            cursor.insertText(line_text)
        
        # Scroll to top
        text_edit.moveCursor(QTextCursor.MoveOperation.Start)
    
    def next_diff(self):
        """Navigate to next diff."""
        if not self._diff_lines:
            return
        self._current_diff_index = (self._current_diff_index + 1) % len(self._diff_lines)
        self._scroll_to_diff(self._current_diff_index)
    
    def prev_diff(self):
        """Navigate to previous diff."""
        if not self._diff_lines:
            return
        self._current_diff_index = (self._current_diff_index - 1) % len(self._diff_lines)
        self._scroll_to_diff(self._current_diff_index)
    
    def _scroll_to_diff(self, index: int):
        """Scroll both panes to show the diff at index."""
        if index >= len(self._diff_lines):
            return
        
        diff = self._diff_lines[index]
        
        # Scroll left pane
        if diff["left_line"] >= 0:
            cursor = self._left_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            for _ in range(diff["left_line"]):
                cursor.movePosition(QTextCursor.MoveOperation.Down)
            self._left_text.setTextCursor(cursor)
            self._left_text.ensureCursorVisible()
        
        # Scroll right pane
        if diff["right_line"] >= 0:
            cursor = self._right_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            for _ in range(diff["right_line"]):
                cursor.movePosition(QTextCursor.MoveOperation.Down)
            self._right_text.setTextCursor(cursor)
            self._right_text.ensureCursorVisible()


class InlineDiffWidget(QWidget):
    """Inline diff widget for showing changes in a single pane (unified diff)."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # Header
        header = QLabel("UNIFIED DIFF")
        header.setStyleSheet(f"font-size:{_fs(F_S)}; font-weight:800; color:{ACCENT}; letter-spacing:0.1em;")
        layout.addWidget(header)
        
        # Diff text
        self._text = DiffTextEdit()
        layout.addWidget(self._text, 1)
    
    def set_diff(self, old_text: str, new_text: str, context_lines: int = 3):
        """Set unified diff."""
        self._text.clear()
        
        if not old_text and not new_text:
            self._text.setPlainText("No changes")
            return
        
        old_lines = old_text.splitlines(keepends=True) if old_text else []
        new_lines = new_text.splitlines(keepends=True) if new_text else []
        
        diff = difflib.unified_diff(
            old_lines, new_lines,
            lineterm="",
            n=context_lines
        )
        
        diff_text = "\n".join(diff)
        self._text.setPlainText(diff_text if diff_text else "No changes")


class CommentThreadWidget(QWidget):
    """Widget for displaying comment threads on diff lines."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._comments = []
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        
        self._header = QLabel("COMMENTS")
        self._header.setStyleSheet(f"font-size:{_fs(F_S)}; font-weight:700; color:{ACCENT};")
        layout.addWidget(self._header)
        
        self._comments_layout = QVBoxLayout()
        self._comments_layout.setSpacing(4)
        layout.addLayout(self._comments_layout)
        layout.addStretch()
    
    def add_comment(self, author: str, text: str, line: int, side: str = "right"):
        """Add a comment to the thread."""
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout as QVBoxLayout2
        
        frame = QFrame()
        frame.setStyleSheet(f"background:{BG_CARD}; border:1px solid {BORDER}; border-radius:4px; padding:8px;")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setSpacing(4)
        
        # Header with author and line info
        header_layout = QHBoxLayout()
        author_label = QLabel(f"<b>{author}</b>")
        author_label.setStyleSheet(f"color:{TEXT};")
        header_layout.addWidget(author_label)
        
        line_label = QLabel(f"line {line} ({side})")
        line_label.setStyleSheet(f"color:{ACCENT}; font-family:{FONT_MONO}; font-size:8pt;")
        header_layout.addWidget(line_label)
        header_layout.addStretch()
        
        frame_layout.addLayout(header_layout)
        
        # Comment text
        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setStyleSheet(f"color:{TEXT};")
        frame_layout.addWidget(text_label)
        
        self._comments_layout.addWidget(frame)
        self._comments.append({
            "author": author,
            "text": text,
            "line": line,
            "side": side,
            "widget": frame,
        })


def _fs(size: int) -> str:
    return f"{size}px"