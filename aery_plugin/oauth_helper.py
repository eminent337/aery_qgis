"""Provider auth helper for the Aery QGIS plugin.

Shim module: the canonical implementation lives at
``aery_plugin.core.ai.auth``. This module aliases itself to that module in
``sys.modules`` so that:

  1. ``import aery_plugin.oauth_helper`` works unconditionally.
  2. ``aery_plugin.oauth_helper is aery_plugin.core.ai.auth`` holds.
  3. Every public symbol on ``auth`` is accessible through ``oauth_helper``.

Only Kilo is supported as a provider; all other providers were removed.
"""

import sys

from aery_plugin.core.ai import auth as _auth

# Replace this module's sys.modules entry with the canonical module so the two
# names refer to the same module object. Subsequent imports of
# ``aery_plugin.oauth_helper`` return the canonical module.
sys.modules[__name__] = _auth