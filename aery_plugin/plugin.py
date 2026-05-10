"""Main plugin class for Aery QGIS Plugin."""

"""Main plugin class for Aery QGIS Plugin."""

import os
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from qgis.core import QgsProject

from aery_plugin.chat_panel import ChatPanel
from aery_plugin.provider_settings import ProviderSettingsDialog
from aery_plugin.qgis_executor import QGISCodeExecutor
from aery_plugin.rpc_bridge import RPCBridge


class AeryPlugin:
    """Main plugin class.

    Starts the QGIS code executor (TCP socket), reads provider config from
    QSettings, spawns the Aery standalone binary with the executor port,
    sends provider config as first stdin message, and creates the chat panel.
    """

    def __init__(self, iface):
        self.iface = iface
        self.executor: Optional[QGISCodeExecutor] = None
        self.rpc: Optional[RPCBridge] = None
        self.panel: Optional[ChatPanel] = None
        self.action: Optional[QAction] = None

    def initGui(self):
        """Initialize the plugin GUI."""
        # Start QGIS code executor (TCP socket)
        self.executor = QGISCodeExecutor(iface=self.iface)
        self.executor.start_socket_server()

        # Read provider config from QSettings
        provider_config = ProviderSettingsDialog.load_config()

        # Start RPC bridge — spawns binary with executor port + provider config
        self.rpc = RPCBridge(
            cwd=self._get_project_dir(),
            port=self.executor.port,
            provider_config=provider_config,
        )
        self.rpc.spawn()

        # Create chat panel with settings button
        self.panel = ChatPanel(
            self.iface.mainWindow(),
            self.rpc,
            on_settings=self._open_settings,
        )
        self.iface.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.panel,
        )

        # Menu action
        self.action = QAction("Aery Agent")
        self.action.setCheckable(True)
        self.action.setChecked(True)
        self.action.triggered.connect(self._toggle_panel)
        self.iface.addPluginToMenu("Aery", self.action)

        # Mark panel as ready
        self.panel.set_ready()

    def unload(self):
        """Clean up when plugin is unloaded."""
        if self.panel:
            self.iface.removeDockWidget(self.panel)
            self.panel.close()
            self.panel = None

        if self.rpc:
            self.rpc.shutdown()
            self.rpc = None

        if self.executor:
            self.executor.shutdown()
            self.executor = None

        if self.action:
            self.iface.removePluginMenu("Aery", self.action)
            self.action = None

    def _toggle_panel(self, visible: bool):
        """Show or hide the chat panel."""
        if self.panel:
            self.panel.setVisible(visible)

    def _open_settings(self):
        """Open the provider settings dialog."""
        dialog = ProviderSettingsDialog(self.iface.mainWindow())
        if dialog.exec():
            # Provider config changed — restart the agent
            if self.rpc:
                self.rpc.shutdown()
            provider_config = ProviderSettingsDialog.load_config()
            self.rpc = RPCBridge(
                cwd=self._get_project_dir(),
                port=self.executor.port,
                provider_config=provider_config,
            )
            self.rpc.spawn()
            if self.panel:
                self.panel.set_rpc(self.rpc)

    def _get_project_dir(self) -> str:
        """Get the current QGIS project directory."""
        path = QgsProject.instance().fileName()
        if path:
            return os.path.dirname(path)
        return os.path.expanduser("~")
