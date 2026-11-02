"""Read the member roster CSV: first_name,last_name,email,team,is_leadership."""

from __future__ import annotations

import csv
from pathlib import Path

from platform_db.people_import.model import TEAM_SLUGS, MemberRow, clean_text, normalize_email

_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0", ""}


def read_members(path: str | Path) -> list[MemberRow]:
    """Every row as a MemberRow. Errors name the offending email.

    ``team`` is a slug from people.team or blank (leadership without a team
    placement is a legitimate state). ``is_leadership`` is optional and
    defaults to false.
    """
    path = Path(path)
    out: list[MemberRow] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = {(f or "").strip().lower() for f in reader.fieldnames or ()}
        for required in ("first_name", "last_name", "email"):
            if required not in fields:
                raise ValueError(f"{path.name}: missing column {required!r}")
        for n, row in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): v for k, v in row.items()}
            email = normalize_email(row.get("email"))
            first = clean_text(row.get("first_name"))
            last = clean_text(row.get("last_name"))
            team = clean_text(row.get("team"))
            lead_text = (clean_text(row.get("is_leadership")) or "").lower()
            where = f"{path.name} line {n} ({email or 'no email'})"
            if email is None:
                raise ValueError(f"{where}: missing email")
            if first is None or last is None:
                raise ValueError(f"{where}: missing first or last name")
            if email in seen:
                raise ValueError(f"{where}: duplicate email {email}")
            if team is not None and team not in TEAM_SLUGS:
                raise ValueError(f"{where}: unknown team {team!r}; expected one of {TEAM_SLUGS}")
            if lead_text in _TRUE:
                lead = True
            elif lead_text in _FALSE:
                lead = False
            else:
                raise ValueError(f"{where}: is_leadership must be true/false, got {lead_text!r}")
            seen.add(email)
            out.append(
                MemberRow(
                    first_name=first, last_name=last, email=email, team=team, is_leadership=lead
                )
            )
    return out
