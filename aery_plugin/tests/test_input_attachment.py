"""Unit tests for InputArea attachment button and drag-and-drop file ingestion."""

from unittest.mock import MagicMock
from aery_plugin.input_area import InputArea, PromptInput


def test_input_area_attachment_button():
    on_attach = MagicMock()
    area = InputArea(on_send=MagicMock(), on_abort=MagicMock(), on_attach=on_attach)
    assert hasattr(area, "_attach_btn")
    area._attach_btn.click()
    on_attach.assert_called_once()


def test_prompt_input_drop_callback():
    dropped_files = []
    inp = PromptInput(
        submit_callback=MagicMock(),
        abort_callback=MagicMock(),
        file_dropped_callback=lambda paths: dropped_files.extend(paths),
    )
    assert inp.acceptDrops() is True
    inp._file_dropped_callback(["/tmp/sample.gpkg", "/tmp/sample.tif"])
    assert dropped_files == ["/tmp/sample.gpkg", "/tmp/sample.tif"]
