"""Main plugin class for Aery QGIS Plugin."""

import os
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from qgis.core import QgsProject

from aery_plugin.chat_panel import ChatPanel
from aery_plugin.provider_settings import AeryConfigDialog
from aery_plugin.qgis_executor import QGISCodeExecutor
from aery_plugin.rpc_bridge import RPCBridge


class AeryPlugin:
    """Main plugin class.

    Starts the QGIS code executor (TCP socket), spawns the specialized
    Aery standalone binary, and creates the chat panel.
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

        # Start RPC bridge — spawns specialized binary
        self.rpc = RPCBridge(
            cwd=self._get_project_dir(),
            port=self.executor.port,
        )
        self.rpc.spawn()

        # Create chat panel
        self.panel = ChatPanel(
            self.iface.mainWindow(),
            self.rpc,
            on_config=self._open_config,
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

        # Kill child processes on abort
        if self.rpc:
            self.rpc.disconnected.connect(lambda: self.executor and self.executor.abort_children())

        # ── Project & layer change signals ──
        QgsProject.instance().readProject.connect(self._on_project_changed)
        QgsProject.instance().projectSaved.connect(self._on_project_changed)
        QgsProject.instance().layersAdded.connect(self._on_layers_added)
        QgsProject.instance().layersRemoved.connect(self._on_layers_removed)

    def _on_project_changed(self) -> None:
        """Reset env context injection so agent gets fresh snapshot on next prompt."""
        if self.panel:
            self.panel.on_project_changed()

    def _on_layers_added(self, layers) -> None:
        if self.panel:
            for layer in layers:
                try:
                    self.panel.notify_layer_added(layer.name(), layer.type().name)
                except Exception:
                    pass

    def _on_layers_removed(self, layer_ids) -> None:
        if self.panel:
            self.panel.notify_layers_removed(len(layer_ids))

    def unload(self):
        """Clean up when plugin is unloaded."""
        try:
            QgsProject.instance().readProject.disconnect(self._on_project_changed)
            QgsProject.instance().projectSaved.disconnect(self._on_project_changed)
            QgsProject.instance().layersAdded.disconnect(self._on_layers_added)
            QgsProject.instance().layersRemoved.disconnect(self._on_layers_removed)
        except Exception:
            pass
        # Disconnect UI before terminating the process so reader-thread exit
        # signals cannot update a closing/deleted dock widget.
        if self.panel:
            self.panel.disconnect_rpc()
        if self.rpc:
            self.rpc.shutdown()
            self.rpc = None

        if self.executor:
            self.executor.shutdown()
            self.executor = None

        if self.panel:
            self.iface.removeDockWidget(self.panel)
            self.panel.close()
            self.panel = None

        if self.action:
            self.iface.removePluginMenu("Aery", self.action)
            self.action = None

    def _toggle_panel(self, visible: bool):
        """Show or hide the chat panel."""
        if self.panel:
            self.panel.setVisible(visible)

    def _open_config(self):
        """Open the engine configuration dialog."""
        dialog = AeryConfigDialog(self.iface.mainWindow())
        if dialog.exec():
            # Restart engine with new technical config
            if self.panel:
                self.panel.disconnect_rpc()
            if self.rpc:
                self.rpc.shutdown()
            self.rpc = RPCBridge(
                cwd=self._get_project_dir(),
                port=self.executor.port
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
