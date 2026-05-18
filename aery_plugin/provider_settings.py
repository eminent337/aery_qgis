"""Provider configuration wizard for the Aery QGIS plugin.

Matches Aery Core's /provider flow:
  Screen 1 — Select authentication method
    (use a subscription / use an API key / Aery gateway)
  Screen 2 — Pick OAuth provider or API-key provider
  Screen 3 — Per-provider config dialog (varies by requirement)
  Backward-compat: AeryConfigDialog = AuthMethodWizard
"""

import json
import os
import threading
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QMessageBox,
    QSizePolicy,
)

from . import oauth_helper

# ── Palette ───────────────────────────────────────────────────────────────────
BG      = "#09090b"
SURFACE = "#18181b"
ACCENT  = "#8abeb7"
BORDER  = "#27272a"
TEXT    = "#e4e4e7"
DIM     = "#52525b"
GREEN   = "#4ade80"
RED     = "#f87171"
YELLOW  = "#facc15"

# ── Auth-method constants ─────────────────────────────────────────────────────
AUTH_OAUTH  = "oauth"
AUTH_APIKEY = "apikey"
AUTH_GATEWAY = "gateway"

# ── System font fallback ──────────────────────────────────────────────────────
F_S  = 10   # small labels, section headers
F_M  = 11   # body / normal
F_H  = 12   # headings / emphasis
F_B  = 14   # big title

