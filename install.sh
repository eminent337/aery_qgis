#!/bin/bash
# Install Aery QGIS Plugin
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$HOME/.local/share/QGIS/QGIS4/profiles/default/python/plugins"

if [ ! -d "$PLUGIN_DIR" ]; then
    echo "Error: Could not find QGIS plugins directory at $PLUGIN_DIR"
    exit 1
fi

TARGET="$PLUGIN_DIR/aery_plugin"

# Remove existing symlink or directory
if [ -L "$TARGET" ] || [ -d "$TARGET" ]; then
    rm -rf "$TARGET"
fi

# Create symlink from source aery_plugin/ dir
ln -s "$SCRIPT_DIR/aery_plugin" "$TARGET"
echo "Installed Aery plugin to: $TARGET"
echo ""
echo "Restart QGIS and enable the plugin in Plugins → Manage and Install Plugins → Aery"
