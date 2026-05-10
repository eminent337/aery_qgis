"""Provider settings dialog for Aery QGIS Plugin."""

import json
import threading
from typing import Optional

from PyQt6.QtCore import QSettings, Qt, QTimer
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
# Presets — named providers with known URLs and API types
# =============================================================================

PRESETS: dict[str, dict] = {
    "OpenAI": {
        "api": "openai-completions",
        "baseUrl": "https://api.openai.com/v1",
        "auth": "bearer",
        "docs": "https://platform.openai.com/docs/api-reference",
    },
    "OpenRouter": {
        "api": "openai-completions",
        "baseUrl": "https://openrouter.ai/api/v1",
        "auth": "bearer",
        "docs": "https://openrouter.ai/docs",
    },
    "Google Gemini": {
        "api": "google-generative-ai",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
        "auth": "header",
        "docs": "https://ai.google.dev/docs",
    },
    "Anthropic (Claude)": {
        "api": "anthropic-messages",
        "baseUrl": "https://api.anthropic.com",
        "auth": "bearer",
        "docs": "https://docs.anthropic.com/claude/reference",
    },
    "Groq": {
        "api": "openai-completions",
        "baseUrl": "https://api.groq.com/openai/v1",
        "auth": "bearer",
        "docs": "https://console.groq.com/docs/models",
    },
    "Ollama (Local)": {
        "api": "openai-completions",
        "baseUrl": "http://localhost:11434/v1",
        "auth": "bearer",
        "docs": "https://ollama.com/library",
    },
    "Together AI": {
        "api": "openai-completions",
        "baseUrl": "https://api.together.xyz/v1",
        "auth": "bearer",
        "docs": "https://docs.together.ai",
    },
    "Cloudflare AI Gateway": {
        "api": "openai-completions",
        "baseUrl": "https://api.cloudflare.com/client/v4/accounts/YOUR_ACCOUNT_ID/ai",
        "auth": "bearer",
        "docs": "https://developers.cloudflare.com/workers-ai/",
    },
    "Custom": {
        "api": "openai-completions",
        "baseUrl": "",
        "auth": "bearer",
        "docs": "",
    },
}

# Context windows for known models (used when API doesn't provide this)
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 128000,
    "gpt-4.1-mini": 128000,
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-haiku-3-20250313": 200000,
    "gemini-2.5-flash": 1048576,
    "gemini-2.5-pro": 1048576,
    "gemini-2.0-flash": 128000,
    "llama-3.3-70b-versatile": 128000,
    "llama3.2": 8192,
}


