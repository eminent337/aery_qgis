"""Shared UI constants for Aery chat panel widgets."""

BG_BASE = "#0D0E15"
BG_SURFACE = "#12131A"
BG_PANEL = "#1A1B22"
BG_HIGH = "#292931"
BG_CARD = "#1E1F26"
ACCENT = "#57F1DB"
ACCENT_DIM = "#2DD4BF"
BORDER = "#3C4A46"
TEXT_MAIN = "#E3E1EC"
TEXT_DIM = "#BACAC5"
TEXT_MUTED = "#859490"
ERROR_COLOR = "#FFB4AB"
WARNING_COLOR = "#FFD1AA"
SUCCESS_COLOR = "#8EE7A8"
FONT_SANS = "Inter, Aptos, Segoe UI, sans-serif"
FONT_MONO = "JetBrains Mono, Consolas, monospace"

# Legacy aliases used by older widgets (approval_flow, diff_widget). Kept so
# those modules stay importable; prefer the newer names above.
TEXT = TEXT_MAIN
DIM = TEXT_DIM
RED = ERROR_COLOR
F_H = 14
F_S = 11
