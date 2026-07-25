"""AlgoGators brand theme for the Textual TUI.

Colors and type treatment are pulled from algogators.com: a near-black
"systematic" dark mode with a single hot-orange accent, cream-white text,
and terminal/monospace flourishes (the site itself renders its labels in
IBM Plex Mono and opens every section with a `>` prompt).
"""

from __future__ import annotations

from textual.theme import Theme

ORANGE = "#FF5C00"
CREAM = "#F6F4EF"
INK = "#14141A"
BACKGROUND = "#0A0A0A"
SURFACE = "#131316"
PANEL = "#1C1C1F"
SUCCESS = "#2ECC71"
WARNING = "#FFB020"
ERROR = "#FF4136"

ALGOGATORS_THEME = Theme(
    name="algogators",
    primary=ORANGE,
    secondary=CREAM,
    accent=ORANGE,
    warning=WARNING,
    error=ERROR,
    success=SUCCESS,
    foreground=CREAM,
    background=BACKGROUND,
    surface=SURFACE,
    panel=PANEL,
    dark=True,
    variables={
        "block-cursor-foreground": INK,
        "block-cursor-background": ORANGE,
        "block-cursor-text-style": "bold",
        "footer-key-foreground": ORANGE,
        "border": ORANGE,
        "scrollbar": PANEL,
        "scrollbar-hover": ORANGE,
        "scrollbar-active": ORANGE,
    },
)

# `> systematic trading engine — est. 2023 ...` echoes the site's hero prompt.
BANNER = r"""
    _    _             ____       _
   / \  | | __ _  ___  / ___| __ _| |_ ___  _ __ ___
  / _ \ | |/ _` |/ _ \| |  _ / _` | __/ _ \| '__/ __|
 / ___ \| | (_| | (_) | |_| | (_| | || (_) | |  \__ \
/_/   \_\_|\__, |\___/ \____|\__,_|\__\___/|_|  |___/
           |___/
""".strip("\n")

TAGLINE = "Data Driven, Student Run."
PROMPT = "> quantitative research workbench — est. 2023 ..."
