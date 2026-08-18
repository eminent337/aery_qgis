"""Pytest configuration for Aery QGIS plugin tests.

Provides QGIS application fixture and sandbox mocks.
"""
import os
import sys

import pytest


@pytest.fixture(scope="session", autouse=True)
def _qgis_app():
    """Initialize QGIS application for tests that require real Qgs objects.

    Tests that only need sandboxing/proxies should use pytest.mark.no_qgis.
    """
    try:
        from qgis.core import QgsApplication
        app = QgsApplication([], False)
        app.initQgis()
        yield app
        app.exitQgis()
    except Exception:
        # QGIS not available in this environment; tests requiring real Qgs objects will be skipped
        pytest.skip("QGIS application not available", allow_module_level=True)
