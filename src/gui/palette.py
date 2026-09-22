"""Unified GUI color palette.

Colors are tuples (light, dark): all CTk widgets natively understand them.
Status elements are tinted "badges" (muted background + saturated text)
instead of solid fills: they read more calmly in both themes and don't
turn the panel into a "traffic light".
"""

# Accent (launch buttons, progress, list markers).
APP_BG = ("#EEF2F3", "#15191B")
SIDEBAR_BG = ("#E2EAEC", "#20272A")
PANEL_BG = ("#FFFFFF", "#202628")
PANEL_ALT_BG = ("#F6F9F9", "#181E20")
FIELD_BG = ("#FAFCFC", "#171C1E")
TEXT = ("#172024", "#F1F5F5")

ACCENT = ("#087F75", "#28C7B7")
ACCENT_HOVER = ("#05675F", "#1AA899")

# Muted text (labels, column headers, "—" in the matrix).
MUTED = ("#637176", "#9BAAAB")

# Status badges: (background, text) for light/dark.
STATUS_BADGE = {
    "idle": (("#E8EEEE", "#293235"), ("#637176", "#9BAAAB")),
    "pending": (("#E8EEEE", "#293235"), ("#637176", "#9BAAAB")),
    "running": (("#D5F2ED", "#123C3A"), ("#087F75", "#5DE0D2")),
    "done": (("#D9F1E1", "#153624"), ("#167345", "#5FDA94")),
    "error": (("#FBE0E2", "#421D24"), ("#B42336", "#FF9EAA")),
    "timeout": (("#FCEBCB", "#40310E"), ("#9A6500", "#FFD166")),
}
