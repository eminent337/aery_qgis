"""Tests for update checker utility."""

from aery_plugin.update_check import parse_version_tuple, is_newer


def test_parse_version_tuple():
    assert parse_version_tuple("0.1.0") == (0, 1, 0)
    assert parse_version_tuple("1.2.3.4") == (1, 2, 3, 4)
    assert parse_version_tuple("v2.0-beta") == (2, 0)


def test_is_newer():
    assert is_newer("0.1.0", "0.1.1") is True
    assert is_newer("0.1.0", "0.2.0") is True
    assert is_newer("0.1.0", "1.0.0") is True
    assert is_newer("0.1.0", "0.1.0") is False
    assert is_newer("0.2.0", "0.1.9") is False
