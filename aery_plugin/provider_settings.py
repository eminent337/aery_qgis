"""Modern, advanced, enterprise-grade Aery AI Settings Dialog.

Matches native QGIS / Aerynel Workstation aesthetic:
- Compact, high-finish UI styling (460x440 for Settings)
- Direct browser launch via QDesktopServices & xdg-open & webbrowser fallback
- Clean enterprise account status card with token metadata and disconnect confirmation
- Aery SVG logo header
- Model Selection (Curated free models)
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import oauth_helper

# ── Aesthetic Palette ───────────────────────────────────────────────────────────
BG_DARK      = "#0d1117"
BG_PANEL     = "#161b22"
BG_CARD      = "#21262d"
BG_INPUT     = "#090d12"
BORDER       = "#30363d"
BORDER_LIGHT = "#3d444d"
ACCENT       = "#2dd4bf"      # Teal from Aerynel
ACCENT_HOVER = "#5eead4"
TEXT_PRIMARY = "#f0f6fc"
TEXT_MUTED   = "#8b949e"
TEXT_DIM     = "#6e7681"
SUCCESS      = "#3fb950"
DANGER       = "#f85149"
DANGER_HOVER = "#ff7b72"


def _open_url(url_str: str) -> None:
    """Robust URL opener across Linux (Wayland/X11), Windows, and macOS."""
    if not url_str:
        return
    import subprocess
    import sys
    # 1. Try QDesktopServices
    try:
        if QDesktopServices.openUrl(QUrl(url_str)):
            return
    except Exception:
        pass
    # 2. Try xdg-open directly on Linux
    if sys.platform.startswith("linux"):
        try:
            subprocess.Popen(["xdg-open", url_str], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
    # 3. Fallback to Python standard webbrowser
    try:
        webbrowser.open(url_str, new=2)
    except Exception:
        pass


def _svg_pixmap(path: str, size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    if os.path.exists(path):
        ren = QSvgRenderer(path)
        painter = QPainter(pix)
        ren.render(painter)
        painter.end()
    return pix


def _dialog_stylesheet() -> str:
    return f"""
        QDialog {{
            background-color: {BG_DARK};
            color: {TEXT_PRIMARY};
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        QLabel {{
            background: transparent;
            color: {TEXT_PRIMARY};
        }}
        QFrame#card {{
            background-color: {BG_PANEL};
            border: 1px solid {BORDER};
            border-radius: 8px;
        }}
        QFrame#innerCard {{
            background-color: {BG_CARD};
            border: 1px solid {BORDER};
            border-radius: 6px;
        }}
        QComboBox {{
            background-color: {BG_INPUT};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER};
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 500;
        }}
        QComboBox:hover {{
            border-color: {TEXT_DIM};
        }}
        QComboBox:focus {{
            border-color: {ACCENT};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 20px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {BG_PANEL};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER};
            selection-background-color: {BG_CARD};
            selection-color: {ACCENT};
            padding: 4px;
        }}
    """


class _DeviceFlowDialog(QDialog):
    """Refined OAuth Device Authorization Dialog with instant feedback & responsive polling."""

    def __init__(self, pid: str = "kilo", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pid = pid
        self._user_code = ""
        self._verification_url = "https://app.kilo.ai/device-auth"
        self._cancelled = False
        
        self.setWindowTitle("Authorize Kilo AI Assistant")
        self.setFixedSize(420, 340)
        self.setModal(True)
        self.setStyleSheet(_dialog_stylesheet())

        self._build_ui()
        threading.Thread(target=self._start_flow, daemon=True).start()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)

        icon_lbl = QLabel()
        svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icons", "aery.svg")
        icon_lbl.setPixmap(_svg_pixmap(svg_path, 30))
        icon_lbl.setFixedSize(30, 30)
        header.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title = QLabel("Device Authorization")
        title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY};")
        subtitle = QLabel("Confirm sign-in to activate Kilo AI models")
        subtitle.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch()

        root.addLayout(header)

        self._status_box = QFrame()
        self._status_box.setObjectName("card")
        status_lay = QVBoxLayout(self._status_box)
        status_lay.setContentsMargins(12, 12, 12, 12)
        status_lay.setSpacing(8)

        inst_label = QLabel("Copy verification code and confirm in the browser tab:")
        inst_label.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        status_lay.addWidget(inst_label)

        code_box = QHBoxLayout()
        code_box.setSpacing(6)

        self._code_display = QLabel("REQUESTING…")
        self._code_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_display.setFixedHeight(38)
        self._code_display.setStyleSheet(
            f"font-family: 'SF Mono', Consolas, Menlo, monospace; font-size: 16px; font-weight: 700;"
            f" color: {ACCENT}; background: {BG_INPUT}; border: 1px dashed {ACCENT}; border-radius: 6px;"
            f" letter-spacing: 0.12em;"
        )
        code_box.addWidget(self._code_display, 1)

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setFixedSize(60, 38)
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_CARD}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER};"
            f" border-radius: 6px; font-size: 11px; font-weight: 600; }}"
            f" QPushButton:hover {{ background: {BORDER}; color: {ACCENT}; }}"
        )
        self._copy_btn.clicked.connect(self._copy_code)
        code_box.addWidget(self._copy_btn)

        status_lay.addLayout(code_box)

        self._poll_status = QLabel("⏳ Waiting for confirmation in browser...")
        self._poll_status.setStyleSheet(f"font-size: 10px; color: {TEXT_DIM};")
        status_lay.addWidget(self._poll_status)

        root.addWidget(self._status_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._open_browser_btn = QPushButton("Open Browser")
        self._open_browser_btn.setFixedHeight(30)
        self._open_browser_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_browser_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_MUTED}; border: 1px solid {BORDER};"
            f" border-radius: 6px; font-size: 11px; font-weight: 600; padding: 0 10px;"
            f" text-align: center; }}"
            f" QPushButton:hover {{ background: {BG_CARD}; color: {TEXT_PRIMARY}; border-color: {TEXT_DIM}; }}"
        )
        self._open_browser_btn.clicked.connect(self._open_browser)
        btn_row.addWidget(self._open_browser_btn)

        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(30)
        cancel_btn.setFixedWidth(70)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_MUTED}; border: 1px solid {BORDER};"
            f" border-radius: 6px; font-size: 11px; font-weight: 600; }}"
            f" QPushButton:hover {{ background: {BG_CARD}; color: {DANGER}; border-color: {DANGER}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        root.addLayout(btn_row)

    def _copy_code(self) -> None:
        if self._user_code:
            cb = QGuiApplication.clipboard()
            if cb:
                cb.setText(self._user_code)
                self._copy_btn.setText("Copied!")
                self._copy_btn.setStyleSheet(
                    f"QPushButton {{ background: {SUCCESS}; color: {BG_DARK}; border: none;"
                    f" border-radius: 6px; font-size: 11px; font-weight: 700; }}"
                )
                QTimer.singleShot(1800, lambda: self._copy_btn.setText("Copy"))

    def _open_browser(self) -> None:
        if self._verification_url:
            _open_url(self._verification_url)

    def reject(self) -> None:
        self._cancelled = True
        super().reject()

    def _start_flow(self) -> None:
        cfg = oauth_helper.OAUTH_CONFIGS.get(self._pid, {})
        auth_url = cfg.get("auth_url", "https://api.kilo.ai/api/device-auth/codes")
        token_url = cfg.get("token_url", "https://api.kilo.ai/api/device-auth/codes")

        try:
            req = urllib.request.Request(
                auth_url,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            if not self._cancelled:
                QTimer.singleShot(0, lambda: self._on_error(f"Failed to request device code: {e}"))
            return

        self._user_code = data.get("code", "")
        self._verification_url = data.get("verificationUrl", "https://app.kilo.ai/device-auth")

        def update_ui():
            self._code_display.setText(self._user_code)
            self._copy_code()
            self._open_browser()

        QTimer.singleShot(0, update_ui)

        poll_endpoint = f"{token_url}/{urllib.parse.quote(self._user_code)}"
        deadline = time.time() + 180

        while time.time() < deadline and not self._cancelled:
            time.sleep(1.5)
            if self._cancelled:
                break

            poll_req = urllib.request.Request(
                poll_endpoint,
                headers={"Accept": "application/json"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(poll_req, timeout=8) as resp:
                    if resp.status == 202:
                        continue
                    token_data = json.loads(resp.read().decode())
                    if token_data.get("status") == "approved" and token_data.get("token"):
                        from aery_plugin.vault import get_vault
                        vault = get_vault("auth")
                        vault.set_oauth_tokens(
                            self._pid,
                            token_data["token"],
                            "",
                            int(time.time() * 1000) + 31536000 * 1000,
                        )
                        if not oauth_helper.get_active_provider():
                            models = oauth_helper._oauth_models(self._pid)
                            oauth_helper.set_active_provider(self._pid, models[0][0] if models else "")
                        
                        if not self._cancelled:
                            QTimer.singleShot(0, self.accept)
                        return
                    elif token_data.get("status") == "rejected":
                        if not self._cancelled:
                            QTimer.singleShot(0, lambda: self._on_error("Authorization declined by user."))
                        return
                    elif token_data.get("status") == "expired":
                        if not self._cancelled:
                            QTimer.singleShot(0, lambda: self._on_error("Code expired. Please try again."))
                        return
            except urllib.error.HTTPError as e:
                if e.code == 202:
                    continue
            except Exception:
                pass

        if not self._cancelled:
            QTimer.singleShot(0, lambda: self._on_error("Authorization timed out. Please try again."))

    def _on_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Authorization Error", msg)
        self.reject()


class SessionSwitcherDialog(QDialog):
    """Compact dialog to switch between conversation sessions or create a new session."""

    session_switched = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Conversation Sessions")
        self.setFixedSize(380, 400)
        self.setModal(True)
        self.setStyleSheet(_dialog_stylesheet())

        self._build_ui()
        self._populate_sessions()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        icon_lbl = QLabel("💬")
        icon_lbl.setFixedSize(28, 28)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 6px; font-size: 14px;")
        hdr.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        t = QLabel("Conversation Sessions")
        t.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY};")
        s = QLabel("Switch between active workspaces")
        s.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED};")
        title_col.addWidget(t)
        title_col.addWidget(s)
        hdr.addLayout(title_col)
        hdr.addStretch()

        root.addLayout(hdr)

        new_btn = QPushButton("+ Start New Session")
        new_btn.setFixedHeight(30)
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: {BG_DARK}; border: none;"
            f" border-radius: 6px; font-size: 11px; font-weight: 700; }}"
            f" QPushButton:hover {{ background: {ACCENT_HOVER}; }}"
        )
        new_btn.clicked.connect(self._create_new_session)
        root.addWidget(new_btn)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: 1px solid {BORDER}; border-radius: 6px; }}")

        self._list_container = QWidget()
        self._list_lay = QVBoxLayout(self._list_container)
        self._list_lay.setContentsMargins(6, 6, 6, 6)
        self._list_lay.setSpacing(4)
        scroll.setWidget(self._list_container)

        root.addWidget(scroll, 1)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_MUTED}; border: 1px solid {BORDER};"
            f" border-radius: 4px; font-size: 11px; }}"
            f" QPushButton:hover {{ background: {BG_CARD}; color: {TEXT_PRIMARY}; }}"
        )
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

    def _populate_sessions(self) -> None:
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from aery_plugin.session_manager import get_session_manager
            mgr = get_session_manager()
            sessions = mgr.list_sessions()
            active_sess = mgr.get_active_session()
            active_id = active_sess.session_id if active_sess else None

            if not sessions:
                lbl = QLabel("No active sessions.")
                lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; padding: 8px;")
                self._list_lay.addWidget(lbl)
            else:
                for idx, s in enumerate(sessions):
                    s_id = s.session_id
                    is_active = (s_id == active_id)
                    
                    row = QFrame()
                    row.setObjectName("innerCard" if is_active else "card")
                    row_lay = QHBoxLayout(row)
                    row_lay.setContentsMargins(8, 6, 8, 6)
                    row_lay.setSpacing(6)

                    dot = QLabel("●" if is_active else "○")
                    dot.setStyleSheet(f"color: {SUCCESS if is_active else TEXT_DIM}; font-size: 11px;")
                    row_lay.addWidget(dot)

                    info_col = QVBoxLayout()
                    info_col.setSpacing(1)
                    created_str = time.strftime("%b %d, %H:%M", time.localtime(s.created_at)) if s.created_at else ""
                    name = QLabel(f"Session {idx + 1} {'(Active)' if is_active else ''}")
                    name.setStyleSheet(f"font-size: 11px; font-weight: {700 if is_active else 500}; color: {ACCENT if is_active else TEXT_PRIMARY};")
                    meta = QLabel(f"{s_id[:8]} • {created_str}")
                    meta.setStyleSheet(f"font-size: 9px; color: {TEXT_DIM};")
                    info_col.addWidget(name)
                    info_col.addWidget(meta)
                    row_lay.addLayout(info_col, 1)

                    if not is_active:
                        switch_btn = QPushButton("Switch")
                        switch_btn.setFixedHeight(22)
                        switch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                        switch_btn.setStyleSheet(
                            f"QPushButton {{ background: {BG_PANEL}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER};"
                            f" border-radius: 4px; font-size: 10px; font-weight: 600; padding: 0 6px; }}"
                            f" QPushButton:hover {{ background: {ACCENT}; color: {BG_DARK}; border: none; }}"
                        )
                        switch_btn.clicked.connect(lambda _, sid=s_id: self._switch_to_session(sid))
                        row_lay.addWidget(switch_btn)

                    self._list_lay.addWidget(row)
        except Exception:
            pass

        self._list_lay.addStretch()

    def _switch_to_session(self, session_id: str) -> None:
        try:
            from aery_plugin.session_manager import get_session_manager
            mgr = get_session_manager()
            mgr.set_active_session(session_id)
            self._populate_sessions()
            self.session_switched.emit(session_id)
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not switch session: {e}")

    def _create_new_session(self) -> None:
        try:
            from aery_plugin.session_manager import get_session_manager
            mgr = get_session_manager()
            new_id = mgr.create_session()
            mgr.set_active_session(new_id)
            self._populate_sessions()
            self.session_switched.emit(new_id)
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not create session: {e}")


class AerySettingsDialog(QDialog):
    """Enterprise-grade, modern AI settings panel for Provider and Models."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Aerynel AI Configuration")
        self.setFixedSize(460, 440)
        self.setModal(True)
        self.setStyleSheet(_dialog_stylesheet())

        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)

        # Header with Logo
        header = QHBoxLayout()
        header.setSpacing(12)

        icon_lbl = QLabel()
        svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icons", "aery.svg")
        icon_lbl.setPixmap(_svg_pixmap(svg_path, 34))
        icon_lbl.setFixedSize(34, 34)
        icon_lbl.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 1px;")
        header.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        h_title = QLabel("AI Configuration")
        h_title.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {TEXT_PRIMARY}; letter-spacing: -0.01em;")
        h_sub = QLabel("Model provider and active intelligence model")
        h_sub.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        title_col.addWidget(h_title)
        title_col.addWidget(h_sub)
        header.addLayout(title_col)
        header.addStretch()

        root.addLayout(header)

        # Provider Card
        prov_card = QFrame()
        prov_card.setObjectName("card")
        prov_lay = QVBoxLayout(prov_card)
        prov_lay.setContentsMargins(14, 14, 14, 14)
        prov_lay.setSpacing(12)

        c1_hdr = QHBoxLayout()
        c1_title = QLabel("PROVIDER & CREDENTIALS")
        c1_title.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {TEXT_MUTED}; letter-spacing: 0.08em;")
        c1_hdr.addWidget(c1_title)
        c1_hdr.addStretch()

        self._status_badge = QLabel("Not Connected")
        self._status_badge.setFixedHeight(22)
        self._status_badge.setStyleSheet(
            f"background: rgba(248, 81, 73, 0.12); color: {DANGER}; border: 1px solid rgba(248, 81, 73, 0.3);"
            f" border-radius: 11px; font-size: 10px; font-weight: 600; padding: 0 8px;"
        )
        c1_hdr.addWidget(self._status_badge)
        prov_lay.addLayout(c1_hdr)

        self._prov_combo = QComboBox()
        self._prov_combo.addItem("Kilo Gateway (free active models)", "kilo")
        self._prov_combo.setFixedHeight(32)
        prov_lay.addWidget(self._prov_combo)

        # Enterprise Auth Container
        auth_inner = QFrame()
        auth_inner.setObjectName("innerCard")
        auth_lay = QHBoxLayout(auth_inner)
        auth_lay.setContentsMargins(12, 10, 12, 10)
        auth_lay.setSpacing(10)

        auth_text = QVBoxLayout()
        auth_text.setSpacing(2)
        self._auth_status_title = QLabel("Device Flow Authentication")
        self._auth_status_title.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {TEXT_PRIMARY};")
        self._auth_status_sub = QLabel("One-click sign in via browser")
        self._auth_status_sub.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED};")
        auth_text.addWidget(self._auth_status_title)
        auth_text.addWidget(self._auth_status_sub)
        auth_lay.addLayout(auth_text, 1)

        self._auth_btn = QPushButton("Sign in with Kilo")
        self._auth_btn.setFixedHeight(30)
        self._auth_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._auth_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: {BG_DARK}; border: none;"
            f" border-radius: 6px; font-size: 11px; font-weight: 700; padding: 0 12px; }}"
            f" QPushButton:hover {{ background: {ACCENT_HOVER}; }}"
        )
        self._auth_btn.clicked.connect(self._on_auth_clicked)
        auth_lay.addWidget(self._auth_btn)

        prov_lay.addWidget(auth_inner)
        root.addWidget(prov_card)

        # Model Selection Card
        model_card = QFrame()
        model_card.setObjectName("card")
        model_lay = QVBoxLayout(model_card)
        model_lay.setContentsMargins(14, 14, 14, 14)
        model_lay.setSpacing(10)

        c2_title = QLabel("ACTIVE MODEL")
        c2_title.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {TEXT_MUTED}; letter-spacing: 0.08em;")
        model_lay.addWidget(c2_title)

        self._model_combo = QComboBox()
        self._model_combo.setFixedHeight(32)
        self._populate_models()
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        model_lay.addWidget(self._model_combo)

        root.addWidget(model_card)

        root.addStretch()

        # Footer Actions
        footer = QHBoxLayout()
        footer.addStretch()

        done_btn = QPushButton("Done")
        done_btn.setFixedHeight(32)
        done_btn.setFixedWidth(85)
        done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        done_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: {BG_DARK}; border: none;"
            f" border-radius: 6px; font-size: 11px; font-weight: 700; }}"
            f" QPushButton:hover {{ background: {ACCENT_HOVER}; }}"
        )
        done_btn.clicked.connect(self.accept)
        footer.addWidget(done_btn)

        root.addLayout(footer)

    def _populate_models(self) -> None:
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        models = oauth_helper._oauth_models("kilo")
        for mid, mname in models:
            self._model_combo.addItem(f"{mname}  ({mid})", mid)
        self._model_combo.blockSignals(False)

    def _refresh(self) -> None:
        auth = oauth_helper._load_auth()
        kilo_entry = auth.get("kilo", {})
        has_creds = bool(
            kilo_entry.get("access") or kilo_entry.get("accessToken")
            or kilo_entry.get("access_token")
        )

        if has_creds:
            self._status_badge.setText("● Connected")
            self._status_badge.setStyleSheet(
                f"background: rgba(63, 185, 80, 0.15); color: {SUCCESS}; border: 1px solid rgba(63, 185, 80, 0.3);"
                f" border-radius: 11px; font-size: 10px; font-weight: 600; padding: 0 8px;"
            )
            self._auth_status_title.setText("Authenticated Session")
            self._auth_status_sub.setText("Kilo OAuth credentials verified & active in Vault")
            self._auth_btn.setText("Disconnect Account")
            self._auth_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {TEXT_MUTED}; border: 1px solid {BORDER_LIGHT};"
                f" border-radius: 6px; font-size: 11px; font-weight: 600; padding: 0 12px; }}"
                f" QPushButton:hover {{ background: rgba(248, 81, 73, 0.15); color: {DANGER}; border-color: {DANGER}; }}"
            )
        else:
            self._status_badge.setText("● Not Connected")
            self._status_badge.setStyleSheet(
                f"background: rgba(248, 81, 73, 0.12); color: {DANGER}; border: 1px solid rgba(248, 81, 73, 0.3);"
                f" border-radius: 11px; font-size: 10px; font-weight: 600; padding: 0 8px;"
            )
            self._auth_status_title.setText("Device Flow Authentication")
            self._auth_status_sub.setText("One-click sign in via browser")
            self._auth_btn.setText("Sign in with Kilo")
            self._auth_btn.setStyleSheet(
                f"QPushButton {{ background: {ACCENT}; color: {BG_DARK}; border: none;"
                f" border-radius: 6px; font-size: 11px; font-weight: 700; padding: 0 12px; }}"
                f" QPushButton:hover {{ background: {ACCENT_HOVER}; }}"
            )

        active = oauth_helper.get_active_provider()
        current_model = active.get("model", "") if active else ""
        if current_model:
            idx = self._model_combo.findData(current_model)
            if idx >= 0:
                self._model_combo.blockSignals(True)
                self._model_combo.setCurrentIndex(idx)
                self._model_combo.blockSignals(False)

    def _on_auth_clicked(self) -> None:
        auth = oauth_helper._load_auth()
        kilo_entry = auth.get("kilo", {})
        has_creds = bool(
            kilo_entry.get("access") or kilo_entry.get("accessToken")
            or kilo_entry.get("access_token")
        )
        if has_creds:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Disconnect Account")
            msg_box.setText("Are you sure you want to disconnect Kilo Gateway?")
            msg_box.setInformativeText("Your stored credentials will be removed from the local vault.")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg_box.setDefaultButton(QMessageBox.StandardButton.No)
            msg_box.setStyleSheet(_dialog_stylesheet())
            if msg_box.exec() == QMessageBox.StandardButton.Yes:
                oauth_helper.logout_provider("kilo")
                self._refresh()
        else:
            dlg = _DeviceFlowDialog("kilo", self)
            if dlg.exec():
                self._refresh()

    def _on_model_changed(self, index: int) -> None:
        model_id = self._model_combo.itemData(index)
        if model_id:
            oauth_helper.set_active_provider("kilo", model_id)


# Backward-compatibility aliases
AeryConfigDialog = AerySettingsDialog
AuthMethodWizard = AerySettingsDialog
ModelSwitcherDialog = AerySettingsDialog
