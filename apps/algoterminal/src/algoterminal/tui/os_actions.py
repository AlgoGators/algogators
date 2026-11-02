"""Launch external tools (file manager, VS Code) for a research record's files.

Each action is fire-and-forget: we hand off to the OS/editor and don't wait
for or capture output, since there's nothing meaningful to report back to
the TUI beyond "it launched."
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def find_vscode() -> str | None:
    return shutil.which("code")


def can_open_file_location() -> bool:
    if sys.platform == "win32":
        return shutil.which("explorer") is not None
    if sys.platform == "darwin":
        return shutil.which("open") is not None
    return shutil.which("xdg-open") is not None


def open_file_location(path: Path) -> None:
    """Open the OS file manager, selecting `path` if it's a file that exists."""
    if sys.platform == "win32":
        if path.is_file():
            # Windows' Explorer only recognizes `/select,<path>` when it
            # arrives as one literal command-line token, which requires
            # bypassing subprocess's normal per-argument quoting (list2cmdline
            # would quote `/select,` and the path separately and break it).
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            subprocess.Popen(["explorer", str(path)])
    elif sys.platform == "darwin":
        args = ["open", "-R", str(path)] if path.is_file() else ["open", str(path)]
        subprocess.Popen(args)
    else:
        target = path if path.is_dir() else path.parent
        subprocess.Popen(["xdg-open", str(target)])


def open_in_vscode(path: Path) -> None:
    code_bin = find_vscode()
    if code_bin is None:
        raise FileNotFoundError("VS Code CLI ('code') not found on PATH.")
    subprocess.Popen([code_bin, str(path)])
