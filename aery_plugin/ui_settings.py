"""User settings dialog for Aery QGIS plugin.
Visual preferences (Themes).
"""

from typing import Optional
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QFrame,
    QGridLayout,
    QWidget,
)

# ── PREMIUM INDUSTRIAL PALETTE ──
BG_BASE = "#1A1A1A"
BG_INPUT = "#252525"
ACCENT = "#00A3FF" # UI Settings use a different blue accent
BORDER = "#333333"
TEXT_MAIN = "#E5E5E7"
TEXT_DIM = "#707072"

class AeryUISettingsDialog(QDialog):
    """User preferences configuration."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("AERY SETTINGS")
        self.setFixedSize(380, 300)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {BG_BASE};
                color: {TEXT_MAIN};
                font-family: 'Inter', sans-serif;
            }}
            QToolTip {{
                background-color: {BG_INPUT};
                color: {TEXT_MAIN};
                border: 1px solid {BORDER};
                padding: 4px;
                font-size: 9px;
            }}
        """)
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(15)

        # ── Header ──
        title = QLabel("INTERFACE PREFERENCES")
        title.setStyleSheet(f"font-weight: 800; font-size: 10px; letter-spacing: 0.1em; color: {TEXT_DIM};")
        layout.addWidget(title)

        # ── Theme Selector ──
        lbl = QLabel("VISUAL THEME")
        lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 8px; font-weight: 800; letter-spacing: 0.05em;")
        layout.addWidget(lbl)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["aery", "dark", "dracula", "nord", "tokyo-night", "catppuccin-mocha", "light"])
        self.theme_combo.setStyleSheet(f"QComboBox {{ background-color: {BG_INPUT}; color: {TEXT_MAIN}; border: 1px solid {BORDER}; border-radius: 4px; padding: 10px; font-size: 12px; }}")
        layout.addWidget(self.theme_combo)

        layout.addStretch()

        # ── Actions ──
        actions = QHBoxLayout()
        actions.setSpacing(12)
        
        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {TEXT_DIM}; border: none; font-size: 9px; font-weight: bold; }} QPushButton:hover {{ color: {TEXT_MAIN}; }}")
        cancel_btn.clicked.connect(self.reject)
        actions.addWidget(cancel_btn)

        self.save_btn = QPushButton("SAVE SETTINGS")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setStyleSheet(f"QPushButton {{ background-color: {ACCENT}; color: white; border: none; border-radius: 4px; padding: 0 20px; font-size: 9px; font-weight: 800; }}")
        self.save_btn.clicked.connect(self._save_settings)
        actions.addWidget(self.save_btn)

        layout.addLayout(actions)

    def _load_settings(self):
        settings = QSettings()
        theme = settings.value("aery/settings/theme", "aery")
        idx = self.theme_combo.findText(theme)
        if idx >= 0: self.theme_combo.setCurrentIndex(idx)

    def _save_settings(self):
        settings = QSettings()
        settings.setValue("aery/settings/theme", self.theme_combo.currentText())
        self.accept()

    @staticmethod
    def load_theme() -> str:
        settings = QSettings()
        return settings.value("aery/settings/theme", "aery")
