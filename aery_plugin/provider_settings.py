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

# =============================================================================
# Presets — named providers with known URLs, API types, and model suggestions
# =============================================================================

# Each preset:
#   api        — Aery API type string (determines streaming implementation)
#   baseUrl    — API endpoint
#   auth       — "bearer" (Authorization: Bearer) or "header" (x-goog-api-key)
#   models     — Suggested models for the combo box
#   contextWindow, maxTokens — Derived from preset, not user-editable

PRESETS: dict[str, dict] = {
    "OpenAI": {
        "api": "openai-completions",
        "baseUrl": "https://api.openai.com/v1",
        "auth": "bearer",
        "models": [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            "o3",
            "o4-mini",
        ],
        "contextWindow": 128000,
        "maxTokens": 16384,
    },
    "OpenRouter": {
        "api": "openai-completions",
        "baseUrl": "https://openrouter.ai/api/v1",
        "auth": "bearer",
        "models": [
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "openai/o3-mini",
            "anthropic/claude-sonnet-4-20250514",
            "anthropic/claude-opus-4-20250514",
            "google/gemini-2.5-flash",
            "google/gemini-2.5-pro",
            "meta-llama/llama-4-scout",
            "meta-llama/llama-4-maverick",
            "deepseek/deepseek-chat",
            "qwen/qwen-2.5-72b-instruct",
            "mistralai/mistral-large",
        ],
        "contextWindow": 128000,
        "maxTokens": 8192,
    },
    "Google Gemini": {
        "api": "google-generative-ai",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
        "auth": "header",  # Uses x-goog-api-key header
        "models": [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.5-pro",
        ],
        "contextWindow": 1048576,
        "maxTokens": 8192,
    },
    "Anthropic (Claude)": {
        "api": "anthropic-messages",
        "baseUrl": "https://api.anthropic.com",
        "auth": "bearer",
        "models": [
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-haiku-3-20250313",
        ],
        "contextWindow": 200000,
        "maxTokens": 8192,
    },
    "Custom (OpenAI-compatible)": {
        "api": "openai-completions",
        "baseUrl": "",
        "auth": "bearer",
        "models": [],
        "contextWindow": 128000,
        "maxTokens": 8192,
    },
}