class ProviderSettingsDialog(QDialog):
    """Dialog for configuring the AI provider from within QGIS.

    Settings are stored in QSettings (QGIS profile) — no external config files.
    Dynamically fetches available models from the API when key is provided.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Aery — Provider Settings")
        self.setMinimumWidth(520)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e24; }
            QLabel { color: #e5e5e7; }
            QPushButton { background-color: #343541; color: white; border: none; padding: 8px 16px; border-radius: 4px; }
            QPushButton:hover { background-color: #3a3a4a; }
            QComboBox { background-color: #282832; color: #e5e5e7; border: 1px solid #3a3a4a; padding: 6px; border-radius: 4px; }
            QLineEdit { background-color: #282832; color: #e5e5e7; border: 1px solid #3a3a4a; padding: 6px; border-radius: 4px; }
            QLineEdit:focus { border-color: #8abeb7; }
        """)

        self._fetching_models = False
        self._build_ui()
        self._load_settings()

    # =========================================================================
    # UI Construction
    # =========================================================================

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # ── Header with status ──
        header = QHBoxLayout()
        title = QLabel("AI Provider Configuration")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #8abeb7;")
        header.addWidget(title)
        header.addStretch()

        self.status_indicator = QLabel("●")
        self.status_indicator.setStyleSheet("color: #888888; font-size: 20px;")
        self.status_indicator.setToolTip("Not configured")
        header.addWidget(self.status_indicator)
        layout.addLayout(header)

        # ── Form ──
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(12)

        # Preset selector
        preset_row = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(200)
        for name in PRESETS:
            self.preset_combo.addItem(name)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo)
        preset_row.addStretch()
        form.addRow("Provider:", preset_row)

        # Base URL
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Base URL:", self.base_url)

        # API Key with toggle
        key_row = QHBoxLayout()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-... or your API key")
        self.api_key.textChanged.connect(self._on_api_key_changed)
        key_row.addWidget(self.api_key, 1)

        self.toggle_key_btn = QPushButton("👁")
        self.toggle_key_btn.setFixedWidth(36)
        self.toggle_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_key_btn.setStyleSheet("padding: 4px; background: transparent; border: 1px solid #3a3a4a;")
        self.toggle_key_btn.clicked.connect(self._toggle_api_key_visibility)
        key_row.addWidget(self.toggle_key_btn)
        form.addRow("API Key:", key_row)

        # Model selector
        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(300)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setPlaceholderText("Select a model...")

        self.fetch_models_btn = QPushButton("⟳")
        self.fetch_models_btn.setFixedWidth(36)
        self.fetch_models_btn.setToolTip("Fetch available models from API")
        self.fetch_models_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fetch_models_btn.setStyleSheet("padding: 4px; background: transparent; border: 1px solid #3a3a4a;")
        self.fetch_models_btn.clicked.connect(self._fetch_models)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.fetch_models_btn)
        form.addRow("Model:", model_row)

        # Context window info
        self.context_label = QLabel("128k tokens")
        self.context_label.setStyleSheet("color: #888888; font-size: 11px;")
        form.addRow("", self.context_label)

        layout.addLayout(form)

        # ── Docs link ──
        self.docs_link = QPushButton("📖 View API Documentation")
        self.docs_link.setStyleSheet("color: #8abeb7; background: transparent; border: none; text-decoration: underline;")
        self.docs_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.docs_link.clicked.connect(self._open_docs)
        layout.addWidget(self.docs_link)

        # ── Test connection ──
        test_layout = QHBoxLayout()
        test_layout.addStretch()

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setStyleSheet("background-color: #8abeb7; color: #1e1e24; font-weight: bold;")
        self.test_btn.clicked.connect(self._test_connection)
        test_layout.addWidget(self.test_btn)

        self.test_status = QLabel("")
        test_layout.addWidget(self.test_status, 1)
        layout.addLayout(test_layout)

        # ── Buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setStyleSheet("background-color: #cc6666;")
        self.reset_btn.clicked.connect(self._reset_to_defaults)
        btn_layout.addWidget(self.reset_btn)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save_and_accept)
        self.buttons.rejected.connect(self.reject)
        btn_layout.addWidget(self.buttons)
        layout.addLayout(btn_layout)

    # =========================================================================
    # Preset handling
    # =========================================================================

    def _on_preset_changed(self):
        """Apply the selected preset — fills in URL and clears model."""
        preset_name = self.preset_combo.currentText()
        preset = PRESETS.get(preset_name)
        if not preset:
            return

        self.base_url.setText(preset["baseUrl"])
        self.model_combo.clear()
        self.docs_link.setHidden(not bool(preset.get("docs", "")))
        if preset.get("docs"):
            self.docs_link.setToolTip(preset["docs"])

    def _on_api_key_changed(self, text: str):
        """Auto-fetch models when API key is provided."""
        if text and len(text) > 10:
            QTimer.singleShot(500, self._fetch_models)

    def _toggle_api_key_visibility(self):
        """Toggle API key visibility."""
        if self.api_key.echoMode() == QLineEdit.EchoMode.Password:
            self.api_key.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_key_btn.setText("🔒")
        else:
            self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_key_btn.setText("👁")

    def _open_docs(self):
        """Open docs link in browser."""
        import webbrowser
        preset = PRESETS.get(self.preset_combo.currentText(), {})
        url = preset.get("docs", "")
        if url:
            webbrowser.open(url)

    def _reset_to_defaults(self):
        """Reset all fields."""
        self.preset_combo.setCurrentIndex(0)
        self.base_url.clear()
        self.api_key.clear()
        self.model_combo.clear()
        self.test_status.setText("")
        self._update_status_indicator()

    def _update_status_indicator(self):
        """Update the status indicator based on configuration."""
        has_key = bool(self.api_key.text().strip())
        has_url = bool(self.base_url.text().strip())
        has_model = bool(self.model_combo.currentText().strip())

        if has_key and has_url and has_model:
            self.status_indicator.setText("●")
            self.status_indicator.setStyleSheet("color: #88c088; font-size: 20px;")
            self.status_indicator.setToolTip("Configured")
        elif has_key or has_url:
            self.status_indicator.setText("◐")
            self.status_indicator.setStyleSheet("color: #e6c866; font-size: 20px;")
            self.status_indicator.setToolTip("Partially configured")
        else:
            self.status_indicator.setText("●")
            self.status_indicator.setStyleSheet("color: #888888; font-size: 20px;")
            self.status_indicator.setToolTip("Not configured")

    def _fetch_models(self):
        """Fetch available models from the configured API."""
        if self._fetching_models:
            return

        api_key = self.api_key.text().strip()
        base_url = self.base_url.text().strip().rstrip("/")
        api_type = PRESETS.get(self.preset_combo.currentText(), {}).get("api", "openai-completions")

        if not api_key or not base_url:
            return

        self._fetching_models = True
        self.fetch_models_btn.setText("...")
        self.model_combo.clear()
        self.model_combo.addItem("Loading models...")
        self.model_combo.setEnabled(False)

        def fetch():
            models = []
            try:
                import urllib.request
                import urllib.error
                import ssl

                ctx = ssl.create_default_context()
                headers = {"Content-Type": "application/json"}

                if api_type == "google-generative-ai":
                    url = f"{base_url}/models?key={api_key}"
                    req = urllib.request.Request(url, headers=headers)
                elif api_type == "anthropic-messages":
                    url = f"{base_url}/v1/models"
                    headers["x-api-key"] = api_key
                    headers["anthropic-version"] = "2023-06-01"
                    req = urllib.request.Request(url, headers=headers)
                else:
                    # OpenAI-compatible: try /models endpoint
                    url = f"{base_url}/models"
                    headers["Authorization"] = f"Bearer {api_key}"
                    req = urllib.request.Request(url, headers=headers)

                with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                    data = json.loads(resp.read())

                    if api_type == "google-generative-ai":
                        for m in data.get("models", []):
                            models.append(m["name"])
                    elif api_type == "anthropic-messages":
                        for m in data.get("data", []):
                            models.append(m["id"])
                    else:
                        for m in data.get("data", []):
                            models.append(m["id"])

            except Exception as e:
                models = [f"Error: {str(e)[:50]}"]

            def update_ui():
                self.model_combo.clear()
                if models:
                    self.model_combo.addItems(models)
                    self.context_label.setText("Models loaded from API")
                self.model_combo.setEnabled(True)
                self.fetch_models_btn.setText("⟳")
                self._fetching_models = False
                self._update_status_indicator()

            QTimer.singleShot(0, update_ui)

        threading.Thread(target=fetch, daemon=True).start()

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
        preset = PRESETS.get(self.preset_combo.currentText(), {})

        settings.setValue("aery/provider/preset", self.preset_combo.currentText())
        settings.setValue("aery/provider/api", preset.get("api", "openai-completions"))
        settings.setValue("aery/provider/baseUrl", self.base_url.text().strip())
        settings.setValue("aery/provider/apiKey", self.api_key.text().strip())
        settings.setValue("aery/provider/model", self.model_combo.currentText().strip())

        # Derive context window from model name or use default
        model = self.model_combo.currentText().strip()
        context_window = MODEL_CONTEXT_WINDOWS.get(model, 128000)
        settings.setValue("aery/provider/contextWindow", context_window)
        settings.setValue("aery/provider/maxTokens", min(context_window // 4, 16384))

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
        preset = PRESETS.get(self.preset_combo.currentText(), {})
        api_type = preset.get("api", "openai-completions")

        if not base_url or not api_key or not model:
            self.test_status.setText("Fill in URL, key, and model first")
            self.test_status.setStyleSheet("color: #e6c866;")
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
                        self.test_status.setStyleSheet("color: #88c088;")
                    else:
                        self.test_status.setText(f"Unexpected: {str(body)[:80]}")
                        self.test_status.setStyleSheet("color: #e6c866;")
                elif api_type == "anthropic-messages":
                    if "content" in body:
                        self.test_status.setText("✓ Connected")
                        self.test_status.setStyleSheet("color: #88c088;")
                    else:
                        self.test_status.setText(f"Unexpected: {str(body)[:80]}")
                        self.test_status.setStyleSheet("color: #e6c866;")
                else:
                    if "model" in body or "choices" in body:
                        self.test_status.setText("✓ Connected")
                        self.test_status.setStyleSheet("color: #88c088;")
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
