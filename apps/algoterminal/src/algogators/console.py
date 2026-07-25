"""Shared Rich console styled to match the AlgoGators brand (see `algogators.theme`)."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

from algogators.theme import ERROR, ORANGE, SUCCESS, WARNING

RICH_THEME = Theme(
    {
        "brand": f"bold {ORANGE}",
        "heading": f"bold {ORANGE}",
        "success": f"bold {SUCCESS}",
        "warning": f"bold {WARNING}",
        "error": f"bold {ERROR}",
    }
)

console = Console(theme=RICH_THEME)
