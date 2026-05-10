"""Provider settings dialog for Aery QGIS Plugin."""

import json
from typing import Optional

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Default base URLs per API type
DEFAULT_URLS = {
    "openai-completions": "https://api.openai.com/v1",
    "anthropic-messages": "https://api.anthropic.com",
}


class ProviderSettingsDialog(QDialog):
    """Dialog for configuring the AI provider from within QGIS.

    Settings are stored in QSettings (QGIS profile) — no external config files.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Aery — Provider Configuration")
        self.setMinimumWidth(450)

        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Form
        form = QFormLayout()

        # API type
        self.api_type = QComboBox()
        self.api_type.addItem("OpenAI-compatible", "openai-completions")
        self.api_type.addItem("Anthropic", "anthropic-messages")
        self.api_type.currentIndexChanged.connect(self._on_api_type_changed)
        form.addRow("API Type:", self.api_type)

        # Base URL
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Base URL:", self.base_url)

        # API Key
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-...")
        form.addRow("API Key:", self.api_key)

        # Model
        self.model = QLineEdit()
        self.model.setPlaceholderText("gpt-4, claude-sonnet-4, etc.")
        form.addRow("Model:", self.model)

        # Context window
        self.context_window = QLineEdit()
        self.context_window.setPlaceholderText("128000")
        form.addRow("Context Window:", self.context_window)

        # Max tokens
        self.max_tokens = QLineEdit()
        self.max_tokens.setPlaceholderText("8192")
        form.addRow("Max Tokens:", self.max_tokens)

        layout.addLayout(form)

        # Test connection button
        test_layout = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._test_connection)
        self.test_status = QLabel("")
        test_layout.addWidget(self.test_btn)
        test_layout.addWidget(self.test_status)
        layout.addLayout(test_layout)

        # Button box
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save_and_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _on_api_type_changed(self):
        """Update base URL placeholder when API type changes."""
        api = self.api_type.currentData()
        if api in DEFAULT_URLS:
            if not self.base_url.text() or self.base_url.text() in DEFAULT_URLS.values():
                self.base_url.setText(DEFAULT_URLS[api])

    def _load_settings(self):
        """Load provider config from QSettings."""
        settings = QSettings()
        api = settings.value("aery/provider/api", "")
        if api:
            idx = self.api_type.findData(api)
            if idx >= 0:
                self.api_type.setCurrentIndex(idx)
            self.base_url.setText(settings.value("aery/provider/baseUrl", ""))
            self.api_key.setText(settings.value("aery/provider/apiKey", ""))
            self.model.setText(settings.value("aery/provider/model", ""))
            self.context_window.setText(settings.value("aery/provider/contextWindow", "128000"))
            self.max_tokens.setText(settings.value("aery/provider/maxTokens", "8192"))

    def _save_and_accept(self):
        """Save provider config to QSettings and close."""
        settings = QSettings()
        settings.setValue("aery/provider/api", self.api_type.currentData())
        settings.setValue("aery/provider/baseUrl", self.base_url.text().strip())
        settings.setValue("aery/provider/apiKey", self.api_key.text().strip())
        settings.setValue("aery/provider/model", self.model.text().strip())
        cw = self.context_window.text().strip()
        settings.setValue("aery/provider/contextWindow", int(cw) if cw.isdigit() else 128000)
        mt = self.max_tokens.text().strip()
        settings.setValue("aery/provider/maxTokens", int(mt) if mt.isdigit() else 8192)
        self.accept()

    def _test_connection(self):
        """Test the provider connection by sending a simple request."""
        import urllib.request
        import urllib.error
        import ssl

        self.test_btn.setEnabled(False)
        self.test_status.setText("Testing...")
        self.test_status.setStyleSheet("color: gray;")

        base_url = self.base_url.text().strip().rstrip("/")
        api_key = self.api_key.text().strip()
        model = self.model.text().strip()

        if not base_url or not api_key or not model:
            self.test_status.setText("Fill in URL, key, and model first")
            self.test_status.setStyleSheet("color: orange;")
            self.test_btn.setEnabled(True)
            return

        try:
            ctx = ssl.create_default_context()
            data = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 5,
            }).encode()
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=data,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                body = json.loads(resp.read())
                if "model" in body or "choices" in body:
                    self.test_status.setText("✓ Connected")
                    self.test_status.setStyleSheet("color: green;")
                else:
                    self.test_status.setText(f"Unexpected response: {str(body)[:80]}")
                    self.test_status.setStyleSheet("color: orange;")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.test_status.setText("✗ Invalid API key")
            elif e.code == 404:
                self.test_status.setText(f"✗ Model '{model}' not found at this URL")
            else:
                self.test_status.setText(f"✗ HTTP {e.code}: {e.reason[:60]}")
            self.test_status.setStyleSheet("color: red;")
        except Exception as e:
            self.test_status.setText(f"✗ {str(e)[:60]}")
            self.test_status.setStyleSheet("color: red;")
        finally:
            self.test_btn.setEnabled(True)

    @staticmethod
    def load_config() -> dict:
        """Load the current provider config from QSettings.

        Returns a dict suitable for sending as provider_config over stdin.
        Returns empty dict if no provider is configured.
        """
        settings = QSettings()
        api = settings.value("aery/provider/api", "")
        if not api:
            return {}
        return {
            "api": api,
            "baseUrl": settings.value("aery/provider/baseUrl", ""),
            "apiKey": settings.value("aery/provider/apiKey", ""),
            "model": settings.value("aery/provider/model", ""),
            "contextWindow": int(settings.value("aery/provider/contextWindow", "128000")),
            "maxTokens": int(settings.value("aery/provider/maxTokens", "8192")),
        }

    @staticmethod
    def is_configured() -> bool:
        """Check if a provider has been configured."""
        settings = QSettings()
        return bool(settings.value("aery/provider/api", ""))
