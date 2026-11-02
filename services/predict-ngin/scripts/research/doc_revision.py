"""Keep the `Last revised: YYYY-MM-DD` line of a generated Markdown doc honest.

Docs are named by topic, never by date (docs/AGENT_GUIDE.md, "Docs: topic names, not dates").
Each doc carries one `Last revised:` line under its title. Generators call `stamp()` so the date
moves only when the content changes: rerunning a generator on unchanged inputs leaves the file
byte-identical, which keeps `--check` modes working.
"""
from __future__ import annotations

import datetime as dt
import re

LINE = re.compile(r"^Last revised: \d{4}-\d{2}-\d{2}\n\n?", re.M)


def _body(text: str) -> str:
    return LINE.sub("", text, count=1)


def stamp(new: str, old: str | None, today: str | None = None) -> str:
    """Return `new` with a `Last revised:` line under its first `# ` title.

    The date is kept from `old` when `old` matches `new` apart from that line; else `today`.
    """
    date = None
    if old is not None and _body(old) == _body(new):
        m = re.search(r"^Last revised: (\d{4}-\d{2}-\d{2})$", old, re.M)
        date = m.group(1) if m else None
    date = date or today or dt.date.today().isoformat()
    body = _body(new)
    line = f"Last revised: {date}\n\n"
    m = re.search(r"^# .*\n", body, re.M)
    if m is None:
        return line + body
    return body[: m.end()] + "\n" + line + body[m.end():].lstrip("\n")
