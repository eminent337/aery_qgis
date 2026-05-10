"""Tests for AeryPlugin."""

from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from aery_plugin.plugin import AeryPlugin


@pytest.fixture(scope="session")
def qapp():
    """Create a QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def plugin(qapp):
    """Create an AeryPlugin with mocked QGIS iface."""
    iface = MagicMock()
    main_window = MagicMock()
    iface.mainWindow.return_value = main_window

    p = AeryPlugin(iface)
    yield p
    if p.executor:
        p.executor.shutdown()
    if p.rpc:
        p.rpc.shutdown()


@patch("aery_plugin.plugin.ProviderSettingsDialog.load_config", return_value={})
@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.RPCBridge")
@patch("aery_plugin.plugin.ChatPanel")
def test_init_gui_creates_executor(
    mock_chat,
    mock_rpc,
    mock_exec,
    mock_load_cfg,
    plugin,
):
    """initGui starts the executor."""
    mock_exec_instance = MagicMock()
    mock_exec_instance.port = 12345
    mock_exec.return_value = mock_exec_instance

    plugin.initGui()

    mock_exec.assert_called_once()
    mock_exec_instance.start_socket_server.assert_called_once()


@patch("aery_plugin.plugin.ProviderSettingsDialog.load_config", return_value={})
@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.RPCBridge")
@patch("aery_plugin.plugin.ChatPanel")
def test_init_gui_spawns_rpc(
    mock_chat,
    mock_rpc,
    mock_exec,
    mock_load_cfg,
    plugin,
):
    """initGui spawns the RPC bridge with the executor port."""
    mock_exec_instance = MagicMock()
    mock_exec_instance.port = 12345
    mock_exec.return_value = mock_exec_instance

    plugin.initGui()

    mock_rpc.assert_called_once_with(
        cwd=plugin._get_project_dir(), port=12345, provider_config={}
    )
    mock_rpc.return_value.spawn.assert_called_once()


@patch("aery_plugin.plugin.ProviderSettingsDialog.load_config", return_value={})
@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.RPCBridge")
@patch("aery_plugin.plugin.ChatPanel")
def test_init_gui_creates_panel(
    mock_chat,
    mock_rpc,
    mock_exec,
    mock_load_cfg,
    plugin,
):
    """initGui creates and adds the chat panel."""
    mock_exec_instance = MagicMock()
    mock_exec_instance.port = 12345
    mock_exec.return_value = mock_exec_instance

    plugin.initGui()

    mock_chat.assert_called_once()
    plugin.iface.addDockWidget.assert_called_once_with(
        Qt.DockWidgetArea.RightDockWidgetArea,
        mock_chat.return_value,
    )


@patch("aery_plugin.plugin.ProviderSettingsDialog.load_config", return_value={})
@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.RPCBridge")
@patch("aery_plugin.plugin.ChatPanel")
def test_init_gui_adds_menu_action(
    mock_chat,
    mock_rpc,
    mock_exec,
    mock_load_cfg,
    plugin,
):
    """initGui adds a menu action."""
    mock_exec_instance = MagicMock()
    mock_exec_instance.port = 12345
    mock_exec.return_value = mock_exec_instance

    plugin.initGui()

    plugin.iface.addPluginToMenu.assert_called_once_with("Aery", plugin.action)


@patch("aery_plugin.plugin.ProviderSettingsDialog.load_config", return_value={})
@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.RPCBridge")
@patch("aery_plugin.plugin.ChatPanel")
def test_unload_cleans_up(
    mock_chat,
    mock_rpc,
    mock_exec,
    mock_load_cfg,
    plugin,
):
    """unload() shuts down all components."""
    mock_exec_instance = MagicMock()
    mock_exec_instance.port = 12345
    mock_exec.return_value = mock_exec_instance

    plugin.initGui()

    # Save references before unload clears them
    rpc = plugin.rpc
    executor = plugin.executor
    panel = plugin.panel

    plugin.unload()

    rpc.shutdown.assert_called_once()
    executor.shutdown.assert_called_once()
    plugin.iface.removeDockWidget.assert_called_once_with(panel)


@patch("aery_plugin.plugin.ProviderSettingsDialog.load_config", return_value={})
@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.RPCBridge")
@patch("aery_plugin.plugin.ChatPanel")
def test_toggle_panel(
    mock_chat,
    mock_rpc,
    mock_exec,
    mock_load_cfg,
    plugin,
):
    """Toggle panel shows/hides the panel."""
    mock_exec_instance = MagicMock()
    mock_exec_instance.port = 12345
    mock_exec.return_value = mock_exec_instance

    plugin.initGui()

    panel = mock_chat.return_value

    plugin._toggle_panel(True)
    panel.setVisible.assert_called_with(True)

    plugin._toggle_panel(False)
    panel.setVisible.assert_called_with(False)
