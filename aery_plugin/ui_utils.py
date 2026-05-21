"""Shared UI utilities for Aery chat panel widgets."""

import re
from datetime import datetime


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def now_stamp() -> str:
    return datetime.now().strftime("%H:%M")


def format_text_html(text: str) -> str:
    from aery_plugin.ui_constants import BG_BASE, BG_HIGH, BORDER, ACCENT, FONT_MONO, TEXT_DIM

    if not text:
        return ""
    text = re.sub(r"```[\w]*\n.*?```", r"[code executed in tool]", text, flags=re.DOTALL)
    html = escape_html(text)
    html = re.sub(
        r"```(\w*)\n?(.*?)```",
        lambda m: (
            f"<pre style='background:{BG_BASE};border:1px solid {BORDER};"
            f"border-radius:4px;padding:8px;font-family:{FONT_MONO};"
            f"font-size:11px;line-height:1.45;color:{ACCENT};white-space:pre-wrap;'>"
            f"{m.group(2).strip()}</pre>"
        ),
        html,
        flags=re.DOTALL,
    )
    html = re.sub(
        r"`([^`]+)`",
        lambda m: (
            f"<code style='background:{BG_HIGH};padding:1px 5px;border-radius:3px;"
            f"font-family:{FONT_MONO};color:{ACCENT};'>{m.group(1)}</code>"
        ),
        html,
    )
    html = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", html)
    return html.replace("\n", "<br>")


def format_thinking_html(text: str) -> str:
    if not text:
        return ""
    html = escape_html(text)
    html = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", html)
    return html.replace("\n", "<br>")