class ProviderSettingsDialog(QDialog):
    """Dialog for configuring the AI provider from within QGIS.

    Settings are stored in QSettings (QGIS profile) — no external config files.
    Supports named presets (OpenAI, OpenRouter, Gemini, Claude) and Custom.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Aery — Provider Configuration")
        self.setMinimumWidth(480)

        self._build_ui()
        self._load_settings()

    # =========================================================================
    # UI Construction
    # =========================================================================

    def _build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()

        # ── Preset selector ──
        self.preset_combo = QComboBox()
        for name in PRESETS:
            self.preset_combo.addItem(name)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        form.addRow("Preset:", self.preset_combo)

        # ── API Type (read-only, derived from preset) ──
        self.api_type_label = QLabel("openai-completions")
        self.api_type_label.setStyleSheet("color: #888;")
        form.addRow("API Type:", self.api_type_label)

        # ── Base URL ──
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Base URL:", self.base_url)

        # ── API Key ──
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-... or your API key")
        form.addRow("API Key:", self.api_key)

        # ── Model ──
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setPlaceholderText("Select or type a model...")
        form.addRow("Model:", self.model_combo)

        layout.addLayout(form)

        # ── Test connection ──
        test_layout = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._test_connection)
        self.test_status = QLabel("")
        test_layout.addWidget(self.test_btn)
        test_layout.addWidget(self.test_status)
        layout.addLayout(test_layout)

        # ── Buttons ──
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save_and_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    # =========================================================================
    # Preset handling
    # =========================================================================

    def _on_preset_changed(self):
        """Apply the selected preset — fills in URL, models, and defaults."""
        preset_name = self.preset_combo.currentText()
        preset = PRESETS.get(preset_name)
        if not preset:
            return

        self.api_type_label.setText(preset["api"])
        self.base_url.setText(preset["baseUrl"])

        # Populate model combo
        self.model_combo.clear()
        self.model_combo.addItems(preset["models"])

        # Show a hint for auth type in the placeholder
        if preset["auth"] == "header":
            self.api_key.setPlaceholderText("Your Google API key")
        else:
            self.api_key.setPlaceholderText("sk-... or your API key")

    # =========================================================================
    # Persistence (QSettings)
    # =========================================================================

    def _load_settings(self):
        """Restore last saved config from QSettings."""
        settings = QSettings()

        preset_name = settings.value("aery/provider/preset", "")
        if preset_name and preset_name in PRESETS:
            idx = self.preset_combo.findText(preset_name)
            if idx >= 0:
                self.preset_combo.setCurrentIndex(idx)

        # Restore fields (in case of custom or overrides)
        saved_url = settings.value("aery/provider/baseUrl", "")
        if saved_url:
            self.base_url.setText(saved_url)

        saved_key = settings.value("aery/provider/apiKey", "")
        if saved_key:
            self.api_key.setText(saved_key)

        saved_model = settings.value("aery/provider/model", "")
        if saved_model:
            idx = self.model_combo.findText(saved_model)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            else:
                self.model_combo.setEditText(saved_model)

    def _save_and_accept(self):
        """Save to QSettings and close."""
        settings = QSettings()
        settings.setValue("aery/provider/preset", self.preset_combo.currentText())
        settings.setValue("aery/provider/api", self.api_type_label.text())
        settings.setValue("aery/provider/baseUrl", self.base_url.text().strip())
        settings.setValue("aery/provider/apiKey", self.api_key.text().strip())
        settings.setValue("aery/provider/model", self.model_combo.currentText().strip())

        # Derive contextWindow/maxTokens from preset (not user-editable)
        preset = PRESETS.get(self.preset_combo.currentText(), {})
        settings.setValue("aery/provider/contextWindow", preset.get("contextWindow", 128000))
        settings.setValue("aery/provider/maxTokens", preset.get("maxTokens", 8192))

        self.accept()

    # =========================================================================
    # Connection test
    # =========================================================================

    def _test_connection(self):
        """Test the provider by sending a minimal request."""
        import urllib.request
        import urllib.error
        import ssl

        self.test_btn.setEnabled(False)
        self.test_status.setText("Testing...")
        self.test_status.setStyleSheet("color: gray;")

        base_url = self.base_url.text().strip().rstrip("/")
        api_key = self.api_key.text().strip()
        model = self.model_combo.currentText().strip()
        api_type = self.api_type_label.text()

        if not base_url or not api_key or not model:
            self.test_status.setText("Fill in URL, key, and model first")
            self.test_status.setStyleSheet("color: orange;")
            self.test_btn.setEnabled(True)
            return

        try:
            ctx = ssl.create_default_context()

            if api_type == "google-generative-ai":
                # Gemini uses REST: GET /models/{model}:generateContent with x-goog-api-key
                url = f"{base_url}/models/{model}:generateContent?key={api_key}"
                data = json.dumps({
                    "contents": [{"parts": [{"text": "Hello"}]}],
                    "generationConfig": {"maxOutputTokens": 5},
                }).encode()
                req = urllib.request.Request(url, data=data, method="POST")
                req.add_header("Content-Type", "application/json")
            elif api_type == "anthropic-messages":
                # Anthropic uses /messages with x-api-key header
                url = f"{base_url}/v1/messages"
                data = json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 5,
                }).encode()
                req = urllib.request.Request(url, data=data, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("x-api-key", api_key)
                req.add_header("anthropic-version", "2023-06-01")
            else:
                # OpenAI-compatible: POST /chat/completions with Bearer token
                url = f"{base_url}/chat/completions"
                data = json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 5,
                }).encode()
                req = urllib.request.Request(url, data=data, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", f"Bearer {api_key}")

            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                body = json.loads(resp.read())
                if api_type == "google-generative-ai":
                    if "candidates" in body:
                        self.test_status.setText("✓ Connected")
                        self.test_status.setStyleSheet("color: green;")
                    else:
                        self.test_status.setText(f"Unexpected: {str(body)[:80]}")
                        self.test_status.setStyleSheet("color: orange;")
                elif api_type == "anthropic-messages":
                    if "content" in body:
                        self.test_status.setText("✓ Connected")
                        self.test_status.setStyleSheet("color: green;")
                    else:
                        self.test_status.setText(f"Unexpected: {str(body)[:80]}")
                        self.test_status.setStyleSheet("color: orange;")
                else:
                    if "model" in body or "choices" in body:
                        self.test_status.setText("✓ Connected")
                        self.test_status.setStyleSheet("color: green;")
                    else:
                        self.test_status.setText(f"Unexpected: {str(body)[:80]}")
                        self.test_status.setStyleSheet("color: orange;")
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")[:80]
            if e.code == 401:
                self.test_status.setText("✗ Invalid API key")
            elif e.code == 404:
                self.test_status.setText(f"✗ Model '{model}' not found or wrong URL")
            elif e.code == 400:
                self.test_status.setText(f"✗ Bad request: {body_text}")
            else:
                self.test_status.setText(f"✗ HTTP {e.code}: {body_text}")
            self.test_status.setStyleSheet("color: red;")
        except Exception as e:
            self.test_status.setText(f"✗ {str(e)[:60]}")
            self.test_status.setStyleSheet("color: red;")
        finally:
            self.test_btn.setEnabled(True)

    # =========================================================================
    # Static helpers (used by plugin.py / rpc_bridge.py)
    # =========================================================================

    @staticmethod
    def load_config() -> dict:
        """Load the current provider config from QSettings.

        Returns a dict suitable for --provider-file:
          { api, baseUrl, apiKey, model, contextWindow, maxTokens }
        Returns empty dict if no provider is configured.

        contextWindow and maxTokens are derived from the selected preset
        (not user-editable). Falls back to sensible defaults.
        """
        settings = QSettings()
        api = settings.value("aery/provider/api", "")
        if not api:
            return {}

        # Derive token limits from preset
        preset_name = settings.value("aery/provider/preset", "")
        preset = PRESETS.get(preset_name, {})

        return {
            "api": api,
            "baseUrl": settings.value("aery/provider/baseUrl", ""),
            "apiKey": settings.value("aery/provider/apiKey", ""),
            "model": settings.value("aery/provider/model", ""),
            "contextWindow": int(preset.get("contextWindow", 128000)),
            "maxTokens": int(preset.get("maxTokens", 8192)),
        }

    @staticmethod
    def is_configured() -> bool:
        """Check if a provider has been configured."""
        settings = QSettings()
        return bool(settings.value("aery/provider/api", ""))
