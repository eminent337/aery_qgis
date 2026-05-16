#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"
REBUILD=false
DEPLOY=false
SKIP_TESTS=false
PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
QGIS_PROFILE="${QGIS_PROFILE:-$HOME/.local/share/QGIS/QGIS4/profiles/default}"

for arg in "$@"; do
    case "$arg" in
        --rebuild) REBUILD=true ;;
        --deploy) DEPLOY=true ;;
        --skip-tests) SKIP_TESTS=true ;;
    esac
done

if [ -z "$VERSION" ] || [[ "$VERSION" == --* ]]; then
    # Auto-bump patch version from metadata.txt
    CURRENT=$(grep '^version=' "$PLUGIN_DIR/aery_plugin/metadata.txt" | cut -d= -f2)
    IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"
    VERSION="$MAJOR.$MINOR.$((PATCH + 1))"
    echo "Auto-bumping version: $CURRENT -> $VERSION"
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: version must be semver (e.g. 0.4.0)"
    exit 1
fi

echo "=== Aery QGIS Plugin Build v$VERSION ==="

if [ "$SKIP_TESTS" = false ]; then
    echo "--- Running tests ---"
    cd "$PLUGIN_DIR"
    python3 -m pytest tests/ -v --tb=short --ignore=tests/test_integration.py
    echo "Tests passed."
fi

echo "--- Updating metadata.txt ---"
sed -i "s/^version=.*/version=$VERSION/" "$PLUGIN_DIR/aery_plugin/metadata.txt"
echo "Version set to $VERSION"

if [ "$REBUILD" = true ]; then
    echo "--- Rebuilding binary ---"
    cd "$PLUGIN_DIR/../aery-core/packages/coding-agent"
    npm run build
    npm run build:binary 2>/dev/null || true  # copy-binary-assets may fail for non-critical files
    cp dist/pi "$PLUGIN_DIR/aery_plugin/bin/aery-qgis-runner"
    echo "Binary rebuilt and copied."
fi

echo "--- Assembling ZIP ---"
ZIP_NAME="aery_qgis_$VERSION.zip"
ZIP_PATH="$PLUGIN_DIR/dist/$ZIP_NAME"
mkdir -p "$PLUGIN_DIR/dist"
cd "$PLUGIN_DIR"
zip -r "$ZIP_PATH" aery_plugin/ -x "aery_plugin/__pycache__/*" "aery_plugin/*.pyc" "aery_plugin/.pytest_cache/*" "aery_plugin/**/*.pyc" "aery_plugin/provider_config.json"
ZIP_SIZE=$(du -h "$ZIP_PATH" | cut -f1)
echo "ZIP created: $ZIP_PATH ($ZIP_SIZE)"

if [ "$DEPLOY" = true ]; then
    echo "--- Deploying to QGIS profile ---"
    rm -rf "$QGIS_PROFILE/python/plugins/aery_plugin"
    unzip -q "$ZIP_PATH" -d "$QGIS_PROFILE/python/plugins/"
    echo "Deployed to $QGIS_PROFILE/python/plugins/aery_plugin"
fi

echo "=== Build complete: v$VERSION ==="
