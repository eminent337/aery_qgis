"""Minimal, non-destructive plugin update check utility.

Adapted from GeoAI (opengeos/geoai) update_checker.py.
Compares installed plugin metadata.txt version with the remote GitHub version.
"""

from __future__ import annotations

import re
import urllib.request
from typing import Optional, Tuple


def parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """Parse a semver string like '1.2.3' into a comparable tuple of ints."""
    clean = re.sub(r"[^\d.]", "", version_str.strip())
    parts = [int(p) for p in clean.split(".") if p.isdigit()]
    return tuple(parts)


def is_newer(installed: str, remote: str) -> bool:
    """Return True if remote version is strictly newer than installed."""
    return parse_version_tuple(remote) > parse_version_tuple(installed)


def fetch_remote_version(
    repo: str = "eminent337/aery_qgis",
    branch: str = "main",
    plugin_path: str = "aery_plugin",
    timeout: int = 5,
) -> Tuple[Optional[str], Optional[str]]:
    """Fetch the version string from GitHub raw metadata.txt.

    Returns:
        (version, None) on success, or (None, error_message) on failure.
    """
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{plugin_path}/metadata.txt"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Aery-QGIS-Plugin"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
        match = re.search(r"^version\s*=\s*(.+)$", content, re.MULTILINE)
        if match:
            return match.group(1).strip(), None
        return None, "Version field not found in remote metadata.txt"
    except Exception as e:
        return None, str(e)
