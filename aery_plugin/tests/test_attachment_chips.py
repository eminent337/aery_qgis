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


def _autosized_area():
    """Build an InputArea with a laid-out 400px-wide document.
    Uses ``document().setTextWidth()`` so ``autosize()`` computes real heights
    without showing a window (window geometry is async under QGIS and would
    make these assertions timing-dependent). Heights are read from the size
    constraints ``setFixedHeight`` sets, which are synchronous.
    """
    area = InputArea(on_send=MagicMock(), on_abort=MagicMock())
    area.input.document().setTextWidth(400)
    return area


def test_autosize_grows_input_with_multiline_text():
    """Long prompt text must grow the editor and its box frame instead of being clipped."""
    from aery_plugin.input_area import PROMPT_MIN_HEIGHT

    area = _autosized_area()
    area.input.setPlainText("line 1\nline 2\nline 3")
    area.autosize()

    # Editor grew above the single-line minimum for multi-line content.
    assert area.input.maximumHeight() > PROMPT_MIN_HEIGHT
    # The box frame is no longer pinned at the single-line height (the bug that
    # clipped long input): it only has a minimum, so it can grow with the editor.
    assert area._box_frame.minimumHeight() == PROMPT_MIN_HEIGHT
    assert area._box_frame.maximumHeight() > PROMPT_MIN_HEIGHT


def test_autosize_caps_input_height_and_recovers():
    """Very long input caps at PROMPT_MAX_HEIGHT, and clearing shrinks back."""
    from aery_plugin.input_area import PROMPT_MIN_HEIGHT, PROMPT_MAX_HEIGHT

    area = _autosized_area()
    long_text = "\n".join("word " * 12 for _ in range(40))
    area.input.setPlainText(long_text)
    area.autosize()
    assert area.input.maximumHeight() == PROMPT_MAX_HEIGHT

    area.input.clear()
    area.autosize()
    assert area.input.maximumHeight() == PROMPT_MIN_HEIGHT


def test_autosize_accounts_for_attachment_chips():
    """Chips row height is included so chips are not clipped when the prompt grows."""
    area = _autosized_area()
    area.input.setPlainText("line 1\nline 2\nline 3")
    area.autosize()
    base_height = area.maximumHeight()

    area.add_attachment("/tmp/roads.gpkg")
    area.add_attachment("/tmp/ortho.tif")
    assert area._chips_container.isHidden() is False
    assert area.maximumHeight() > base_height  # chips row adds height on top of the prompt
