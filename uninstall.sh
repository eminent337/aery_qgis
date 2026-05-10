#!/bin/bash
# Uninstall Aery QGIS Plugin

PLUGIN_DIR="$HOME/.local/share/QGIS/QGIS4/profiles/default/python/plugins"
TARGET="$PLUGIN_DIR/aery_plugin"

if [ -L "$TARGET" ] || [ -d "$TARGET" ]; then
    rm -rf "$TARGET"
    echo "Removed Aery plugin from: $TARGET"
else
    echo "Aery plugin not found at: $TARGET"
fi
