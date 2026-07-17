"""Tests for AeryPlugin."""

from unittest.mock import MagicMock, patch

import pytest
from aery_plugin.agent import Agent
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


@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.Agent")
@patch("aery_plugin.plugin.ChatPanel")
def test_init_gui_creates_executor(mock_chat, mock_agent, mock_exec, plugin):
    """initGui starts the executor."""
    mock_exec_instance = MagicMock()
    mock_exec_instance.port = 12345
    mock_exec.return_value = mock_exec_instance

    plugin.initGui()

    mock_exec.assert_called_once()
    mock_exec_instance.start_socket_server.assert_called_once()


@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.Agent")
@patch("aery_plugin.plugin.ChatPanel")
def test_init_gui_creates_agent(mock_chat, mock_agent, mock_exec, plugin):
    """initGui creates the agent."""
    mock_exec_instance = MagicMock()
    mock_exec_instance.port = 12345
    mock_exec.return_value = mock_exec_instance
    plugin.initGui()
    mock_agent.assert_called_once()


@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.Agent")
@patch("aery_plugin.plugin.ChatPanel")
def test_init_gui_creates_panel(mock_chat, mock_agent, mock_exec, plugin):
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


@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.Agent")
@patch("aery_plugin.plugin.ChatPanel")
def test_init_gui_adds_menu_action(mock_chat, mock_agent, mock_exec, plugin):
    """initGui adds a menu action."""
    mock_exec_instance = MagicMock()
    mock_exec_instance.port = 12345
    mock_exec.return_value = mock_exec_instance

    plugin.initGui()

    plugin.iface.addPluginToMenu.assert_called_once_with("Aery", plugin.action)


@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.Agent")
@patch("aery_plugin.plugin.ChatPanel")
def test_unload_cleans_up(mock_chat, mock_agent, mock_exec, plugin):
    """unload() shuts down all components."""
    mock_exec_instance = MagicMock()
    mock_exec_instance.port = 12345
    mock_exec.return_value = mock_exec_instance

    plugin.initGui()

    executor = plugin.executor
    panel = plugin.panel

    plugin.unload()

    executor.shutdown.assert_called_once()
    plugin.iface.removeDockWidget.assert_called_once_with(panel)


@patch("aery_plugin.plugin.QGISCodeExecutor")
@patch("aery_plugin.plugin.Agent")
@patch("aery_plugin.plugin.ChatPanel")
def test_toggle_panel(mock_chat, mock_agent, mock_exec, plugin):
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

def test_aery_engine_adapter_interface():
    from aery_plugin.engine_adapter import AeryEngineAdapter
    adapter = AeryEngineAdapter()
    
    # Verify new methods are callable and don't raise errors
    adapter.invalidate_project_context()
    adapter.reset()
    adapter.reinitialize()
    adapter.resolve_permission("req_1", True)
    
    # Verify cancel maps to stop_execution
    with patch.object(adapter, "stop_execution") as mock_stop:
        adapter.cancel()
        mock_stop.assert_called_once()

def test_engine_worker_402_handling():
    from aery_plugin.engine_adapter import EngineWorker
    from unittest.mock import MagicMock, patch
    
    worker = EngineWorker("test query", {}, MagicMock(), MagicMock(), {})
    
    with patch.object(worker, "_run_async", side_effect=Exception("HTTP Error 402")):
        worker.error = MagicMock()
        worker.run()
        
        worker.error.emit.assert_called_once()
        err_msg = worker.error.emit.call_args[0][0]
        assert "Payment Required" in err_msg
        assert "out of credits/quota" in err_msg

def test_kilo_models_cache_read():
    import json
    import os
    from aery_plugin.oauth_helper import _oauth_models, AGENT_DIR
    
    cache_path = os.path.join(AGENT_DIR, "kilo_models.json")
    mock_cache = [
        ["dynamic-model/free", "Dynamic Model Free"],
        ["openrouter/free", "Free Router"]
    ]
    
    backup_path = cache_path + ".bak"
    has_backup = False
    if os.path.exists(cache_path):
        os.rename(cache_path, backup_path)
        has_backup = True
        
    try:
        with open(cache_path, "w") as f:
            json.dump(mock_cache, f)
            
        models = _oauth_models("kilo")
        assert len(models) == 2
        assert models[0] == ("dynamic-model/free", "Dynamic Model Free")
        assert models[1] == ("openrouter/free", "Free Router")
        
    finally:
        if os.path.exists(cache_path):
            os.remove(cache_path)
        if has_backup:
            os.rename(backup_path, cache_path)
