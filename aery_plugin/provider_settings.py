"""Simplified Provider configuration wizard for the Aery QGIS plugin.
Replaced the old 35-provider UI with a clean 4-provider UI matching the new LLM Engine.
"""

from typing import Optional
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QWidget,
)

# ── Palette ───────────────────────────────────────────────────────────────────
BG      = "#09090b"
SURFACE = "#18181b"
ACCENT  = "#8abeb7"
BORDER  = "#27272a"
TEXT    = "#e4e4e7"
DIM     = "#52525b"

def _fs(size: int) -> str:
    return f"{max(size, 8)}px"

class AeryConfigDialog(QDialog):
    """User preferences configuration for LLM providers."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("AERY PROVIDER CONFIGURATION")
        self.setFixedSize(400, 350)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG}; }}
            QLabel {{ color: {TEXT}; font-family: 'Inter', sans-serif; }}
            QComboBox {{ 
                background-color: {SURFACE}; color: {TEXT}; 
                border: 1px solid {BORDER}; border-radius: 4px; 
                padding: 8px; font-size: {_fs(11)};
            }}
            QLineEdit {{ 
                background-color: {SURFACE}; color: {TEXT}; 
                border: 1px solid {BORDER}; border-radius: 4px; 
                padding: 8px; font-size: {_fs(11)};
            }}
            QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
        """)
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(15)

        # ── Header ──
        title = QLabel("AI PROVIDER")
        title.setStyleSheet(f"font-weight: 800; font-size: {_fs(10)}; letter-spacing: 0.1em; color: {DIM};")
        layout.addWidget(title)

        # ── Provider Selector ──
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("OpenCode Zen", "opencode-zen")
        self.provider_combo.addItem("Kilo", "kilo")
        self.provider_combo.addItem("Antigravity", "google-antigravity")
        self.provider_combo.addItem("Custom OpenAI-Compatible", "custom-openai")
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)

        # ── API Key ──
        lbl_key = QLabel("API KEY (Optional for some providers)")
        lbl_key.setStyleSheet(f"color: {DIM}; font-size: {_fs(10)}; font-weight: 600;")
        layout.addWidget(lbl_key)
        
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Enter API Key...")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        layout.addWidget(self.api_key_input)

        # ── Base URL ──
        self.lbl_url = QLabel("BASE URL")
        self.lbl_url.setStyleSheet(f"color: {DIM}; font-size: {_fs(10)}; font-weight: 600;")
        layout.addWidget(self.lbl_url)
        
        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText("https://api.openai.com/v1")
        layout.addWidget(self.base_url_input)

        layout.addStretch()

        # ── Actions ──
        actions = QHBoxLayout()
        actions.setSpacing(12)
        
        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {DIM}; border: none; font-size: {_fs(11)}; font-weight: bold; }} QPushButton:hover {{ color: {TEXT}; }}")
        cancel_btn.clicked.connect(self.reject)
        actions.addWidget(cancel_btn)

        save_btn = QPushButton("SAVE CONFIGURATION")
        save_btn.setFixedHeight(34)
        save_btn.setStyleSheet(f"QPushButton {{ background-color: {ACCENT}; color: {BG}; border: none; border-radius: 4px; padding: 0 20px; font-size: {_fs(11)}; font-weight: 800; }}")
        save_btn.clicked.connect(self._save_settings)
        actions.addWidget(save_btn)

        layout.addLayout(actions)

    def _on_provider_changed(self):
        provider_id = self.provider_combo.currentData()
        is_custom = (provider_id == "custom-openai")
        self.lbl_url.setVisible(is_custom)
        self.base_url_input.setVisible(is_custom)

    def _load_settings(self):
        settings = QSettings()
        provider_id = settings.value("aery/settings/provider", "opencode-zen")
        idx = self.provider_combo.findData(provider_id)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
            
        self.api_key_input.setText(settings.value("aery/settings/api_key", ""))
        self.base_url_input.setText(settings.value("aery/settings/base_url", "https://api.openai.com/v1"))
        self._on_provider_changed()

    def _save_settings(self):
        settings = QSettings()
        settings.setValue("aery/settings/provider", self.provider_combo.currentData())
        settings.setValue("aery/settings/api_key", self.api_key_input.text().strip())
        if self.base_url_input.isVisible():
            settings.setValue("aery/settings/base_url", self.base_url_input.text().strip())
        self.accept()