def _fs(size: int) -> str:
    """Return a font-size CSS token clamped to sensible bounds."""
    return f"{max(size, 8)}px"


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _btn(text: str, fg: str = ACCENT, bg: str = "transparent",
         fixed: bool = True, w: int = 0) -> QPushButton:
    b = QPushButton(text)
    if fixed:
        b.setFixedHeight(26)
    if w:
        b.setFixedWidth(w)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton {{ background:{bg}; color:{fg}; border:1px solid {fg};"
        f" border-radius:2px; font-size:{_fs(F_M)}; font-weight:700; padding:0 8px; }}"
        f" QPushButton:hover {{ background:{fg}; color:{BG}; }}"
        f" QPushButton:disabled {{ opacity:0.4; }}"
    )
    return b


def _input(placeholder: str = "", width: int = 0) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    if width:
        e.setFixedWidth(width)
    e.setStyleSheet(
        f"QLineEdit {{ background:{BG}; color:{TEXT}; border:1px solid {BORDER};"
        f" border-radius:2px; padding:4px 8px; font-size:{_fs(F_M)}; }}"
        f" QLineEdit:focus {{ border-color:{ACCENT}; }}"
    )
    return e


def _section_hdr(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFixedHeight(28)
    lbl.setStyleSheet(
        f"font-size:{_fs(F_S)}; font-weight:800; color:{DIM}; letter-spacing:0.12em;"
        f" border:none; background:transparent;"
    )
    return lbl


def _apply_dialog_style(dlg: QDialog) -> None:
    dlg.setStyleSheet(
        f"QDialog, QWidget {{ background:{BG}; color:{TEXT}; font-family:'Inter',sans-serif; }}"
        f" QScrollBar:vertical {{ background:{SURFACE}; width:5px; border:none; }}"
        f" QScrollBar::handle:vertical {{ background:{BORDER}; border-radius:2px; min-height:20px; }}"
        f" QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Clickable list item used on auth-method and provider-list screens
# ══════════════════════════════════════════════════════════════════════════════

class _ListButton(QPushButton):
    """Dark two-row list button: title + optional subtitle. Signals: clicked(pid)."""

    def __init__(self, title: str, subtitle: str = "", pid: str = "",
                 bold: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pid = pid
        self._selected = False
        self._bold = bold

        self.setText(f"{title}\n{subtitle}" if subtitle else title)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(44 if subtitle else 34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._update_style()

    def set_selected(self, on: bool) -> None:
        self._selected = on
        self._update_style()

    def _update_style(self) -> None:
        bg       = "#0f2020" if self._selected else "transparent"
        border   = ACCENT   if self._selected else BORDER
        title_clr = ACCENT  if self._selected else (ACCENT if self._bold else TEXT)
        has_sub  = "\n" in self.text()
        sub_clr   = ACCENT if self._selected else DIM
        pad = "padding:7px 14px;" if has_sub else "padding:0 14px;"
        fw = 900 if self._selected else (800 if self._bold else 500)
        self.setStyleSheet(
            f"QPushButton {{"
            f"  background:{bg};"
            f"  border:1px solid {border};"
            f"  border-radius:3px;"
            f"  {pad}"
            f"  text-align:left;"
            f"  font-size:{_fs(F_H)};"
            f"  font-weight:{fw};"
            f"  color:{title_clr};"
            f"}}"
            f" QPushButton:hover {{"
            f"  background:{'#0f2020' if self._selected else SURFACE};"
            f"  border-color:{ACCENT};"
            f"  color:{ACCENT};"
            f"}}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Screen 1 — Authentication method list
# ══════════════════════════════════════════════════════════════════════════════

class AuthMethodList(QWidget):
    """Three-row list: Subscribe / API Key / Aery Gateway."""

    method_selected = pyqtSignal(str)  # AUTH_*

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(_section_hdr("SELECT AUTHENTICATION METHOD"))
        root.addSpacing(6)

        _items = [
            (AUTH_OAUTH,
             "Use a Subscription",
             "OAuth providers: Google, Anthropic, GitHub Copilot"),
            (AUTH_APIKEY,
             "Use an API Key",
             "Direct providers: OpenAI, Groq, DeepSeek, Mistral, and more"),
            (AUTH_GATEWAY,
             "Aery Gateway",
             "One key — all providers via aery-web.pages.dev"),
        ]

        self._btns: dict[str, _ListButton] = {}
        for method, title, sub in _items:
            btn = _ListButton(title, sub, pid=method, bold=True)
            btn.clicked.connect(lambda _, m=method: self.method_selected.emit(m))
            root.addWidget(btn)
            self._btns[method] = btn

        root.addStretch()


# ══════════════════════════════════════════════════════════════════════════════
# Screen 2 — OAuth provider list
# ══════════════════════════════════════════════════════════════════════════════

class ProviderOAuthList(QWidget):
    """All OAuth providers, each with a LAUNCH AUTH / LOGOUT button."""

    provider_selected = pyqtSignal(str)  # provider pid
    logout_requested   = pyqtSignal(str)
    status_changed     = pyqtSignal(str)  # provider pid

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pid_map: dict[str, str] = {}
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QLabel("SELECT OAUTH PROVIDER")
        hdr.setStyleSheet(
            f"font-size:{_fs(F_S)}; font-weight:800; color:{DIM}; letter-spacing:0.1em;"
            f" border:none; background:transparent;"
        )
        hdr.setFixedHeight(26)
        root.addWidget(hdr)
        root.addSpacing(6)

        self._body = QWidget()
        self._blay  = QVBoxLayout(self._body)
        self._blay.setContentsMargins(0, 0, 0, 0)
        self._blay.setSpacing(4)
        root.addWidget(self._body)
        root.addStretch()

        self._refresh()

    def _refresh(self) -> None:
        while self._blay.count():
            item = self._blay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._pid_map.clear()
        auth = oauth_helper._load_auth()

        for pid, cfg in oauth_helper.OAUTH_CONFIGS.items():
            creds = auth.get(pid, {})
            connected = bool(creds.get("access") or creds.get("accessToken")
                             or creds.get("refresh") or creds.get("refreshToken"))
            self._pid_map[pid] = cfg["name"]

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            dot = QLabel("●" if connected else "○")
            dot.setFixedSize(16, 16)
            dc = GREEN if connected else DIM
            dot.setStyleSheet(
                f"color:{dc}; font-size:{_fs(F_H)}; border:none; background:transparent;")
            row.addWidget(dot)

            nm = QLabel(cfg["name"])
            nm.setStyleSheet(
                f"font-size:{_fs(F_H)}; font-weight:600; color:{TEXT};"
                f" border:none; background:transparent;")
            row.addWidget(nm, 1)

            if connected:
                lo = _btn("LOGOUT", RED)
                lo.setFixedWidth(72)
                lo.clicked.connect(lambda _, p=pid: self.logout_requested.emit(p))
                row.addWidget(lo)
            else:
                login = _btn("LOGIN")
                login.setFixedWidth(72)
                login.clicked.connect(lambda _, p=pid: self.provider_selected.emit(p))
                row.addWidget(login)

            wrap = QWidget()
            wrap.setLayout(row)
            wrap.setStyleSheet(
                f"QWidget {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:3px; padding:4px 8px; }}"
                f" QWidget:hover {{ border-color:{ACCENT}; }}"
            )
            self._blay.addWidget(wrap)

        self._blay.addStretch()


# ══════════════════════════════════════════════════════════════════════════════
# Screen 2b — API-key provider list
# ══════════════════════════════════════════════════════════════════════════════

class ProviderApiKeyList(QWidget):
    """All API-key-capable providers. Clicking one opens an ApiKeyDialog."""

    provider_clicked = pyqtSignal(str)  # provider pid

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QLabel("SELECT PROVIDER")
        hdr.setStyleSheet(
            f"font-size:{_fs(F_S)}; font-weight:800; color:{DIM}; letter-spacing:0.1em;"
            f" border:none; background:transparent;")
        hdr.setFixedHeight(26)
        root.addWidget(hdr)
        root.addSpacing(6)

        # Scroll area for many providers
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        self._body = QWidget()
        self._blay = QVBoxLayout(self._body)
        self._blay.setContentsMargins(0, 0, 0, 0)
        self._blay.setSpacing(4)

        scroll.setWidget(self._body)
        root.addWidget(scroll, 1)

        self._refresh()

    def _refresh(self) -> None:
        while self._blay.count():
            item = self._blay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        auth = oauth_helper._load_auth()
        # Order: custom providers first, then gateway, then api_key providers

        # Custom providers from models.json
        custom_providers = oauth_helper.get_custom_providers()
        for cp in custom_providers:
            sub = f"{'✓ configured' if cp['connected'] else 'API key required'} · {len(cp.get('models', []))} models"
            btn = _ListButton(cp["name"], sub, pid=cp["id"])
            btn.clicked.connect(lambda _, p=cp["id"]: self.provider_clicked.emit(p))
            self._blay.addWidget(btn)

        # Custom OpenAI-compatible add button
        add_btn = _ListButton("＋ Custom OpenAI-compatible", "Add a new provider", pid="__add_custom__")
        add_btn.clicked.connect(lambda _, p="__add_custom__": self.provider_clicked.emit(p))
        self._blay.addWidget(add_btn)

        # Gateway shortcut
        gw_name = oauth_helper.API_PROVIDERS.get("aery-gateway", {}).get("name", "Aery Gateway")
        gw_creds = bool(auth.get("aery-gateway", {}).get("key"))
        sub = f"{'✓ connected' if gw_creds else 'not configured'} · {len(oauth_helper.API_PROVIDERS.get('aery-gateway', {}).get('models', []))} models"
        btn = _ListButton("Aery Gateway", sub, pid="aery-gateway")
        btn.clicked.connect(lambda _, p="aery-gateway": self.provider_clicked.emit(p))
        self._blay.addWidget(btn)

        for pid, cfg in oauth_helper.API_PROVIDERS.items():
            if pid == "aery-gateway":
                continue
            connected = bool(auth.get(pid, {}).get("key"))
            sub = f"{'✓ configured' if connected else 'API key required'} · {len(cfg.get('models', []))} models"
            btn = _ListButton(cfg["name"], sub, pid=pid)
            btn.clicked.connect(lambda _, p=pid: self.provider_clicked.emit(p))
            self._blay.addWidget(btn)

        self._blay.addStretch()


# ══════════════════════════════════════════════════════════════════════════════
# Per-provider API key configuration dialog
# ══════════════════════════════════════════════════════════════════════════════

def _dialog_auth_hint(pid: str) -> str:
    """Return the field variant for this provider's API-key dialog.

    Returns one of: minimal | account_id | base_url | gateway | aws | custom
    """
    if pid == "aery-gateway":
        return "gateway"
    if pid == "amazon-bedrock":
        return "aws"
    if pid in ("openai-compatible", "claude-local"):
        return "custom"
    cfg = oauth_helper.API_PROVIDERS.get(pid, {})
    if cfg.get("needs_account_id"):
        return "account_id"
    if cfg.get("needs_base_url"):
        return "base_url"
    return "minimal"


class CustomProviderDialog(QDialog):
    """Dialog to add a custom OpenAI-compatible provider."""

    _FONT_SIZE = "14px"  # Fixed font size for dialog labels

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("ADD CUSTOM PROVIDER")
        self.setModal(True)
        self.setMinimumWidth(460)
        _apply_dialog_style(self)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel("ADD CUSTOM OPENAI-COMPATIBLE PROVIDER")
        title.setStyleSheet(
            f"font-size:15px; font-weight:800; color:{ACCENT}; letter-spacing:0.1em;"
            f" border:none; background:transparent;")
        root.addWidget(title)

        sub = QLabel(
            "Enter the base URL, model ID, and API key for any "
            "OpenAI-compatible API endpoint."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"font-size:{self._FONT_SIZE}; color:{DIM}; border:none; background:transparent;")
        root.addWidget(sub)

        # Base URL
        row_b = QHBoxLayout()
        row_b.setSpacing(8)
        blbl = QLabel("Base URL")
        blbl.setFixedWidth(80)
        blbl.setStyleSheet(f"font-size:{self._FONT_SIZE}; color:{DIM}; border:none; background:transparent;")
        row_b.addWidget(blbl)
        self._url_inp = _input("https://api.example.com/v1", 280)
        row_b.addWidget(self._url_inp, 1)
        root.addLayout(row_b)

        # Model ID
        row_m = QHBoxLayout()
        row_m.setSpacing(8)
        mlbl = QLabel("Model ID")
        mlbl.setFixedWidth(80)
        mlbl.setStyleSheet(f"font-size:{self._FONT_SIZE}; color:{DIM}; border:none; background:transparent;")
        row_m.addWidget(mlbl)
        self._model_inp = _input("e.g. gpt-4o, llama-3.1-8b", 280)
        row_m.addWidget(self._model_inp, 1)
        root.addLayout(row_m)

        # API Key
        row_k = QHBoxLayout()
        row_k.setSpacing(8)
        klbl = QLabel("API Key")
        klbl.setFixedWidth(80)
        klbl.setStyleSheet(f"font-size:{self._FONT_SIZE}; color:{DIM}; border:none; background:transparent;")
        row_k.addWidget(klbl)
        self._key_inp = _input("sk-...", 280)
        self._key_inp.setEchoMode(QLineEdit.EchoMode.Password)
        row_k.addWidget(self._key_inp, 1)
        root.addLayout(row_k)

        root.addSpacing(8)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        cancel = _btn("CANCEL", DIM)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = _btn("ADD", ACCENT, ACCENT)
        save.setStyleSheet(save.styleSheet().replace(f"color:{ACCENT}", f"color:{BG}"))
        save.clicked.connect(self._save)
        btn_row.addWidget(save)
        root.addLayout(btn_row)

    def _save(self) -> None:
        base_url = self._url_inp.text().strip()
        model_id = self._model_inp.text().strip()
        api_key = self._key_inp.text().strip()
        if not base_url:
            QMessageBox.warning(self, "Missing URL", "Base URL is required.")
            return
        if not model_id:
            QMessageBox.warning(self, "Missing Model", "Model ID is required.")
            return
        if not api_key:
            QMessageBox.warning(self, "Missing Key", "API key is required.")
            return
        try:
            result = oauth_helper.save_custom_provider(base_url, model_id, api_key)
            QMessageBox.information(self, "Provider Added",
                                    f"Provider '{result['provider_id']}' added with model '{result['model_id']}'.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


class ApiKeyDialog(QDialog):
    """Per-provider API key / credential dialog."""

    _FONT_SIZE = "14px"  # Fixed font size for dialog labels

    def __init__(self, pid: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pid = pid
        self._cfg  = oauth_helper.API_PROVIDERS.get(pid, {})
        self._hint = _dialog_auth_hint(pid)

        title = f"LOGIN {self._cfg.get('name', pid).upper()}"
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        _apply_dialog_style(self)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        # Title
        title = QLabel(f"LOGIN {self._cfg.get('name', self._pid).upper()}")
        title.setStyleSheet(
            f"font-size:15px; font-weight:800; color:{ACCENT}; letter-spacing:0.1em;"
            f" border:none; background:transparent;")
        root.addWidget(title)

        # AWS banner
        if self._hint == "aws":
            banner = QLabel(
                "Amazon Bedrock credentials are managed via the AWS CLI.\n"
                "Configure with:  aws configure\n"
                "Then restart Aery."
            )
            banner.setWordWrap(True)
            banner.setStyleSheet(
                f"font-size:{self._FONT_SIZE}; color:{DIM}; border:1px solid {BORDER};"
                f" border-radius:3px; padding:10px 12px; background:{SURFACE};")
            root.addWidget(banner)
            root.addStretch()
            return

        # API key field (always present)
        row_k = QHBoxLayout()
        row_k.setSpacing(8)
        klbl = QLabel("API Key")
        klbl.setFixedWidth(80)
        klbl.setStyleSheet(f"font-size:{self._FONT_SIZE}; color:{DIM}; border:none; background:transparent;")
        row_k.addWidget(klbl)
        self._key_inp = _input("Enter API key…", 260)
        self._key_inp.setStyleSheet(
            self._key_inp.styleSheet().replace(_fs(F_M), self._FONT_SIZE))
        row_k.addWidget(self._key_inp, 1)
        root.addLayout(row_k)

        # Account ID
        if self._hint == "account_id":
            row_a = QHBoxLayout()
            row_a.setSpacing(8)
            albl = QLabel("Account ID")
            albl.setFixedWidth(80)
            albl.setStyleSheet(f"font-size:{self._FONT_SIZE}; color:{DIM}; border:none; background:transparent;")
            row_a.addWidget(albl)
            self._acct_inp = _input("Enter account ID…", 260)
            self._acct_inp.setStyleSheet(
                self._acct_inp.styleSheet().replace(_fs(F_M), self._FONT_SIZE))
            row_a.addWidget(self._acct_inp, 1)
            root.addLayout(row_a)

        # Base URL
        if self._hint in ("base_url", "custom"):
            row_b = QHBoxLayout()
            row_b.setSpacing(8)
            blbl = QLabel("Base URL")
            blbl.setFixedWidth(80)
            blbl.setStyleSheet(f"font-size:{self._FONT_SIZE}; color:{DIM}; border:none; background:transparent;")
            row_b.addWidget(blbl)
            default_url = self._cfg.get("base_url", "https://api.openai.com/v1")
            self._url_inp = _input(default_url, 260)
            self._url_inp.setStyleSheet(
                self._url_inp.styleSheet().replace(_fs(F_M), self._FONT_SIZE))
            self._url_inp.setText(default_url)
            row_b.addWidget(self._url_inp, 1)
            root.addLayout(row_b)

        # Model picker
        if self._hint in ("base_url", "custom"):
            row_m = QHBoxLayout()
            row_m.setSpacing(8)
            mlbl = QLabel("Model")
            mlbl.setFixedWidth(80)
            mlbl.setStyleSheet(f"font-size:{self._FONT_SIZE}; color:{DIM}; border:none; background:transparent;")
            row_m.addWidget(mlbl)
            self._model_combo = QComboBox()
            self._model_combo.setFixedWidth(260)
            self._model_combo.setStyleSheet(
                f"QComboBox {{ background:{BG}; color:{TEXT}; border:1px solid {BORDER};"
                f" border-radius:2px; padding:4px 6px; font-size:{self._FONT_SIZE}; }}"
                f" QComboBox:hover {{ border-color:{ACCENT}; }}"
                f" QComboBox QAbstractItemView {{ background:{SURFACE}; color:{TEXT}; selection-background-color:{ACCENT}; }}"
            )
            for mid, mlabel in self._cfg.get("models", []):
                self._model_combo.addItem(mlabel, mid)
            if self._model_combo.count() == 0:
                self._model_combo.addItem("(no models listed)", "")
            row_m.addWidget(self._model_combo, 1)
            root.addLayout(row_m)

        # Gateway key field label override
        if self._hint == "gateway":
            klbl.setText("Aery Key")
            self._key_inp.setPlaceholderText("Paste Aery key…")

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        cancel = _btn("CANCEL", DIM)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = _btn("SAVE", ACCENT, ACCENT)
        save.setStyleSheet(save.styleSheet().replace(f"color:{ACCENT}", f"color:{BG}"))
        save.clicked.connect(self._save)
        btn_row.addWidget(save)
        root.addLayout(btn_row)

    def _save(self) -> None:
        key = self._key_inp.text().strip()
        if not key:
            QMessageBox.warning(self, "Missing Key", "API key is required.")
            return

        if self._hint == "account_id":
            acct = getattr(self, "_acct_inp", None)
            account_id = acct.text().strip() if acct else ""
            oauth_helper.save_api_key(self._pid, key, account_id=account_id)
        elif self._hint in ("base_url", "custom"):
            base_url = self._url_inp.text().strip()
            selected_model = ""
            mc = getattr(self, "_model_combo", None)
            if mc and mc.currentIndex() >= 0:
                selected_model = mc.currentData() or ""
            oauth_helper.save_api_key(self._pid, key, base_url=base_url)
            if selected_model:
                oauth_helper.set_active_provider(self._pid, selected_model)
        elif self._hint == "gateway":
            oauth_helper.save_gateway_key(key)
        else:
            oauth_helper.save_api_key(self._pid, key)

        self.accept()


# ══════════════════════════════════════════════════════════════════════════════
# Model Switcher dialog
# ══════════════════════════════════════════════════════════════════════════════

class ModelSwitcherDialog(QDialog):
    """Floating model picker — per-provider sections with model lists."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("MODEL SELECTION")
        self.setFixedSize(490, 580)
        self.setModal(True)
        _apply_dialog_style(self)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        # Active provider badge
        active = oauth_helper.get_active_provider()
        hdr_txt = (f"Active: {active['name']}  {active.get('model', '')}"
                   if active else "No active provider")
        hdr = QLabel(hdr_txt)
        hdr.setStyleSheet(
            f"font-size:{_fs(F_M)}; font-weight:700; color:{ACCENT};"
            f" border:none; background:{SURFACE}; border-radius:3px; padding:5px 8px;")
        hdr.setFixedHeight(26)
        root.addWidget(hdr)
        root.addSpacing(8)

        # Provider sections
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        body = QWidget()
        blay = QVBoxLayout(body)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(2)

        active_pid   = active["id"]   if active else ""
        active_model = active.get("model", "") if active else ""

        auth = oauth_helper._load_auth()

        # OAuth providers with models
        for pid, cfg in oauth_helper.OAUTH_CONFIGS.items():
            models = _oauth_models(pid)
            if not models:
                continue
            creds    = auth.get(pid, {})
            connected = bool(creds.get("access") or creds.get("accessToken")
                             or creds.get("refresh") or creds.get("refreshToken"))
            if not connected:
                continue
            blay.addWidget(self._provider_section(cfg["name"], pid, models,
                                                  pid == active_pid, active_model))

        # API key providers with models and hydra
        for pid, cfg in oauth_helper.API_PROVIDERS.items():
            if pid == "aery-gateway":
                continue
            models = cfg.get("models", [])
            if not models:
                continue
            creds     = auth.get(pid, {})
            connected = bool(creds.get("key"))
            if not connected:
                continue
            blay.addWidget(self._provider_section(cfg["name"], pid, models,
                                                  pid == active_pid, active_model))

        # Aery gateway
        gw = oauth_helper.API_PROVIDERS.get("aery-gateway", {})
        gw_models = gw.get("models", [])
        gw_creds  = bool(auth.get("aery-gateway", {}).get("key"))
        if gw_creds and gw_models:
            blay.addWidget(self._provider_section(
                "Aery Gateway", "aery-gateway", gw_models,
                active_pid == "aery-gateway", active_model))

        blay.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def _provider_section(self, name: str, pid: str, models: list,
                          is_active_pid: bool, active_model: str) -> QWidget:
        cont = QWidget()
        cv   = QVBoxLayout(cont)
        cv.setContentsMargins(0, 2, 0, 2)
        cv.setSpacing(0)

        # Section title
        lbl = QLabel(name)
        lbl.setStyleSheet(
            f"font-size:{_fs(F_S)}; font-weight:800; color:{DIM}; letter-spacing:0.08em;"
            f" border:none; background:transparent;")
        lbl.setFixedHeight(20)
        cv.addWidget(lbl)

        for mid, mlabel in models:
            is_active = is_active_pid and mid == active_model
            btn = QPushButton(mlabel)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(22)
            fw = 900 if is_active else 400
            fc = ACCENT if is_active else TEXT
            prefix = "● " if is_active else "  "
            btn.setText(f"{prefix}{mlabel}")
            btn.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{fc}; border:none;"
                f" border-radius:2px; font-size:{_fs(F_M)}; font-weight:{fw};"
                f" padding:0 8px 0 16px; text-align:left; }}"
                f" QPushButton:hover {{ background:{SURFACE}; color:{ACCENT}; }}"
            )
            btn.clicked.connect(lambda _, p=pid, m=mid, dlg=self: self._pick(p, m, dlg))
            cv.addWidget(btn)

        return cont

    @staticmethod
    def _pick(pid: str, mid: str, dlg: QDialog) -> None:
        oauth_helper.set_active_provider(pid, mid)
        dlg.accept()
# Use canonical _oauth_models from oauth_helper — single source of truth
_oauth_models = oauth_helper._oauth_models


# ══════════════════════════════════════════════════════════════════════════════
# Scopes / enabled-models dialog
# ══════════════════════════════════════════════════════════════════════════════

class ScopesDialog(QDialog):
    """Manage enabledModels[] — checkboxes per configured provider/model."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("SCOPES MODEL")
        self.setFixedSize(490, 520)
        self.setModal(True)
        _apply_dialog_style(self)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        sub = QLabel(
            "Enabled models are visible to the auto-router. "
            "Uncheck to hide a model."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"font-size:{_fs(F_M)}; color:{DIM}; border:none; background:transparent;")
        root.addWidget(sub)
        root.addSpacing(4)

        # Bulk controls
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        enable_all = _btn("ENABLE ALL", DIM)
        enable_all.clicked.connect(self._enable_all)
        ctrl.addWidget(enable_all)
        disable_all = _btn("DISABLE ALL", DIM)
        disable_all.clicked.connect(self._disable_all)
        ctrl.addWidget(disable_all)
        ctrl.addStretch()
        root.addLayout(ctrl)
        root.addSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        body = QWidget()
        blay = QVBoxLayout(body)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(4)

        auth     = oauth_helper._load_auth()
        settings = {}
        sp       = os.path.join(oauth_helper.AGENT_DIR, "settings.json")
        try:
            with open(sp) as f:
                settings = json.load(f)
        except Exception:
            pass
        enabled: set = set(settings.get("enabledModels", []))

        def _add_group(title: str, entries: list[tuple[str, str]], prefix: str = "") -> None:
            if not entries:
                return
            lbl = QLabel(title)
            lbl.setStyleSheet(
                f"font-size:{_fs(F_S)}; font-weight:800; color:{DIM}; letter-spacing:0.08em;"
                f" border:none; background:transparent;")
            lbl.setFixedHeight(18)
            blay.addWidget(lbl)
            for mid, mlabel in entries:
                key = f"{prefix}{mid}" if prefix else mid
                cb  = QCheckBox(mlabel)
                cb.setChecked(key in enabled if enabled else True)
                cb.setStyleSheet(
                    f"QCheckBox {{ color:{TEXT}; font-size:{_fs(F_M)}; spacing:6px; }}"
                    f" QCheckBox::indicator {{ width:14px; height:14px; border:1px solid {BORDER}; border-radius:2px; background:{BG}; }}"
                    f" QCheckBox::indicator:checked {{ background:{ACCENT}; border-color:{ACCENT}; }}"
                )
                self._checkboxes[key] = cb
                blay.addWidget(cb)

        # OAuth providers
        for pid, cfg in oauth_helper.OAUTH_CONFIGS.items():
            models = _oauth_models(pid)
            creds  = auth.get(pid, {})
            if creds.get("access") or creds.get("accessToken") or creds.get("refresh"):
                _add_group(cfg["name"], models, pid + "/")

        # API key providers
        for pid, cfg in oauth_helper.API_PROVIDERS.items():
            if pid == "aery-gateway":
                continue
            models = cfg.get("models", [])
            creds  = auth.get(pid, {})
            if creds.get("key"):
                _add_group(cfg["name"], models, pid + "/")

        # Gateway
        gw = auth.get("aery-gateway", {})
        if gw.get("key"):
            _add_group("Aery Gateway",
                       oauth_helper.API_PROVIDERS.get("aery-gateway", {}).get("models", []))

        blay.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Footer buttons
        ftr = QHBoxLayout()
        ftr.setSpacing(8)
        ftr.addStretch()
        cancel = _btn("CANCEL", DIM)
        cancel.clicked.connect(self.reject)
        ftr.addWidget(cancel)
        save = _btn("SAVE", ACCENT, ACCENT)
        save.setStyleSheet(save.styleSheet().replace(f"color:{ACCENT}", f"color:{BG}"))
        save.clicked.connect(self._save)
        ftr.addWidget(save)
        root.addLayout(ftr)

    def _enable_all(self) -> None:
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def _disable_all(self) -> None:
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def _save(self) -> None:
        sp = os.path.join(oauth_helper.AGENT_DIR, "settings.json")
        settings: dict = {}
        try:
            with open(sp) as f:
                settings = json.load(f)
        except Exception:
            pass

        enabled = [k for k, cb in self._checkboxes.items() if cb.isChecked()]
        if enabled:
            settings["enabledModels"] = enabled
        else:
            settings.pop("enabledModels", None)

        with open(sp, "w") as f:
            json.dump(settings, f, indent=2)
        self.accept()


# ══════════════════════════════════════════════════════════════════════════════
# OAuth login thread worker
# ══════════════════════════════════════════════════════════════════════════════

def _run_login(pid: str, on_done) -> None:
    """Run oauth_helper.login_provider in a worker thread; call on_done(None | str)."""
    try:
        ok = oauth_helper.login_provider(pid)
        QTimer.singleShot(0, lambda: on_done(None if ok else "Login cancelled or failed"))
    except RuntimeError as exc:
        err_msg = str(exc)
        QTimer.singleShot(0, lambda: on_done(err_msg))
    except Exception as exc:
        err_msg = str(exc)
        QTimer.singleShot(0, lambda: on_done(err_msg))


# ══════════════════════════════════════════════════════════════════════════════
# GitHub Copilot device-flow sub-dialog
# ══════════════════════════════════════════════════════════════════════════════

class _DeviceFlowDialog(QDialog):
    """Shows GitHub Copilot device-code; polls until authorised or timeout."""

    def __init__(self, pid: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pid = pid
        self.setWindowTitle(f"LOGIN {oauth_helper.OAUTH_CONFIGS[pid]['name'].upper()}")
        self.setFixedWidth(400)
        self.setModal(True)
        _apply_dialog_style(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        self._code_lbl = QLabel("Opening browser…")
        self._code_lbl.setWordWrap(True)
        self._code_lbl.setStyleSheet(
            f"font-size:12px; font-weight:700; color:{ACCENT};"
            f" border:none; background:{SURFACE}; border-radius:4px; padding:12px;")
        root.addWidget(self._code_lbl)

        hint = QLabel(
            "Enter the code above at github.com/login/device\n"
            "This window will close automatically when complete."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"font-size:{_fs(F_M)}; color:{DIM}; border:none; background:transparent;")
        root.addWidget(hint)

        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self) -> None:
        QTimer.singleShot(0, lambda: self._code_lbl.setText(
            "Opening browser — enter code at github.com/login/device…"))
        try:
            cfg = oauth_helper.OAUTH_CONFIGS.get(self._pid, {})
            ok = oauth_helper._device_flow_login(self._pid, cfg)
        except Exception as e:
            err_msg = str(e)
            QTimer.singleShot(0, lambda: self._fail(err_msg))
            return
        QTimer.singleShot(0, lambda: self._done(ok))

    def _fail(self, msg: str) -> None:
        QMessageBox.critical(self, "Login Failed", msg)
        self.reject()

    def _done(self, ok: bool) -> None:
        if ok:
            self.accept()
        else:
            QMessageBox.warning(self, "Login Failed", "Device flow timed out or was cancelled.")
            self.reject()


# ══════════════════════════════════════════════════════════════════════════════
# Top-level Wizard
# ══════════════════════════════════════════════════════════════════════════════

class AuthMethodWizard(QDialog):
    """Aery provider configuration wizard.

    Screen stack:
      _page_auth_method  →  _page_oauth  |  _page_apikey  |  _gateway_page
      per-provider dialogs open via ApiKeyDialog / OAuth link in browser

    Backward-compat alias: AeryConfigDialog = AuthMethodWizard
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(490, 560)
        self.setModal(True)
        self.setWindowTitle("AERY — CONFIGURE")
        _apply_dialog_style(self)

        self._current_screen: Optional[QWidget] = None
        self._build_chrome()
        self._show_auth_method()

    # ── Chrome ─────────────────────────────────────────────────────────────────

    def _build_chrome(self) -> None:
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # Header
        hdr = QFrame()
        hdr.setFixedHeight(40)
        hdr.setStyleSheet(f"background:{SURFACE}; border-bottom:1px solid {BORDER};")
        hl  = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 0, 12, 0)
        self._title_lbl = QLabel("AERY — CONFIGURE")
        self._title_lbl.setStyleSheet(
            f"font-size:{_fs(F_M)}; font-weight:800; color:{DIM};"
            f" letter-spacing:0.1em; border:none; background:transparent;")
        hl.addWidget(self._title_lbl)
        hl.addStretch()
        self._back_btn = _btn("← BACK", DIM)
        self._back_btn.setVisible(False)
        self._back_btn.clicked.connect(self._go_back)
        hl.addWidget(self._back_btn)
        self._root.addWidget(hdr)

        # Content area
        self._content = QWidget()
        self._clay    = QVBoxLayout(self._content)
        self._clay.setContentsMargins(12, 10, 12, 10)
        self._clay.setSpacing(0)
        self._root.addWidget(self._content, 1)

        # Footer
        ftr = QFrame()
        ftr.setFixedHeight(38)
        ftr.setStyleSheet(f"background:{SURFACE}; border-top:1px solid {BORDER};")
        fl  = QHBoxLayout(ftr)
        fl.setContentsMargins(16, 0, 12, 0)
        fl.addStretch()
        self._action_btn = _btn("", DIM)
        self._action_btn.setVisible(False)
        fl.addWidget(self._action_btn)
        self._root.addWidget(ftr)

    # ── Page switching ─────────────────────────────────────────────────────────

    def _set_page(self, widget: QWidget, title: str = "",
                  show_back: bool = False,
                  action_text: str = "", action_cb=None) -> None:
        # Clear previous
        w = self._content
        while self._clay.count():
            it = self._clay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        self._current_screen = widget
        self._clay.addWidget(widget, 1)

        self._title_lbl.setText(title or getattr(widget, "_screen_title", "AERY — CONFIGURE"))
        self._back_btn.setVisible(show_back)

        if action_text and action_cb:
            self._action_btn.setText(action_text)
            self._action_btn.setVisible(True)
            try:
                self._action_btn.clicked.disconnect()
            except TypeError:
                pass
            self._action_btn.clicked.connect(action_cb)
        else:
            self._action_btn.setVisible(False)

    def _go_back(self) -> None:
        self._show_auth_method()

    # ── Screen 1: auth method list ─────────────────────────────────────────────

    def _show_auth_method(self) -> None:
        self._auth_list = AuthMethodList()
        self._auth_list.method_selected.connect(self._on_method_selected)
        self._set_page(self._auth_list, "AERY — CONFIGURE", show_back=False)
        # Also populate provider list for API key screen lazily
        self._api_list: Optional[ProviderApiKeyList] = None

    def _on_method_selected(self, method: str) -> None:
        if method == AUTH_OAUTH:
            self._show_oauth()
        elif method == AUTH_APIKEY:
            self._show_apikey()
        elif method == AUTH_GATEWAY:
            self._show_gateway()

    # ── Screen 2a: OAuth providers ─────────────────────────────────────────────

    def _show_oauth(self) -> None:
        self._oauth_list = ProviderOAuthList()
        self._oauth_list.provider_selected.connect(self._on_oauth_click)
        self._oauth_list.logout_requested.connect(self._on_oauth_logout)
        self._oauth_list.status_changed.connect(self._on_oauth_status)
        self._set_page(self._oauth_list, "SELECT OAUTH PROVIDER", show_back=True)

    def _on_oauth_click(self, pid: str) -> None:
        cfg = oauth_helper.OAUTH_CONFIGS.get(pid, {})
        if cfg.get("device_flow"):
            dlg = _DeviceFlowDialog(pid, self)
            dlg.exec()
        else:
            self._set_busy(f"Opening browser for {pid}…")
            def done(err):
                self._set_busy("")
                self._oauth_list._refresh()
                if err:
                    QMessageBox.warning(self, "Login Failed", err)
                else:
                    QMessageBox.information(self, "Login Success",
                                            f"{oauth_helper.OAUTH_CONFIGS[pid]['name']} connected.")
            threading.Thread(target=_run_login, args=(pid, done), daemon=True).start()

    def _on_oauth_logout(self, pid: str) -> None:
        oauth_helper.logout_provider(pid)
        self._oauth_list._refresh()

    def _on_oauth_status(self, pid: str) -> None:
        self._oauth_list._refresh()

    # ── Screen 2b: API-key providers ───────────────────────────────────────────

    def _show_apikey(self) -> None:
        if self._api_list is None:
            self._api_list = ProviderApiKeyList()
            self._api_list.provider_clicked.connect(self._on_apikey_click)
        self._set_page(self._api_list, "SELECT PROVIDER", show_back=True)

    def _on_apikey_click(self, pid: str) -> None:
        if pid == "__add_custom__":
            dlg = CustomProviderDialog(self)
            if dlg.exec():
                if self._api_list:
                    self._api_list._refresh()
                self._show_auth_method()
        else:
            self._show_apikey_dialog(pid)

    def _show_apikey_dialog(self, pid: str) -> None:
        hint = _dialog_auth_hint(pid)
        if hint == "aws":
            # Banner-only: show OK/cancel
            dlg = ApiKeyDialog(pid, self)
            dlg.exec()
            if self._api_list:
                self._api_list._refresh()
            return
        dlg = ApiKeyDialog(pid, self)
        if dlg.exec():
            if self._api_list:
                self._api_list._refresh()
            # Return to auth method screen on save (matches Aery: flow ends on save)
            self._show_auth_method()

    # ── Screen 2c: Aery Gateway ────────────────────────────────────────────────

    def _show_gateway(self) -> None:
        self._gateway_page = GatewayPage()
        self._gateway_page.saved.connect(self._on_gateway_saved)
        self._set_page(self._gateway_page, "AERY GATEWAY", show_back=True)

    def _on_gateway_saved(self) -> None:
        QMessageBox.information(self, "Gateway Key Saved",
                                "Aery Gateway key saved. Restart Aery to pick up the new credentials.")
        self._show_auth_method()

    # ── Busy indicator ─────────────────────────────────────────────────────────

    def _set_busy(self, msg: str) -> None:
        self._action_btn.setText(msg)
        self._action_btn.setVisible(bool(msg))
        self._action_btn.setEnabled(not msg)


# ══════════════════════════════════════════════════════════════════════════════
# Gateway key page
# ══════════════════════════════════════════════════════════════════════════════

class GatewayPage(QWidget):
    """Simple Aery gateway key entry."""

    _FONT_SIZE = "14px"
    saved = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        sub = QLabel(
            "Get a key at aery-web.pages.dev\n"
            "Paste it below to enable all providers through the gateway."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"font-size:{self._FONT_SIZE}; color:{DIM}; border:none; background:transparent;")
        root.addWidget(sub)

        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel("Aery Key")
        lbl.setFixedWidth(70)
        lbl.setStyleSheet(f"font-size:{self._FONT_SIZE}; color:{DIM}; border:none; background:transparent;")
        row.addWidget(lbl)
        self._key_inp = _input("Paste key…", 260)
        self._key_inp.setStyleSheet(
            self._key_inp.styleSheet().replace(_fs(F_M), self._FONT_SIZE))
        row.addWidget(self._key_inp, 1)
        root.addLayout(row)

        link = QLabel(
            '<a href="https://aery-web.pages.dev" style="color:#8abeb7;">'
            'Open aery-web.pages.dev →</a>')
        link.setOpenExternalLinks(True)
        link.setStyleSheet(f"font-size:13px; color:{ACCENT}; border:none; background:transparent;")
        root.addWidget(link)
        root.addSpacing(12)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = _btn("CANCEL", DIM)
        cancel.clicked.connect(lambda: None)
        btn_row.addWidget(cancel)
        save = _btn("SAVE", ACCENT, ACCENT)
        save.setStyleSheet(save.styleSheet().replace(f"color:{ACCENT}", f"color:{BG}"))
        save.clicked.connect(self._save)
        btn_row.addWidget(save)
        root.addLayout(btn_row)

    def _save(self) -> None:
        key = self._key_inp.text().strip()
        if not key:
            return
        oauth_helper.save_gateway_key(key)
        self.saved.emit()


# ══════════════════════════════════════════════════════════════════════════════
# Public shortcuts used by chat_panel.py settings menu
# ══════════════════════════════════════════════════════════════════════════════

def show_model_switcher(parent: Optional[QWidget] = None) -> None:
    dlg = ModelSwitcherDialog(parent)
    dlg.exec()


def show_scopes_dialog(parent: Optional[QWidget] = None) -> None:
    dlg = ScopesDialog(parent)
    dlg.exec()


ModelSwitcherDialog_public = ModelSwitcherDialog
ScopesDialog_public        = ScopesDialog


# ══════════════════════════════════════════════════════════════════════════════
# Backward-compat aliases
# ══════════════════════════════════════════════════════════════════════════════

AeryConfigDialog    = AuthMethodWizard
ProviderSetupWizard = AuthMethodWizard
