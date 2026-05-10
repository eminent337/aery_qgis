"""Aery AI Agent for QGIS."""


def classFactory(iface):
    from aery_plugin.plugin import AeryPlugin
    return AeryPlugin(iface)
