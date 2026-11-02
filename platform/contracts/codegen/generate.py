#!/usr/bin/env python
"""Placeholder contract generator.

The migration will replace this with schema-to-Python and schema-to-C++ codegen.
For now it keeps `just contracts` executable and creates the committed output
directories expected by the repo layout.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "codegen" / "generated"


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def main() -> int:
    touch(GENERATED / "python" / ".gitkeep")
    touch(GENERATED / "cpp" / "include" / "algogators" / "contracts" / ".gitkeep")
    print("contract codegen placeholder: no schemas to generate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
