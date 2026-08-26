"""Unit test for pip_install tool in ToolRegistry."""

import asyncio
from unittest.mock import MagicMock, patch
from aery_plugin.tools import ToolRegistry


def test_pip_install_tool_registered():
    reg = ToolRegistry(executor=MagicMock())
    assert "pip_install" in reg._tools
    schema = reg._tools["pip_install"]
    assert schema["parameters"]["required"] == ["package"]


def test_pip_install_mock_execution():
    reg = ToolRegistry(executor=MagicMock())

    mock_proc = MagicMock()
    mock_proc.stdout = ["Collecting geoai-py\n", "Successfully installed geoai-py-1.0.0\n"]
    mock_proc.returncode = 0
    mock_proc.wait.return_value = 0

    with patch("subprocess.Popen", return_value=mock_proc):
        result = asyncio.run(reg._execute_pip_install({"package": "geoai-py"}))
        assert "Successfully installed geoai-py" in result
