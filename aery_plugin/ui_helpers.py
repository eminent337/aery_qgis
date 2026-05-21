"""Shared UI helpers for Aery chat panel.

Extracted from chat_panel.py to reduce file size and enable reuse.
"""
import re
from enum import Enum
from typing import Any, Optional

from aery_plugin.ui_constants import ACCENT_DIM
from aery_plugin.ui_utils import escape_html as _escape


class SessionState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    REQUIRES_ACTION = "requires_action"


# ── layer name cache ───────────────────────────────────────────────────────────
_layer_name_cache: list[str] = []


def refresh_layer_cache() -> None:
    """Populate _layer_name_cache from the current QGIS project."""
    global _layer_name_cache
    try:
        from qgis.core import QgsProject
        _layer_name_cache = [lyr.name() for lyr in QgsProject.instance().mapLayers().values()]
    except Exception:
        _layer_name_cache = []


# ── HTML formatting ─────────────────────────────────────────────────────────────
def format_text_html(text: str) -> str:
    """Convert agent/markdown text to compact HTML, linkifying known layer names."""
    import re
    from aery_plugin.ui_constants import ACCENT_DIM
    from aery_plugin.ui_utils import escape_html as _esc

    html = _esc(text)
    # code fences → <pre>
    html = re.sub(
        r"```(\w*)\n(.*?)```",
        lambda m: f"<pre style='color:#e4e4e7;background:#09090b;"
                  f"border:1px solid #27272a;border-radius:6px;"
                  f"padding:8px 12px;font-family:\"JetBrains Mono\",monospace;"
                  f"font-size:11px;overflow-x:auto;white-space:pre-wrap;"
                  f"margin:6px 0'>{_esc(m.group(2))}</pre>",
        html,
        flags=re.DOTALL,
    )
    # inline `code`
    html = re.sub(
        r"`([^`]+)`",
        r"<code style='color:#57F1DB;background:#12131A;border-radius:3px;"
        r"padding:1px 4px;font-size:11px'>\1</code>",
        html,
    )
    # bold
    html = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", html)
    # italic
    html = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", html)
    # links
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" style="color:#2DD4BF;text-decoration:underline">\1</a>', html)
    # paragraphs
    for block in html.split("\n\n"):
        stripped = block.strip()
        if stripped and not stripped.startswith("<"):
            html = html.replace(stripped, f"<p style='margin:4px 0'>{stripped}</p>", 1)
    # linkify layer names from cache
    for name in _layer_name_cache:
        esc = _escape(name)
        if esc in html:
            html = html.replace(
                esc,
                f"<a href='layer://{esc}' style='color:#57F1DB;"
                f"text-decoration:underline'>{esc}</a>",
                1,
            )
    return html
