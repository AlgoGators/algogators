"""Read a Microsoft Forms .xlsx export (and the grading workbook's decisions)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import openpyxl

from platform_db.people_import.model import (
    Decision,
    Submission,
    clean_text,
    json_safe,
    month_year_to_term_year,
    normalize_email,
    normalize_header,
    normalize_term,
    normalize_year,
    parse_team_preferences,
    split_fields,
)

#: Forms exports are stamped in the form owner's local time with no zone.
DEFAULT_TZ = "America/New_York"

#: Column roles -> the header prefixes that fill them, in priority order. A
#: header matches a prefix when its normalised text starts with it, which is
#: what lets the same reader cope with the analyst form's long question text,
#: the relations form's "Gradation Year" typo, and a NBSP in "Phone Number".
COLUMN_PREFIXES: dict[str, tuple[str, ...]] = {
    "id": ("id",),
    "completion_time": ("completion time",),
    "start_time": ("start time",),
    "first_name": ("first name",),
    "last_name": ("last name",),
    "uf_email": ("uf email",),
    "email": ("email",),
    "grad_year": ("graduation year", "gradation year"),
    "grad_term": ("graduation semester",),
    "class_standing": ("graduation classification",),
    "grad_month_year": ("expected date of graduation",),
    "majors": ("major(s)", "majors", "major"),
    "minors": ("minor(s)", "minors", "minor"),
    "resume": ("resume",),
    "strategy": ("trading stategy", "trading strategy"),
    "team_preferences": ("which team are you most interested",),
}

#: The question_map stored on application_form for this reader — which
#: question feeds which column. Data, not code, so a later cycle can differ.
QUESTION_MAP: dict[str, dict[str, Any]] = {
    "Id": {"column": "application.external_response_id"},
    "Completion time": {
        "column": "application.submitted_at",
        "transform": f"localize:{DEFAULT_TZ}",
    },
    "First Name": {"column": "person.first_name"},
    "Last Name": {"column": "person.last_name"},
    "UF Email": {"column": "person.email", "transform": "lower"},
    "Email": {
        "column": "person.email",
        "transform": "lower",
        "note": "fallback when UF Email blank",
    },
    "Graduation Year": {"column": "student.grad_year"},
    "Graduation Semester": {"column": "student.grad_term", "transform": "lower"},
    "Graduation Classification": {"column": "student.class_standing"},
    "Expected Date of Graduation (eg. May, 2028)": {
        "column": "student.grad_term+grad_year",
        "transform": "month_year_to_term",
    },
    "Major(s)": {"column": "student_major[kind=major].field", "transform": "split_fields"},
    "Minor(s)": {"column": "student_major[kind=minor].field", "transform": "split_fields"},
    "Resume (PDF)": {"column": "attachment[kind=resume].source_url"},
    "Trading Stategy": {"column": "attachment[kind=trading_strategy].source_url"},
    "Which team are you most interested in joining?": {
        "column": "application_team_preference",
        "transform": "ranked_list",
        "values": {
            "Quant Dev": "quant_dev",
            "Quant Trading": "quant_trading",
            "Quant Research": "quant_research",
        },
    },
}


def _resolve_columns(header: Sequence[object]) -> dict[str, int]:
    """Map each column role to the first header index whose text matches."""
    normalised = [normalize_header(h) for h in header]
    roles: dict[str, int] = {}
    for role, prefixes in COLUMN_PREFIXES.items():
        for prefix in prefixes:
            hit = next(
                (
                    i
                    for i, h in enumerate(normalised)
                    if h.startswith(prefix) and i not in roles.values()
                ),
                None,
            )
            if hit is not None:
                roles[role] = hit
                break
    return roles


def _rows(path: Path, sheet: str | None) -> tuple[list[object], Iterator[tuple[object, ...]]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = list(next(it, ()))
    return header, it


def _cell(row: tuple[object, ...], cols: dict[str, int], role: str) -> object:
    i = cols.get(role)
    return row[i] if i is not None and i < len(row) else None


def read_submissions(
    path: str | Path, sheet: str | None = None, tz: str = DEFAULT_TZ
) -> list[Submission]:
    """Every non-blank row of the export as a Submission.

    Raises ``ValueError`` naming the row for anything the schema cannot accept:
    a missing email, an unknown team in the ranking, a missing response id.
    """
    path = Path(path)
    header, rows = _rows(path, sheet)
    cols = _resolve_columns(header)
    missing = [r for r in ("id", "completion_time", "first_name", "last_name") if r not in cols]
    if missing or ("email" not in cols and "uf_email" not in cols):
        raise ValueError(f"{path.name}: cannot find columns {missing or ['email']} in {header!r}")
    zone = ZoneInfo(tz)
    out: list[Submission] = []
    for row in rows:
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        response_id = clean_text(_cell(row, cols, "id"))
        label = f"{path.name} Id {response_id or '?'}"
        if response_id is None:
            raise ValueError(f"{label}: missing response Id")
        email = normalize_email(_cell(row, cols, "uf_email")) or normalize_email(
            _cell(row, cols, "email")
        )
        if email is None:
            raise ValueError(f"{label}: missing email")
        first = clean_text(_cell(row, cols, "first_name"))
        last = clean_text(_cell(row, cols, "last_name"))
        if first is None or last is None:
            raise ValueError(f"{label}: missing first or last name")
        completed = _cell(row, cols, "completion_time")
        if not isinstance(completed, datetime):
            raise ValueError(f"{label}: completion time is not a timestamp: {completed!r}")
        submitted_at = completed if completed.tzinfo else completed.replace(tzinfo=zone)

        term = normalize_term(_cell(row, cols, "grad_term"))
        year = normalize_year(_cell(row, cols, "grad_year"))
        if term is None or year is None:
            fallback = month_year_to_term_year(_cell(row, cols, "grad_month_year"))
            if fallback:
                term, year = term or fallback[0], year or fallback[1]
        try:
            prefs = parse_team_preferences(_cell(row, cols, "team_preferences"))
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from exc

        raw = {
            str(h): json_safe(row[i])
            for i, h in enumerate(header)
            if h is not None and i < len(row)
        }
        out.append(
            Submission(
                response_id=response_id,
                first_name=first,
                last_name=last,
                email=email,
                grad_term=term,
                grad_year=year,
                class_standing=clean_text(_cell(row, cols, "class_standing")),
                majors=split_fields(_cell(row, cols, "majors")),
                minors=split_fields(_cell(row, cols, "minors")),
                resume_url=clean_text(_cell(row, cols, "resume")),
                strategy_url=clean_text(_cell(row, cols, "strategy")),
                team_preferences=prefs,
                submitted_at=submitted_at,
                raw=raw,
            )
        )
    return out


def read_decisions(path: str | Path, sheet: str = "Composite Score") -> dict[str, Decision]:
    """Interview / fund-invite flags keyed by normalised email. ``{}`` if no such sheet."""
    path = Path(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet not in wb.sheetnames:
        return {}
    it = wb[sheet].iter_rows(values_only=True)
    header = [normalize_header(h) for h in next(it, ())]
    try:
        email_i = header.index("email")
    except ValueError:
        email_i = next((i for i, h in enumerate(header) if h.startswith("uf email")), -1)
    interview_i = next((i for i, h in enumerate(header) if h.startswith("interview invite")), None)
    fund_i = next((i for i, h in enumerate(header) if h.startswith("fund invite")), None)
    if email_i < 0:
        return {}
    out: dict[str, Decision] = {}
    for row in it:
        email = normalize_email(row[email_i] if email_i < len(row) else None)
        if email is None:
            continue
        interview = _flag(row, interview_i) == "Y"
        fund = _flag(row, fund_i)
        out[email] = Decision(interview=interview, fund=fund if fund in ("Y", "N") else None)
    return out


def _flag(row: tuple[object, ...], i: int | None) -> str | None:
    if i is None or i >= len(row):
        return None
    text = clean_text(row[i])
    return text.upper() if text else None
