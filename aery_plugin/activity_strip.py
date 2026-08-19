"""Activity strip widget for Aery chat panel."""

from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget, QSizePolicy

from aery_plugin.ui_constants import ACCENT, BG_SURFACE, BORDER, TEXT_DIM, TEXT_MUTED


class ActivityStrip(QFrame):
    """Animated activity indicator shown during agent operations."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setVisible(False)
        self.setStyleSheet(
            f"background:{BG_SURFACE};border-top:1px solid {BORDER};"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        self._star = QLabel("\u273b")
        self._star.setStyleSheet(
            f"color:{ACCENT};font-size:20px;font-family:'JetBrains Mono', Consolas, monospace;background:transparent;"
        )
        layout.addWidget(self._star)

        self._label = QLabel("ready")
        self._label.setStyleSheet(
            f"color:{TEXT_DIM};font-family:'JetBrains Mono', Consolas, monospace;font-size:11px;"
            "font-weight:700;background:transparent;"
        )
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._label)
        layout.addStretch()

        self._detail = QLabel("")
        self._detail.setStyleSheet(
            f"color:{TEXT_MUTED};font-family:'JetBrains Mono', Consolas, monospace;font-size:10px;background:transparent;"
        )
        self._detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._detail)
        self._blink_on = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # Timer only runs when visible

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 40)
    @property
    def star(self) -> QLabel:
        return self._star

    @property
    def label(self) -> QLabel:
        return self._label

    @property
    def detail(self) -> QLabel:
        return self._detail

    def _tick(self) -> None:
        self._blink_on = not self._blink_on
        color = ACCENT if self._blink_on else TEXT_MUTED
        self._star.setStyleSheet(
            f"color:{color};font-size:20px;font-family:'JetBrains Mono', Consolas, monospace;background:transparent;"
        )

    def set_active(self, text: str, detail: str = "") -> None:
        self.setVisible(True)
        self._timer.start(650)
        self._label.setText(text)
        self._detail.setText(detail)

    def set_idle(self) -> None:
        self._timer.stop()
        self._label.setText("ready")
        self.setVisible(False)

    def activity_for_tool(self, name: str) -> str:
        normalized = name.lower()
        tool_map = {
            ("run_qgis_code", "qgis_code"): "running QGIS code...",
            ("run_processing", "run_processing_algorithm"): "running processing...",
            ("list_processing_algorithms",): "listing algorithms...",
            ("describe_processing_algorithm",): "reading algorithm details...",
            ("validate_processing_runtime",): "checking processing runtime...",
        }
        for keys, label in tool_map.items():
            if normalized in keys:
                return label

        keyword_map = [
            ("add_layer", "adding layer..."),
            ("get_layer_info", "reading layer info..."),
            ("export_layer", "exporting layer..."),
            ("select_by_attribute", "selecting features..."),
            ("select_by_location", "selecting features..."),
            ("get_project_context", "reading project context..."),
            ("validate_project", "validating project..."),
            ("capture", "capturing canvas..."),
            ("canvas", "capturing canvas..."),
            ("web_search", "searching web..."),
            ("search", "searching web..."),
            ("web_fetch", "fetching web page..."),
            ("fetch", "fetching web page..."),
            ("read", "reading file..."),
            ("write", "writing file..."),
            ("edit", "writing file..."),
            ("bash", "running command..."),
            ("grep", "searching files..."),
            ("search_files", "searching files..."),
            ("find", "finding files..."),
            ("glob", "listing files..."),
            ("ls", "listing files..."),
            ("gee", "running Earth Engine..."),
            ("earth_engine", "running Earth Engine..."),
            ("ask_user", "asking you..."),
            ("confirm_action", "waiting for confirmation..."),
            ("register_tool", "registering tool..."),
            ("list_registered_tools", "listing tools..."),
            ("list_tools", "listing tools..."),
            ("remove_registered_tool", "removing tool..."),
            ("audit", "reading audit trail..."),
        ]
        for keyword, label in keyword_map:
            if keyword in normalized:
                return label

        stripped = normalized.replace("_", " ").replace("-", " ")
        return f"using {stripped}..."
