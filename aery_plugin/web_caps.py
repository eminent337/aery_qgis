"""Response read helpers with hard byte caps.

GeoLibre caps network responses (e.g. `readTextCapped`, 100 MB) so an
unbounded `resp.read()` cannot OOM the plugin. These helpers apply the same
discipline to the plugin's web-facing tools (Nominatim, Overpass, WMS, web
search). A cap violation raises a friendly error instead of buffering the
whole body.
"""
from __future__ import annotations

import urllib.error

# Default cap: 10 MiB. WMS imagery can legitimately be large, so callers may
# pass a larger limit for specialized fetches.
DEFAULT_MAX_BYTES = 10 * 1024 * 1024


class ResponseTooLargeError(urllib.error.URLError):
    """Raised when a response body exceeds the configured byte cap."""


def read_capped(resp, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
    """Read a urllib response body, enforcing a hard byte cap.

    Reads in chunks so a malicious/buggy Content-Length cannot bypass the
    guard, and stops as soon as the cap is exceeded (no unbounded buffering).
    """
    chunks = []
    total = 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(
                f"Response exceeded {max_bytes} byte limit ({total} bytes seen)"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def read_capped_text(
    resp,
    max_bytes: int = DEFAULT_MAX_BYTES,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> str:
    """ReadCapped then decode to text."""
    return read_capped(resp, max_bytes).decode(encoding, errors=errors)