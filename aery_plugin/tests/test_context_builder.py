import pytest
from aery_plugin.agent_context import ContextBuilder

def test_context_builder_initialization():
    cb = ContextBuilder()
    assert cb.last_layer_hash == ""

def test_layers_changed_no_qgis():
    cb = ContextBuilder()
    # Without QGIS loaded in tests, it should safely return False
    # thanks to the broad except clause in layers_changed()
    assert cb.layers_changed() is False

def test_build_context_message_no_qgis():
    cb = ContextBuilder()
    # Should safely return an empty string or gracefully degraded message
    # if QGIS is not running (which is the case in standard pytest)
    ctx = cb.build_context_message("find the highest point in the DEM layer", "/tmp")
    assert isinstance(ctx, str)
