import re

with open("aery_plugin/provider_settings.py", "r") as f:
    content = f.read()

# 1. Update imports
old_imports = """import json
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import oauth_helper"""

new_imports = """import json
import os
import re
import threading
import time
from typing import Optional
import uuid

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from aery_plugin import oauth_helper
from aery_plugin.profiles import (
    AssistantProfile,
    get_default_profile_id,
    list_profiles,
    load_profile,
    save_profile,
    select_active_profile,
    set_default_profile_id,
)"""

content = content.replace(old_imports, new_imports)

# 2. Update ProviderOAuthList._refresh method
old_refresh = """    def _refresh(self) -> None:
        while self._blay.count():
            item = self._blay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._pid_map.clear()
        auth = oauth_helper._load_auth()

        for pid, cfg in oauth_helper.OAUTH_CONFIGS.items():
            if pid not in ('google-antigravity', 'kilo'): continue
            creds = auth.get(pid, {})
            connected = bool(creds.get("access") or creds.get("accessToken")
                             or creds.get("refresh") or creds.get("refreshToken"))
            self._pid_map[pid] = cfg["name"]

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            dot = QLabel("\u25cf" if connected else "\u25cb")
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

        self._blay.addStretch()"""

new_refresh = """    def _refresh(self) -> None:
        while self._blay.count():
            item = self._blay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._pid_map.clear()
        auth = oauth_helper._load_auth()

        # Show profiles instead of raw providers
        profiles = list_profiles()
        default_pid = get_default_profile_id()

        for profile in profiles:
            pid = profile.provider
            cfg = oauth_helper.OAUTH_CONFIGS.get(pid, {})
            if not cfg:
                continue
            creds = auth.get(pid, {})
            connected = bool(creds.get("access") or creds.get("accessToken")
                             or creds.get("refresh") or creds.get("refreshToken"))
            is_default = (pid == default_pid)
            self._pid_map[pid] = cfg["name"]

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            # Connection status dot
            dot = QLabel("\u25cf" if connected else "\u25cb")
            dot.setFixedSize(16, 16)
            dc = GREEN if connected else DIM
            dot.setStyleSheet(
                f"color:{dc}; font-size:{_fs(F_H)}; border:none; background:transparent;")
            row.addWidget(dot)

            # Profile name + provider name
            name_text = f"{profile.name} ({cfg['name']})"
            if is_default:
                name_text += " \u2605"
            nm = QLabel(name_text)
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

        # Add "Create Profile" button
        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.setSpacing(8)
        add_btn = _btn("+ CREATE PROFILE", ACCENT)
        add_btn.clicked.connect(self._create_profile)
        add_row.addWidget(add_btn, 1)
        wrap = QWidget()
        wrap.setLayout(add_row)
        wrap.setStyleSheet(
            f"QWidget {{ background:{SURFACE}; border:2px dashed {ACCENT}; border-radius:3px; padding:4px 8px; }}"
        )
        self._blay.addWidget(wrap)

        self._blay.addStretch()"""

content = content.replace(old_refresh, new_refresh)

# 3. Add _create_profile method to ProviderOAuthList
old_class_end = """        self._blay.addStretch()


# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# Screen 2b \u2014 API-key provider list"""

new_class_end = """        self._blay.addStretch()

    def _create_profile(self) -> None:
        \"\"\"Open a dialog to create a new profile.\"\"\"
        # For now, just create a Kilo profile with default name
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Create Profile", "Profile name:", text="My Kilo Profile")
        if ok and name:
            import time
            profile = AssistantProfile(
                id=f"profile_{int(time.time())}",
                name=name.strip(),
                provider="kilo",
                model="kilo-auto/free",
            )
            if save_profile(profile):
                set_default_profile_id(profile.id)
                self._refresh()


# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# Screen 2b \u2014 API-key provider list"""

content = content.replace(old_class_end, new_class_end)

with open("aery_plugin/provider_settings.py", "w") as f:
    f.write(content)

print("Modifications applied successfully")