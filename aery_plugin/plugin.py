"""Main plugin class for Aery QGIS Plugin."""

import os
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from qgis.core import QgsProject

from aery_plugin.chat_panel import ChatPanel
from aery_plugin.provider_settings import AeryConfigDialog
from aery_plugin.qgis_executor import QGISCodeExecutor
from aery_plugin.agent import Agent


class AeryPlugin:
    """Main plugin class.

    Starts the QGIS code executor and creates the chat panel with a direct LLM agent.
    """

    def __init__(self, iface):
        self.iface = iface
        self.executor: Optional[QGISCodeExecutor] = None
        self.agent: Optional[Agent] = None
        self.panel: Optional[ChatPanel] = None
        self.action: Optional[QAction] = None

    def initGui(self):
        """Initialize the plugin GUI."""
        # Start QGIS code executor (TCP socket)
        self.executor = QGISCodeExecutor(iface=self.iface)
        self.executor.start_socket_server()

        # Create agent
        self.agent = Agent(executor=self.executor, iface=self.iface)

        # Create chat panel
        self.panel = ChatPanel(
            self.iface.mainWindow(),
            self.agent,
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

        if self.panel:
            self.iface.removeDockWidget(self.panel)
            self.panel.close()
            self.panel = None

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

    def _open_config(self):
        """Open the engine configuration dialog."""
        dialog = AeryConfigDialog(self.iface.mainWindow())
        if dialog.exec():
            # Reinitialize agent with new provider config
            if self.agent:
                try:
                    self.agent.initialize()
                except Exception as e:
                    if self.panel:
                        self.panel.show_error(f"Failed to initialize agent: {e}")

    def _get_project_dir(self) -> str:
        """Get the current QGIS project directory."""
        path = QgsProject.instance().fileName()
        if path:
            return os.path.dirname(path)
        return os.path.expanduser("~")
