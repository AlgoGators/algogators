"""Records and pure normalisers shared by the readers and the importer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

#: Slugs seeded by 001_people_schema.sql. The roster CSV and the form value
#: map both key on these.
TEAM_SLUGS: tuple[str, ...] = ("quant_research", "quant_dev", "quant_trading", "investor_relations")

#: What the analyst form's ranking question says, mapped to a team slug. The
#: form says "Quant Dev" while people.team says "Quantitative Development"; a
#: lookup that matched nothing would look exactly like an unanswered question,
#: so an unknown value is an error rather than a silent skip.
FORM_TEAM_VALUES: dict[str, str] = {
    "quant dev": "quant_dev",
    "quant development": "quant_dev",
    "quant trading": "quant_trading",
    "quant research": "quant_research",
}

TERMS: tuple[str, ...] = ("spring", "summer", "fall")

_MONTH_TERM: dict[str, str] = {
    **dict.fromkeys(("jan", "feb", "mar", "apr", "may"), "spring"),
    **dict.fromkeys(("jun", "jul", "aug"), "summer"),
    **dict.fromkeys(("sep", "oct", "nov", "dec"), "fall"),
}


@dataclass(frozen=True)
class Cycle:
    term: str
    year: int

    def __post_init__(self) -> None:
        if self.term not in ("fall", "spring"):
            raise ValueError(f"cycle term must be 'fall' or 'spring', got {self.term!r}")
        if not 1900 <= self.year <= 2200:
            raise ValueError(f"cycle year out of range: {self.year}")

    @classmethod
    def parse(cls, text: str) -> Cycle:
        """``"fall-2026"`` / ``"fall 2026"`` / ``"Fall2026"`` -> Cycle."""
        m = re.fullmatch(r"\s*(fall|spring)[\s_-]*(\d{4})\s*", text, re.IGNORECASE)
        if not m:
            raise ValueError(f"cycle must look like 'fall-2026', got {text!r}")
        return cls(m.group(1).lower(), int(m.group(2)))

    def __str__(self) -> str:
        return f"{self.term} {self.year}"


@dataclass(frozen=True)
class Submission:
    """One row of a Forms export, normalised to what the schema stores."""

    response_id: str
    first_name: str
    last_name: str
    email: str
    grad_term: str | None
    grad_year: int | None
    class_standing: str | None
    majors: tuple[str, ...]
    minors: tuple[str, ...]
    resume_url: str | None
    strategy_url: str | None
    team_preferences: tuple[str, ...]
    submitted_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """Leadership's decision columns from the grading workbook."""

    interview: bool
    fund: str | None  # 'Y', 'N' or None (undecided)


@dataclass(frozen=True)
class MemberRow:
    first_name: str
    last_name: str
    email: str
    team: str | None
    is_leadership: bool


def normalize_email(value: object) -> str | None:
    """Trimmed, lowercased; None for blank or for something that is not an address.

    One applicant typed their UFID into the "UF Email" box. Rejecting that here
    lets the reader fall back to the login email Forms recorded for them.
    """
    text = str(value).strip().lower() if value is not None else ""
    if not text or "@" not in text or text.startswith("@") or text.endswith("@"):
        return None
    return text


def normalize_header(value: object) -> str:
    """Lowercase, NBSP -> space, whitespace collapsed. Headers only."""
    text = str(value or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_term(value: object) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    text = text.lower()
    return text if text in TERMS else None


def normalize_year(value: object) -> int | None:
    text = clean_text(value)
    if text is None:
        return None
    m = re.search(r"\d{4}", text)
    return int(m.group()) if m else None


def month_year_to_term_year(value: object) -> tuple[str, int] | None:
    """``"May, 2029"`` -> ``("spring", 2029)``. Older analyst forms asked this way."""
    text = clean_text(value)
    if text is None:
        return None
    m = re.search(r"([A-Za-z]{3,})[^\d]*(\d{4})", text)
    if not m:
        return None
    term = _MONTH_TERM.get(m.group(1)[:3].lower())
    return (term, int(m.group(2))) if term else None


_FIELD_SPLIT = re.compile(r"\s*(?:;|,|\+|&|\band\b)\s*")


def split_fields(value: object) -> tuple[str, ...]:
    """Split a free-text majors/minors answer on the separators people use.

    ``/`` is deliberately not a separator ("Professional Sales/AI Certificate"
    is one thing). Values are stored as typed apart from trimming; "CS" and
    "Computer Science" stay two different majors, by design.
    """
    text = clean_text(value)
    if text is None:
        return ()
    return tuple(part for part in (p.strip() for p in _FIELD_SPLIT.split(text)) if part)


def parse_team_preferences(value: object) -> tuple[str, ...]:
    """``"Quant Dev;Quant Trading;Quant Research;"`` -> slugs in rank order."""
    text = clean_text(value)
    if text is None:
        return ()
    slugs: list[str] = []
    for part in text.split(";"):
        name = part.strip()
        if not name:
            continue
        slug = FORM_TEAM_VALUES.get(name.lower())
        if slug is None:
            raise ValueError(f"unknown team in preference list: {name!r}")
        if slug not in slugs:
            slugs.append(slug)
    return tuple(slugs)


def filename_from_url(url: str) -> str:
    """The file name Forms gave the upload, recovered from either SharePoint URL shape.

    ``.../_layouts/15/Doc.aspx?...&file=Resume_Minh%20Nguyen.docx`` carries it in
    the ``file`` query parameter; a direct ``.../Resume/Pitch_Zhang%20Zhijun.pdf``
    link carries it as the last path segment.
    """
    parsed = urlparse(url)
    names = parse_qs(parsed.query).get("file")
    if names and names[0].strip():
        return names[0].strip()
    last = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]).strip()
    return last or url


def json_safe(value: object) -> object:
    """Cell value -> something json.dumps accepts, losing nothing a human wrote."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
