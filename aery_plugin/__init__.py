"""Aery AI Agent for QGIS."""

# Defensive: ensure pkg_resources is available for dependencies that require it
try:
    from aery_plugin._pkg_resources_compat import ensure_pkg_resources
    ensure_pkg_resources()
except Exception:
    pass


def classFactory(iface):
    from aery_plugin.plugin import AeryPlugin
    return AeryPlugin(iface)
