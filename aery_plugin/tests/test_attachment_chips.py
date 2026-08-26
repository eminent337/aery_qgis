"""Unit tests for modern AttachmentChip and InputArea chip preview bar."""

from unittest.mock import MagicMock
from aery_plugin.input_area import InputArea, AttachmentChip


def test_input_area_add_remove_chips():
    area = InputArea(on_send=MagicMock(), on_abort=MagicMock())
    assert area._chips_container.isHidden() is True
    assert len(area.get_attachments()) == 0

    # Add attachments
    area.add_attachment("/tmp/roads.gpkg")
    area.add_attachment("/tmp/ortho.tif")
    assert area._chips_container.isHidden() is False
    assert len(area.get_attachments()) == 2
    assert area.get_attachments() == ["/tmp/roads.gpkg", "/tmp/ortho.tif"]

    # Remove attachment
    area.remove_attachment("/tmp/roads.gpkg")
    assert len(area.get_attachments()) == 1
    assert area.get_attachments() == ["/tmp/ortho.tif"]

    # Clear all
    area.clear_attachments()
    assert area._chips_container.isHidden() is True
    assert len(area.get_attachments()) == 0


def test_attachment_chip_render():
    on_remove = MagicMock()
    chip = AttachmentChip("/path/to/my_very_long_file_name_for_testing.geojson", on_remove)
    assert chip.file_path == "/path/to/my_very_long_file_name_for_testing.geojson"
