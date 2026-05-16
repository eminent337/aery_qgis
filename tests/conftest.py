"""Pytest configuration for aery_plugin tests.

Adds the project root to sys.path so that `import aery_plugin.*` works
without requiring QGIS to be installed as a package.
"""

import sys
import os

# Project root = one level up from tests/
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
