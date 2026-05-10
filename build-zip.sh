#!/usr/bin/env bash
set -euo pipefail

# ── Aery QGIS Plugin — Distribution ZIP Builder ──────────
# Packages the plugin for QGIS Plugin Manager submission.
# - Includes: aery_plugin/ (with compiled binary, no source)
# - Excludes: runner/, tests/, .pytest_cache, __pycache__
# - Preserves metadata.txt version for the ZIP filename

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PLUGIN_NAME="aery_plugin"
VERSION="$(grep '^version=' "$PLUGIN_NAME/metadata.txt" | cut -d= -f2 | tr -d ' \r\n')"
OUTPUT_ZIP="${PLUGIN_NAME}-v${VERSION}.zip"

echo "Building distribution ZIP for $PLUGIN_NAME v$VERSION..."
echo "Output: $OUTPUT_ZIP"

# Remove any previous build
rm -f "$OUTPUT_ZIP"

# Create ZIP containing only aery_plugin/ with its compiled binary
# Explicit exclusions:
#   runner/  — TypeScript source and bun deps (not for distribution)
#   tests/   — unit/integration tests (not for distribution)
#   __pycache__ — cached bytecode
#   *.pyc, *.pyo — compiled Python files

cd "$SCRIPT_DIR"
zip -r "$OUTPUT_ZIP" "$PLUGIN_NAME" \
    --exclude "$PLUGIN_NAME/__pycache__/*" \
    --exclude "$PLUGIN_NAME/**/__pycache__/*" \
    --exclude "*.pyc" \
    --exclude "*.pyo" \
    --exclude "$PLUGIN_NAME/tests/*" \
    --exclude "$PLUGIN_NAME/**/tests/*" \
    1>&2

echo ""
echo "Contents of $OUTPUT_ZIP:"
echo "  Size: $(du -h "$OUTPUT_ZIP" | cut -f1)"
echo "  Files:"
unzip -l "$OUTPUT_ZIP" | awk 'NR>3 && !/^--/ {print "    " $NF}' | head -20
echo "    ... ($(unzip -l "$OUTPUT_ZIP" | tail -1 | awk '{print $2}') total files)"
echo ""
echo "Done — ready for Plugin Manager upload: $OUTPUT_ZIP"
