"""Regression tests for the oauth_helper import-fragility fix (P5 Step 1).

Before the fix, ``oauth_helper`` was only a re-export in
``aery_plugin/__init__.py``. That broke ``import aery_plugin.oauth_helper``
when the package __init__ hadn't run yet (and is fragile across Python
versions / QGIS plugin loaders).

The fix is a real module file at ``aery_plugin/oauth_helper.py`` that
aliases itself to ``aery_plugin.core.ai.auth`` in ``sys.modules`` so:

  1. ``import aery_plugin.oauth_helper`` works unconditionally.
  2. ``aery_plugin.oauth_helper is aery_plugin.core.ai.auth`` holds.
  3. Every public symbol on ``auth`` is accessible through ``oauth_helper``.

These tests intentionally avoid clearing ``sys.modules`` — that breaks
module caching for the rest of the suite. The shim's correctness can be
verified without disturbing the import state.
"""


def test_oauth_helper_imports_directly():
    """`import aery_plugin.oauth_helper` must succeed."""
    import aery_plugin.oauth_helper  # noqa: F401
    assert aery_plugin.oauth_helper is not None


def test_oauth_helper_identity_matches_canonical():
    """Legacy callers compare ``oauth_helper is aery_plugin.core.ai.auth``."""
    import aery_plugin.core.ai.auth as canonical
    import aery_plugin.oauth_helper as shim
    assert shim is canonical


def test_oauth_helper_exposes_full_auth_surface():
    """Every public symbol on ``auth`` must be reachable through the shim."""
    import aery_plugin.core.ai.auth as canonical
    import aery_plugin.oauth_helper as shim

    # Spot-check the most-used symbols.
    assert shim.get_auth_entry is canonical.get_auth_entry
    assert shim.refresh_oauth_token is canonical.refresh_oauth_token
    assert shim.OAUTH_CONFIGS is canonical.OAUTH_CONFIGS
    assert shim.API_PROVIDERS is canonical.API_PROVIDERS
    assert shim.AGENT_DIR == canonical.AGENT_DIR


def test_oauth_helper_registered_in_sys_modules_as_auth():
    """The shim must register itself in sys.modules as the auth module so
    that ``import aery_plugin.oauth_helper`` works regardless of import order."""
    import sys

    import aery_plugin.core.ai.auth as canonical
    import aery_plugin.oauth_helper as shim

    assert sys.modules["aery_plugin.oauth_helper"] is canonical
    assert shim is canonical
