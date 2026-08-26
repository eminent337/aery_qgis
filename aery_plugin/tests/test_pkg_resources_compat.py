"""Tests for pkg_resources compatibility shim."""

import sys
from aery_plugin._pkg_resources_compat import ensure_pkg_resources, _build_module


def test_build_module_provides_required_api():
    shim = _build_module()
    assert hasattr(shim, "get_distribution")
    assert hasattr(shim, "parse_version")
    assert hasattr(shim, "resource_filename")
    assert hasattr(shim, "require")
    assert hasattr(shim, "DistributionNotFound")


def test_parse_version_tuple_fallback():
    shim = _build_module()
    v = shim.parse_version("1.2.3")
    assert v is not None
