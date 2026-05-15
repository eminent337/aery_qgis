"""Provider configuration dialog for the Aery QGIS plugin."""

import threading
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QWidget, QMessageBox, QComboBox,
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


def _btn(text: str, fg: str = ACCENT, bg: str = "transparent") -> QPushButton:
    b = QPushButton(text)
    b.setFixedHeight(24)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton {{ background:{bg}; color:{fg}; border:1px solid {fg};"
        f" border-radius:2px; font-size:8px; font-weight:700; padding:0 8px; }}"
        f" QPushButton:hover {{ background:{fg}; color:{BG}; }}"
        f" QPushButton:disabled {{ opacity:0.4; }}"
    )
    return b


def _input(placeholder: str = "", width: int = 120) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFixedWidth(width)
    e.setStyleSheet(
        f"QLineEdit {{ background:{BG}; color:{TEXT}; border:1px solid {BORDER};"
        f" border-radius:2px; padding:3px 6px; font-size:9px; }}"
        f" QLineEdit:focus {{ border-color:{ACCENT}; }}"
    )
    return e


class ProviderRow(QFrame):
    """One provider row: dot · name · tag · [inputs] · buttons."""

    def __init__(self, provider: dict, is_active: bool, parent: "ProviderSettingsDialog"):
        super().__init__()
        self._pid = provider["id"]
        self._dlg = parent
        self._testing = False

        border = ACCENT if is_active else BORDER
        bg = "#0f1f1e" if is_active else SURFACE
        self.setStyleSheet(
            f"QFrame {{ background:{bg}; border:1px solid {border};"
            f" border-radius:3px; }}"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(7)

        # Status dot
        self._dot = QLabel("○")
        self._dot.setFixedSize(12, 12)
        self._dot.setStyleSheet(f"color:{DIM}; font-size:11px; border:none; background:transparent;")
        row.addWidget(self._dot)

        # Name
        name = QLabel(provider["name"])
        name.setStyleSheet(f"font-size:11px; font-weight:600; color:{TEXT}; border:none; background:transparent;")
        row.addWidget(name)

        # Type tag
        tag_text = {"gateway": "GATEWAY", "oauth": "OAUTH", "api_key": "API KEY"}.get(provider["type"], "")
        tag = QLabel(tag_text)
        tag.setStyleSheet(f"font-size:7px; font-weight:700; color:{DIM}; border:none; background:transparent; letter-spacing:0.05em;")
        row.addWidget(tag)

        row.addStretch()

        ptype = provider["type"]
        has = provider["has_creds"]

        if ptype == "gateway":
            self._build_gateway(row, provider, has)
        elif ptype == "oauth":
            self._build_oauth(row, provider, has)
        else:
            self._build_apikey(row, provider, has)

        # Model combo (configured providers)
        if has and provider.get("model_names"):
            self._combo = QComboBox()
            self._combo.setFixedWidth(160)
            self._combo.setStyleSheet(
                f"QComboBox {{ background:{BG}; color:{TEXT}; border:1px solid {BORDER};"
                f" border-radius:2px; padding:2px 4px; font-size:9px; }}"
                f" QComboBox:hover {{ border-color:{ACCENT}; }}"
                f" QComboBox QAbstractItemView {{ background:{SURFACE}; color:{TEXT}; selection-background-color:{ACCENT}; }}"
            )
            for mid, mlabel in provider["model_names"]:
                self._combo.addItem(mlabel, mid)
            active = parent._active
            if is_active and active:
                for i in range(self._combo.count()):
                    if self._combo.itemData(i) == active.get("model"):
                        self._combo.setCurrentIndex(i)
                        break
            self._combo.currentIndexChanged.connect(self._on_model_change)
            row.addWidget(self._combo)
        else:
            self._combo = None

        # TEST button
        self._test_btn = _btn("TEST")
        self._test_btn.clicked.connect(self._test)
        row.addWidget(self._test_btn)

        # USE button (configured, not active)
        if has and not is_active:
            use_btn = _btn("USE", YELLOW)
            use_btn.clicked.connect(lambda: self._dlg._activate(self._pid))
            row.addWidget(use_btn)

    # ── Section builders ──────────────────────────────────────────────────────

    def _build_gateway(self, row: QHBoxLayout, provider: dict, has: bool):
        if not has:
            self._key_inp = _input("Aery key…", 140)
            row.addWidget(self._key_inp)
            save = _btn("SAVE", ACCENT, ACCENT)
            save.setStyleSheet(save.styleSheet().replace(f"color:{ACCENT}", f"color:{BG}"))
            save.clicked.connect(self._save_gateway)
            row.addWidget(save)
        else:
            rm = _btn("REMOVE", RED)
            rm.clicked.connect(lambda: self._dlg._remove(self._pid))
            row.addWidget(rm)

    def _build_oauth(self, row: QHBoxLayout, provider: dict, has: bool):
        if not has:
            login = _btn("LOGIN")
            login.clicked.connect(lambda: self._dlg._login(self._pid))
            row.addWidget(login)
        else:
            lo = _btn("LOGOUT", RED)
            lo.clicked.connect(lambda: self._dlg._remove(self._pid))
            row.addWidget(lo)

    def _build_apikey(self, row: QHBoxLayout, provider: dict, has: bool):
        if not has:
            self._key_inp = _input("API key…", 130)
            row.addWidget(self._key_inp)
            if provider.get("needs_account_id"):
                self._acct_inp = _input("Account ID", 90)
                row.addWidget(self._acct_inp)
            else:
                self._acct_inp = None
            save = _btn("SAVE", ACCENT, ACCENT)
            save.setStyleSheet(save.styleSheet().replace(f"color:{ACCENT}", f"color:{BG}"))
            save.clicked.connect(self._save_key)
            row.addWidget(save)
        else:
            rm = _btn("REMOVE", RED)
            rm.clicked.connect(lambda: self._dlg._remove(self._pid))
            row.addWidget(rm)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _save_gateway(self):
        key = self._key_inp.text().strip()
        if key:
            oauth_helper.save_gateway_key(key)
            self._dlg._refresh()

    def _save_key(self):
        key = self._key_inp.text().strip()
        if not key:
            return
        acct = getattr(self, "_acct_inp", None)
        account_id = acct.text().strip() if acct else ""
        oauth_helper.save_api_key(self._pid, key, account_id=account_id)
        self._dlg._refresh()

    def _on_model_change(self, idx: int):
        if self._combo:
            mid = self._combo.itemData(idx)
            if mid:
                oauth_helper.set_active_provider(self._pid, mid)
                self._dlg._active = oauth_helper.get_active_provider()

    def _test(self):
        if self._testing:
            return
        self._testing = True
        self._test_btn.setEnabled(False)
        self._test_btn.setText("…")
        self._dot.setStyleSheet(f"color:{YELLOW}; font-size:11px; border:none; background:transparent;")

        def worker():
            err = oauth_helper.test_provider_connection(self._pid)
            QTimer.singleShot(0, lambda: self._on_test_done(err))

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, err: Optional[str]):
        self._testing = False
        self._test_btn.setEnabled(True)
        self._test_btn.setText("TEST")
        if err is None:
            self._dot.setStyleSheet(f"color:{GREEN}; font-size:11px; border:none; background:transparent;")
            self._test_btn.setToolTip("OK")
        else:
            self._dot.setStyleSheet(f"color:{RED}; font-size:11px; border:none; background:transparent;")
            self._test_btn.setToolTip(err[:200])

    def set_dot(self, color: str):
        self._dot.setStyleSheet(f"color:{color}; font-size:11px; border:none; background:transparent;")


class ProviderSettingsDialog(QDialog):
    """AERY — PROVIDERS dialog. Fixed 560×680, dark theme."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("AERY — PROVIDERS")
        self.setFixedSize(560, 680)
        self.setStyleSheet(
            f"QDialog, QWidget {{ background:{BG}; color:{TEXT}; font-family:'Inter',sans-serif; }}"
            f" QScrollBar:vertical {{ background:{SURFACE}; width:5px; border:none; }}"
            f" QScrollBar::handle:vertical {{ background:{BORDER}; border-radius:2px; min-height:20px; }}"
            f" QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}"
        )
        self._active: Optional[dict] = oauth_helper.get_active_provider()
        self._rows: list[ProviderRow] = []
        self._build_chrome()
        self._refresh()

    # ── Chrome (header + scroll + footer) ────────────────────────────────────

    def _build_chrome(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        hdr = QFrame()
        hdr.setFixedHeight(52)
        hdr.setStyleSheet(f"background:{SURFACE}; border-bottom:1px solid {BORDER};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(20, 0, 16, 0)
        title = QLabel("AERY — PROVIDERS")
        title.setStyleSheet(f"font-size:11px; font-weight:800; letter-spacing:0.12em; color:{DIM}; border:none; background:transparent;")
        hl.addWidget(title)
        hl.addSpacing(12)
        self._badge = QLabel("")
        self._badge.setStyleSheet(f"font-size:9px; font-weight:700; color:{ACCENT}; border:none; background:transparent;")
        hl.addWidget(self._badge)
        hl.addStretch()
        root.addWidget(hdr)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(20, 16, 20, 16)
        self._body_layout.setSpacing(0)
        scroll.setWidget(self._body)
        root.addWidget(scroll, 1)

        # Footer
        ftr = QFrame()
        ftr.setFixedHeight(48)
        ftr.setStyleSheet(f"background:{SURFACE}; border-top:1px solid {BORDER};")
        fl = QHBoxLayout(ftr)
        fl.setContentsMargins(16, 0, 16, 0)
        fl.addStretch()
        test_all = _btn("TEST ALL")
        test_all.clicked.connect(self._test_all)
        fl.addWidget(test_all)
        fl.addSpacing(8)
        done = QPushButton("DONE")
        done.setFixedHeight(28)
        done.setCursor(Qt.CursorShape.PointingHandCursor)
        done.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:{BG}; border:none; border-radius:2px;"
            f" font-size:9px; font-weight:900; padding:0 22px; }}"
            f" QPushButton:hover {{ background:#9ecec7; }}"
        )
        done.clicked.connect(self.accept)
        fl.addWidget(done)
        root.addWidget(ftr)

    # ── Section header ────────────────────────────────────────────────────────

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedHeight(30)
        lbl.setStyleSheet(
            f"font-size:9px; font-weight:800; color:{ACCENT}; letter-spacing:0.1em;"
            f" border:none; background:transparent;"
        )
        return lbl

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _refresh(self):
        self._active = oauth_helper.get_active_provider()
        self._rows.clear()

        # Clear body
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        providers = oauth_helper.get_all_providers()
        active_id = self._active["id"] if self._active else None

        # Update badge
        if self._active:
            model = self._active.get("model", "")
            self._badge.setText(f"● {self._active['name']}  {model}".strip())
        else:
            self._badge.setText("● no provider selected")

        # ── AERY GATEWAY section ──
        gw = next((p for p in providers if p["id"] == "aery-gateway"), None)
        if gw:
            self._body_layout.addWidget(self._section_label("AERY GATEWAY"))
            sub = QLabel("One key · all providers")
            sub.setStyleSheet(f"font-size:8px; color:{DIM}; border:none; background:transparent;")
            self._body_layout.addWidget(sub)
            self._body_layout.addSpacing(4)
            row = ProviderRow(gw, active_id == "aery-gateway", self)
            self._rows.append(row)
            self._body_layout.addWidget(row)
            # "Get key" link
            link = QLabel('<a href="https://aery-web.pages.dev" style="color:#8abeb7;">Get key at aery-web.pages.dev</a>')
            link.setOpenExternalLinks(True)
            link.setStyleSheet(f"font-size:8px; border:none; background:transparent;")
            self._body_layout.addWidget(link)
            self._body_layout.addSpacing(12)

        # ── SUBSCRIPTION (OAuth) section ──
        oauth_list = [p for p in providers if p["type"] == "oauth"]
        if oauth_list:
            self._body_layout.addWidget(self._section_label("SUBSCRIPTION  (OAuth)"))
            self._body_layout.addSpacing(4)
            for p in oauth_list:
                row = ProviderRow(p, active_id == p["id"], self)
                self._rows.append(row)
                self._body_layout.addWidget(row)
                self._body_layout.addSpacing(4)
            self._body_layout.addSpacing(8)

        # ── API KEY PROVIDERS section ──
        key_list = [p for p in providers if p["type"] == "api_key"]
        if key_list:
            self._body_layout.addWidget(self._section_label("API KEY PROVIDERS"))
            self._body_layout.addSpacing(4)
            for p in key_list:
                row = ProviderRow(p, active_id == p["id"], self)
                self._rows.append(row)
                self._body_layout.addWidget(row)
                self._body_layout.addSpacing(4)

        self._body_layout.addStretch()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _activate(self, pid: str):
        models = []
        for p in oauth_helper.get_all_providers():
            if p["id"] == pid:
                models = p.get("model_names", [])
                break
        model = models[0][0] if models else ""
        oauth_helper.set_active_provider(pid, model)
        self._refresh()

    def _remove(self, pid: str):
        oauth_helper.logout_provider(pid)
        self._refresh()

    def _login(self, pid: str):
        cfg = oauth_helper.OAUTH_CONFIGS.get(pid, {})
        if cfg.get("device_flow"):
            # Device flow: show code to user
            def worker():
                try:
                    oauth_helper.login_provider(pid)
                except RuntimeError as e:
                    msg = str(e)
                    QTimer.singleShot(0, lambda: QMessageBox.information(self, "GitHub Copilot Login", msg))
                except Exception as e:
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Login Failed", str(e)))
                QTimer.singleShot(0, self._refresh)
            threading.Thread(target=worker, daemon=True).start()
        else:
            def worker():
                try:
                    oauth_helper.login_provider(pid)
                except Exception as e:
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Login Failed", str(e)))
                QTimer.singleShot(0, self._refresh)
            threading.Thread(target=worker, daemon=True).start()

    def _test_all(self):
        for row in self._rows:
            row._test()
