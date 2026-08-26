"""Tests for text-only LLM vision fallback descriptor."""

from aery_plugin.engine.vision import describe_canvas_fallback, analyze_with_vision_model


def test_describe_canvas_fallback():
    summary = describe_canvas_fallback("data:image/png;base64,dummy")
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_analyze_with_vision_model():
    res = analyze_with_vision_model("/fake/path.png")
    assert isinstance(res, str)
    assert len(res) > 0
