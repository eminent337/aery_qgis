#!/usr/bin/env python3
"""Multi-step Approval Flow for Code Review in Aery QGIS Plugin."""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from aery_plugin.diff_widget import DiffWidget
from aery_plugin.ui_constants import ACCENT, BG_BASE, BG_CARD, BORDER, DIM, RED, TEXT, F_H, F_S


class ApprovalStage(Enum):
    """Stages in the approval flow."""
    REVIEW = "review"      # Showing diff, user reviews
    APPROVE = "approve"    # User approved, ready to execute
    EXECUTE = "execute"    # Execution in progress
    DONE = "done"          # Completed (success or cancelled)


class MultiStepApprovalWidget(QWidget):
    """Multi-step approval widget: Review → Approve → Execute."""
    
    # Signals
    approve_requested = pyqtSignal()
    execute_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    stage_changed = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._stage = ApprovalStage.REVIEW
        self._old_code = ""
        self._new_code = ""
        self._description = ""
        self._tool_name = ""
        self._setup_ui()
    
    def _setup_ui(self):
        self.setStyleSheet(f"background:{BG_CARD}; border:1px solid {BORDER}; border-radius:6px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Stage indicator
        self._stage_indicator = QLabel()
        self._stage_indicator.setStyleSheet(f"font-size:{_fs(F_S)}; font-weight:700; color:{ACCENT}; letter-spacing:0.1em;")
        layout.addWidget(self._stage_indicator)
        
        # Tool info
        self._tool_info = QLabel()
        self._tool_info.setStyleSheet(f"font-size:{_fs(F_H)}; color:{TEXT};")
        self._tool_info.setWordWrap(True)
        layout.addWidget(self._tool_info)
        
        # Diff widget
        self._diff = DiffWidget()
        layout.addWidget(self._diff, 1)
        
        # Action buttons (change based on stage)
        self._button_container = QWidget()
        self._button_layout = QHBoxLayout(self._button_container)
        self._button_layout.setContentsMargins(0, 0, 0, 0)
        self._button_layout.setSpacing(8)
        layout.addWidget(self._button_container)
        
        # Create buttons
        self._btn_review = QPushButton("REVIEW CHANGES")
        self._btn_review.setStyleSheet(f"background:{ACCENT}; color:white; border:none; border-radius:4px; padding:10px 20px; font-weight:700;")
        self._btn_review.clicked.connect(self._on_review_clicked)
        
        self._btn_approve = QPushButton("APPROVE & EXECUTE")
        self._btn_approve.setStyleSheet(f"background:{ACCENT}; color:white; border:none; border-radius:4px; padding:10px 20px; font-weight:700;")
        self._btn_approve.clicked.connect(self._on_approve_clicked)
        
        self._btn_execute = QPushButton("EXECUTING...")
        self._btn_execute.setStyleSheet(f"background:{DIM}; color:white; border:none; border-radius:4px; padding:10px 20px; font-weight:700;")
        self._btn_execute.setEnabled(False)
        
        self._btn_cancel = QPushButton("CANCEL")
        self._btn_cancel.setStyleSheet(f"background:transparent; color:{TEXT}; border:1px solid {BORDER}; border-radius:4px; padding:10px 20px; font-weight:600;")
        self._btn_cancel.clicked.connect(self._on_cancel_clicked)
        
        self._btn_done = QPushButton("DONE")
        self._btn_done.setStyleSheet(f"background:{ACCENT}; color:white; border:none; border-radius:4px; padding:10px 20px; font-weight:700;")
        self._btn_done.clicked.connect(self._on_done_clicked)
        self._btn_done.hide()
        
        # Initial button state
        self._update_buttons()
    
    def set_tool_info(self, tool_name: str, description: str, old_code: str, new_code: str):
        """Set the tool information and code diff."""
        self._tool_name = tool_name
        self._description = description
        self._old_code = old_code
        self._new_code = new_code
        
        # Update tool info label
        self._tool_info.setText(f"<b>{tool_name}</b>: {description}")
        
        # Set diff
        self._diff.set_diff(old_code, new_code)
        
        # Reset to review stage
        self._set_stage(ApprovalStage.REVIEW)
    
    def _set_stage(self, stage: ApprovalStage):
        """Set the current approval stage."""
        self._stage = stage
        self._update_stage_indicator()
        self._update_buttons()
        self.stage_changed.emit(stage.value)
    
    def _update_stage_indicator(self):
        """Update the stage indicator text."""
        stage_texts = {
            ApprovalStage.REVIEW: "STAGE 1/3: REVIEW CHANGES",
            ApprovalStage.APPROVE: "STAGE 2/3: APPROVE",
            ApprovalStage.EXECUTE: "STAGE 3/3: EXECUTING",
            ApprovalStage.DONE: "COMPLETED",
        }
        self._stage_indicator.setText(stage_texts.get(self._stage, ""))
    
    def _update_buttons(self):
        """Update visible buttons based on stage."""
        # Clear existing buttons
        for i in reversed(range(self._button_layout.count())):
            item = self._button_layout.itemAt(i)
            if item and item.widget():
                self._button_layout.removeWidget(item.widget())
        
        if self._stage == ApprovalStage.REVIEW:
            self._button_layout.addWidget(self._btn_review)
            self._button_layout.addWidget(self._btn_cancel)
        
        elif self._stage == ApprovalStage.APPROVE:
            self._button_layout.addWidget(self._btn_approve)
            self._button_layout.addWidget(self._btn_cancel)
        
        elif self._stage == ApprovalStage.EXECUTE:
            self._button_layout.addWidget(self._btn_execute)
            self._button_layout.addWidget(self._btn_cancel)
        
        elif self._stage == ApprovalStage.DONE:
            self._button_layout.addWidget(self._btn_done)
    
    def _on_review_clicked(self):
        """User clicked REVIEW CHANGES - move to APPROVE stage."""
        self._set_stage(ApprovalStage.APPROVE)
    
    def _on_approve_clicked(self):
        """User clicked APPROVE & EXECUTE - emit signals."""
        self._set_stage(ApprovalStage.EXECUTE)
        self.approve_requested.emit()
        self.execute_requested.emit()
    
    def _on_cancel_clicked(self):
        """User clicked CANCEL - emit cancel signal."""
        self.cancel_requested.emit()
    
    def _on_done_clicked(self):
        """User clicked DONE - close dialog."""
        self.cancel_requested.emit()  # Use same signal to close
    
    def set_execution_complete(self, success: bool, message: str = ""):
        """Mark execution as complete."""
        self._set_stage(ApprovalStage.DONE)
        if success:
            self._stage_indicator.setText("EXECUTION SUCCESSFUL")
            self._stage_indicator.setStyleSheet(f"font-size:{_fs(F_S)}; font-weight:700; color:#69db7c; letter-spacing:0.1em;")
        else:
            self._stage_indicator.setText("EXECUTION FAILED")
            self._stage_indicator.setStyleSheet(f"font-size:{_fs(F_S)}; font-weight:700; color:{RED}; letter-spacing:0.1em;")
        if message:
            self._tool_info.setText(f"{self._tool_info.text()}<br><br><i>{message}</i>")


class MultiStepApprovalDialog(QDialog):
    """Dialog wrapper for MultiStepApprovalWidget."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Code Review & Approval")
        self.setMinimumSize(900, 700)
        self.resize(1000, 800)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self._approval_widget = MultiStepApprovalWidget()
        layout.addWidget(self._approval_widget)
        
        # Connect signals
        self._approval_widget.approve_requested.connect(self._on_approve)
        self._approval_widget.execute_requested.connect(self._on_execute)
        self._approval_widget.cancel_requested.connect(self.reject)
        self._approval_widget.stage_changed.connect(self._on_stage_changed)
    
    def set_tool_info(self, tool_name: str, description: str, old_code: str, new_code: str):
        """Set the tool information for review."""
        self._approval_widget.set_tool_info(tool_name, description, old_code, new_code)
    
    def set_execution_complete(self, success: bool, message: str = ""):
        """Mark execution as complete."""
        self._approval_widget.set_execution_complete(success, message)
    
    def _on_approve(self):
        """Handle approve request."""
        pass  # Handled by parent
    
    def _on_execute(self):
        """Handle execute request."""
        pass  # Handled by parent
    
    def _on_stage_changed(self, stage: str):
        """Handle stage change."""
        pass  # Can be used to update dialog title
    
    def get_approval_widget(self) -> MultiStepApprovalWidget:
        """Get the inner approval widget."""
        return self._approval_widget


def show_code_review_dialog(
    parent: QWidget,
    tool_name: str,
    description: str,
    old_code: str,
    new_code: str,
    on_approve: Callable[[], None],
    on_execute: Callable[[], None],
    on_cancel: Callable[[], None],
) -> MultiStepApprovalDialog:
    """Show the multi-step approval dialog."""
    dialog = MultiStepApprovalDialog(parent)
    dialog.set_tool_info(tool_name, description, old_code, new_code)
    
    # Connect callbacks
    approval_widget = dialog.get_approval_widget()
    approval_widget.approve_requested.connect(on_approve)
    approval_widget.execute_requested.connect(on_execute)
    approval_widget.cancel_requested.connect(on_cancel)
    
    dialog.show()
    return dialog


def _fs(size: int) -> str:
    return f"{size}px"