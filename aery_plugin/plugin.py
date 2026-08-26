"""Main plugin class for Aery QGIS Plugin."""
from aery_plugin.logger import logger
import asyncio
import os
import threading
from typing import Optional
from PyQt6.QtCore import Qt, QTimer
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
        self._mcp_loop: Optional[asyncio.AbstractEventLoop] = None
        self._mcp_thread: Optional[threading.Thread] = None
    def initGui(self):
        """Initialize the plugin GUI."""
        # Start QGIS code executor (TCP socket)
        self.executor = QGISCodeExecutor(iface=self.iface)
        self.executor.start_socket_server()
        # Start MCP server in background thread
        self._start_mcp_server()
        self.agent = Agent(executor=self.executor, iface=self.iface)
        # Create chat panel
        self.panel = ChatPanel(
            self.iface,
            self.agent,
            on_config=self._open_config,
            parent=self.iface.mainWindow() if self.iface else None,
        )
        self.iface.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.panel,
        )
        # Schedule dock resize after QGIS completes window restore & layout pass
        # Schedule dock resize after QGIS completes window restore & layout pass
        def _apply_compact_dock():
            try:
                main_win = self.iface.mainWindow()
                if main_win and self.panel:
                    main_win.resizeDocks([self.panel], [298], Qt.Orientation.Horizontal)
            except Exception:
                pass
        QTimer.singleShot(200, _apply_compact_dock)
        QTimer.singleShot(800, _apply_compact_dock)
        QTimer.singleShot(1500, _apply_compact_dock)
        self.action = QAction("Aery Agent")
        self.action.setCheckable(True)
        self.action.setChecked(True)
        self.action.triggered.connect(self._toggle_panel)
        self.iface.addPluginToMenu("Aery", self.action)
        # Fetch active free models from Kilo in background to auto-update
        threading.Thread(target=self._fetch_kilo_models_bg, daemon=True).start()
        # Mark panel as ready
        self.panel.set_ready()
        # ── Project & layer change signals ──
        QgsProject.instance().readProject.connect(self._on_project_changed)
        QgsProject.instance().projectSaved.connect(self._on_project_changed)
        QgsProject.instance().layersAdded.connect(self._on_layers_added)
        QgsProject.instance().layersRemoved.connect(self._on_layers_removed)
    def _start_mcp_server(self):
        """Start the MCP background server (SSE transport or dispatcher)."""
        try:
            from aery_plugin.mcp.server import get_dispatcher
            dispatcher = get_dispatcher()
            dispatcher.start()
        except Exception as e:
            logger.debug(f"Aery: MCP dispatcher startup skipped/failed: {e}")
    def _stop_mcp_server(self):
        """Stop background MCP server/loop if running."""
        try:
            if self._mcp_loop and self._mcp_loop.is_running():
                self._mcp_loop.call_soon_threadsafe(self._mcp_loop.stop)
            self._mcp_thread = None
            self._mcp_loop = None
        except Exception as e:
            logger.debug(f"Aery: MCP shutdown failed: {e}")
    def _on_project_changed(self) -> None:
        """Reset env context injection so agent gets fresh snapshot on next prompt."""
        if self.panel:
            self.panel.on_project_changed()

    def _on_layers_added(self, layers) -> None:
        if self.panel:
            for layer in layers:
                try:
                    self.panel.notify_layer_added(layer.name(), layer.type().name)
                except (AttributeError, RuntimeError) as e:
                    # Log unexpected failures instead of silently swallowing them
                    try:
                        from qgis.core import QgsMessageLog
                        QgsMessageLog.logMessage(
                            f"Aery: layer-added notification failed: {e}",
                            "Aery", Qgis.MessageLevel.Warning
                        )
                    except Exception:
                        logger.info(f"Aery: layer-added notification failed: {e}")

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
        except Exception as e:
            logger.debug("plugin: disconnect signals failed: %s", e)

        if self.panel:
            self.iface.removeDockWidget(self.panel)
            self.panel.close()
            self.panel = None

        self._stop_mcp_server()
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
    def _fetch_kilo_models_bg(self) -> None:
        """Fetch active free models from Kilo gateway and cache them."""
        import urllib.request
        import json
        try:
            from aery_plugin.oauth_helper import AGENT_DIR
            auth_path = os.path.join(AGENT_DIR, "auth.json")
            if not os.path.exists(auth_path):
                return
            with open(auth_path) as f:
                auth = json.load(f)
            token = auth.get("kilo", {}).get("access", "")
            if not token:
                return
            req = urllib.request.Request(
                "https://api.kilo.ai/api/gateway/models",
                headers={"Authorization": f"Bearer {token}"},
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            free_models = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                name = m.get("name", model_id)
                if ":free" in model_id or model_id == "openrouter/free":
                    free_models.append((model_id, name))
            if free_models:
                cache_path = os.path.join(AGENT_DIR, "kilo_models.json")
                with open(cache_path, "w") as f:
                    json.dump(free_models, f, indent=2)
        except Exception:
            pass