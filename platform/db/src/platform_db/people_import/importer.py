"""Write submissions and roster rows into the `people` schema, one batch per call.

Everything here is idempotent: re-running the same workbook finds the rows it
made last time (person by lower(email), application by (form, response id),
attachment by (person, kind, url), and so on) and updates rather than
duplicates. Every write happens inside one transaction with the session
settings ``people.actor`` and ``people.batch_id`` set, so the schema's
triggers stamp every change_log row with who did it and which batch it
belongs to. ``dry_run=True`` does all of it and then rolls back.

The connection is a psycopg2 connection with autocommit off. The caller
commits; this module only ever rolls back (on dry run or error).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from platform_db.people_import.forms import QUESTION_MAP
from platform_db.people_import.model import (
    Cycle,
    Decision,
    MemberRow,
    Submission,
    filename_from_url,
)

STAGE_RANK = {"submitted": 1, "screen": 2, "interview": 3, "final": 4}


@dataclass
class ImportReport:
    """What an import did (or, on a dry run, would do)."""

    counts: Counter[str] = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    batch_id: int | None = None
    dry_run: bool = False

    def note(self, text: str) -> None:
        self.notes.append(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)

    def summary(self) -> str:
        head = (
            "DRY RUN — nothing written"
            if self.dry_run
            else f"applied as import_batch {self.batch_id}"
        )
        lines = [head, ""]
        for key in sorted(self.counts):
            lines.append(f"  {key:32s} {self.counts[key]}")
        if self.notes:
            lines += ["", "notes:"] + [f"  - {n}" for n in self.notes]
        if self.warnings:
            lines += ["", "warnings:"] + [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines)


class _Db:
    """The handful of queries the importer needs, kept out of the business logic."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def one(self, sql: str, *args: object) -> tuple[Any, ...] | None:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            row = cur.fetchone()
            return tuple(row) if row is not None else None

    def all(self, sql: str, *args: object) -> list[tuple[Any, ...]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return [tuple(r) for r in cur.fetchall()]

    def run(self, sql: str, *args: object) -> int:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return int(cur.rowcount)

    # -- batch bookkeeping -------------------------------------------------

    def open_batch(
        self, *, source: str, filename: str | None, sha: str | None, actor: str, note: str
    ) -> int:
        row = self.one(
            "INSERT INTO people.import_batch (source, filename, file_sha256, actor, note)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            source,
            filename,
            sha,
            actor,
            note,
        )
        assert row is not None
        batch_id = int(row[0])
        # Session settings the change_log trigger reads; LOCAL so they die with the transaction.
        self.run("SELECT set_config('people.actor', %s, true)", actor)
        self.run("SELECT set_config('people.batch_id', %s, true)", str(batch_id))
        return batch_id

    def close_batch(self, batch_id: int, status: str, row_count: int) -> None:
        self.run(
            "UPDATE people.import_batch SET status = %s, row_count = %s, finished_at = now() WHERE id = %s",
            status,
            row_count,
            batch_id,
        )

    # -- lookups / upserts -------------------------------------------------

    def team_ids(self) -> dict[str, int]:
        return {
            slug: int(i)
            for i, slug in self.all("SELECT id, slug FROM people.team WHERE deleted_at IS NULL")
        }

    def find_person(self, email: str) -> tuple[int, str, str] | None:
        row = self.one(
            "SELECT id, first_name, last_name FROM people.person WHERE lower(email) = lower(%s) AND deleted_at IS NULL",
            email,
        )
        return (int(row[0]), str(row[1]), str(row[2])) if row else None

    def insert_person(self, first: str, last: str, email: str, person_type: str | None) -> int:
        row = self.one(
            "INSERT INTO people.person (first_name, last_name, email, person_type)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            first,
            last,
            email,
            person_type,
        )
        assert row is not None
        return int(row[0])

    def find_student(self, person_id: int) -> int | None:
        row = self.one(
            "SELECT id FROM people.student WHERE person_id = %s AND deleted_at IS NULL", person_id
        )
        return int(row[0]) if row else None

    def find_form(self, cycle: Cycle, track: str) -> int | None:
        row = self.one(
            "SELECT id FROM people.application_form WHERE cycle_term = %s AND cycle_year = %s"
            " AND track = %s AND deleted_at IS NULL",
            cycle.term,
            cycle.year,
            track,
        )
        return int(row[0]) if row else None


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def import_applications(
    conn: Any,
    submissions: Sequence[Submission],
    *,
    cycle: Cycle,
    track: str,
    decisions: dict[str, Decision],
    actor: str,
    filename: str | None,
    file_sha256: str | None,
    form_url: str,
    dry_run: bool = False,
) -> ImportReport:
    """Upsert every submission of one cycle+track. Caller commits unless dry_run."""
    if track not in ("analyst", "relations"):
        raise ValueError(f"track must be 'analyst' or 'relations', got {track!r}")
    db = _Db(conn)
    report = ImportReport(dry_run=dry_run)
    try:
        batch_id = db.open_batch(
            source="excel_upload",
            filename=filename,
            sha=file_sha256,
            actor=actor,
            note=f"applications {cycle} {track}",
        )
        report.batch_id = batch_id
        teams = db.team_ids()
        form_id = _ensure_form(db, cycle, track, form_url, report)
        for s in submissions:
            _import_submission(
                db, s, cycle=cycle, form_id=form_id, teams=teams, decisions=decisions, report=report
            )
        db.close_batch(batch_id, "applied", len(submissions))
    except Exception:
        conn.rollback()
        raise
    if dry_run:
        conn.rollback()
    return report


def _ensure_form(db: _Db, cycle: Cycle, track: str, form_url: str, report: ImportReport) -> int:
    form_id = db.find_form(cycle, track)
    if form_id is not None:
        report.counts["application_form.existing"] += 1
        return form_id
    row = db.one(
        "INSERT INTO people.application_form (cycle_term, cycle_year, track, form_url, question_map)"
        " VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id",
        cycle.term,
        cycle.year,
        track,
        form_url,
        json.dumps(QUESTION_MAP),
    )
    assert row is not None
    report.counts["application_form.created"] += 1
    return int(row[0])


def _ensure_person(db: _Db, first: str, last: str, email: str, report: ImportReport) -> int:
    found = db.find_person(email)
    if found:
        report.counts["person.existing"] += 1
        return found[0]
    report.counts["person.created"] += 1
    return db.insert_person(first, last, email, None)


def _import_submission(
    db: _Db,
    s: Submission,
    *,
    cycle: Cycle,
    form_id: int,
    teams: dict[str, int],
    decisions: dict[str, Decision],
    report: ImportReport,
) -> None:
    label = f"{s.first_name} {s.last_name} <{s.email}> (Id {s.response_id})"
    person_id = _ensure_person(db, s.first_name, s.last_name, s.email, report)
    student_id = _upsert_student(db, person_id, s, report, label)
    if student_id is not None:
        _ensure_majors(db, student_id, s, report)
    resume_id = _ensure_attachment(db, person_id, "resume", s.resume_url, report)
    _ensure_attachment(db, person_id, "trading_strategy", s.strategy_url, report)

    decision = decisions.get(s.email)
    stage, outcome = _stage_outcome(decision)

    existing = db.one(
        "SELECT id, stage, outcome, deleted_at IS NOT NULL FROM people.application"
        " WHERE form_id = %s AND external_response_id = %s",
        form_id,
        s.response_id,
    )
    raw = json.dumps(s.raw)
    # About to make this response live (insert, or restore a soft-deleted row).
    # Someone who submitted the form twice in one cycle has one live
    # application: the later submission is the one they meant, so it wins and
    # the earlier one is soft-deleted (its raw_response stays in the table and
    # in change_log). Re-importing must not flip that back.
    going_live = existing is None or bool(existing[3])
    if going_live and not _supersede_live(db, s, person_id, cycle, label, report):
        return
    if existing is None:
        row = db.one(
            "INSERT INTO people.application (person_id, cycle_term, cycle_year, form_id, external_response_id,"
            " raw_response, stage, outcome, resume_attachment_id, submitted_at)"
            " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s) RETURNING id",
            person_id,
            cycle.term,
            cycle.year,
            form_id,
            s.response_id,
            raw,
            stage,
            outcome,
            resume_id,
            s.submitted_at,
        )
        assert row is not None
        application_id = int(row[0])
        report.counts["application.created"] += 1
    else:
        application_id, old_stage, old_outcome, was_deleted = (
            int(existing[0]),
            str(existing[1]),
            str(existing[2]),
            bool(existing[3]),
        )
        # Stage only moves forward; an undecided re-import never downgrades a decision.
        if STAGE_RANK[old_stage] > STAGE_RANK[stage]:
            stage = old_stage
        if outcome == "pending" and old_outcome != "pending":
            outcome = old_outcome
        db.run(
            "UPDATE people.application SET raw_response = %s::jsonb, stage = %s, outcome = %s,"
            " resume_attachment_id = %s, submitted_at = %s, deleted_at = NULL WHERE id = %s",
            raw,
            stage,
            outcome,
            resume_id,
            s.submitted_at,
            application_id,
        )
        report.counts["application.restored" if was_deleted else "application.updated"] += 1
        if (stage, outcome) != (old_stage, old_outcome):
            report.note(f"{label}: {old_stage}/{old_outcome} -> {stage}/{outcome}")

    if decision and (stage, outcome) != ("submitted", "pending"):
        report.counts[f"application.outcome.{outcome}"] += 1

    # Preferences live and die with the application: replace wholesale.
    db.run(
        "DELETE FROM people.application_team_preference WHERE application_id = %s", application_id
    )
    for rank, slug in enumerate(s.team_preferences, start=1):
        db.run(
            "INSERT INTO people.application_team_preference (application_id, team_id, rank) VALUES (%s, %s, %s)",
            application_id,
            teams[slug],
            rank,
        )
    if s.team_preferences:
        report.counts["team_preference.rows"] += len(s.team_preferences)


def _supersede_live(
    db: _Db, s: Submission, person_id: int, cycle: Cycle, label: str, report: ImportReport
) -> bool:
    """Make room for `s` as the person's live application this cycle.

    Returns False when a later submission is already live (so `s` stays out).
    Soft-deletes an earlier live submission otherwise.
    """
    live = db.one(
        "SELECT id, external_response_id, submitted_at FROM people.application"
        " WHERE person_id = %s AND cycle_term = %s AND cycle_year = %s AND deleted_at IS NULL"
        " AND external_response_id IS DISTINCT FROM %s",
        person_id,
        cycle.term,
        cycle.year,
        s.response_id,
    )
    if live is None:
        return True
    if live[2] >= s.submitted_at:
        report.counts["application.skipped_superseded"] += 1
        report.warn(f"{label}: a later {cycle} submission (Id {live[1]}) is live; this one is not")
        return False
    db.run("UPDATE people.application SET deleted_at = now() WHERE id = %s", live[0])
    report.counts["application.superseded"] += 1
    report.warn(
        f"{label}: supersedes their earlier {cycle} submission (Id {live[1]}), now soft-deleted"
    )
    return True


def _stage_outcome(decision: Decision | None) -> tuple[str, str]:
    if decision is None:
        return "submitted", "pending"
    if decision.fund == "Y":
        return "final", "accepted"
    if decision.fund == "N":
        return "final", "rejected"
    return ("interview", "pending") if decision.interview else ("submitted", "pending")


def _upsert_student(
    db: _Db, person_id: int, s: Submission, report: ImportReport, label: str
) -> int | None:
    if s.grad_term is None or s.grad_year is None:
        report.warn(f"{label}: no graduation term/year on the form; student row skipped")
        return db.find_student(person_id)
    student_id = db.find_student(person_id)
    if student_id is None:
        row = db.one(
            "INSERT INTO people.student (person_id, grad_term, grad_year, class_standing)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            person_id,
            s.grad_term,
            s.grad_year,
            s.class_standing,
        )
        assert row is not None
        report.counts["student.created"] += 1
        return int(row[0])
    changed = db.run(
        "UPDATE people.student SET grad_term = %s, grad_year = %s, class_standing = COALESCE(%s, class_standing)"
        " WHERE id = %s AND (grad_term, grad_year) IS DISTINCT FROM (%s, %s::smallint)",
        s.grad_term,
        s.grad_year,
        s.class_standing,
        student_id,
        s.grad_term,
        s.grad_year,
    )
    report.counts["student.updated" if changed else "student.existing"] += 1
    return student_id


def _ensure_majors(db: _Db, student_id: int, s: Submission, report: ImportReport) -> None:
    for kind, fields in (("major", s.majors), ("minor", s.minors)):
        for f in fields:
            exists = db.one(
                "SELECT 1 FROM people.student_major WHERE student_id = %s AND lower(field) = lower(%s)"
                " AND kind = %s AND deleted_at IS NULL",
                student_id,
                f,
                kind,
            )
            if exists:
                continue
            db.run(
                "INSERT INTO people.student_major (student_id, field, kind) VALUES (%s, %s, %s)",
                student_id,
                f,
                kind,
            )
            report.counts[f"student_major.{kind}.created"] += 1


def _ensure_attachment(
    db: _Db, person_id: int, kind: str, url: str | None, report: ImportReport
) -> int | None:
    if url is None:
        return None
    row = db.one(
        "SELECT id FROM people.attachment WHERE person_id = %s AND kind = %s AND source_url = %s AND deleted_at IS NULL",
        person_id,
        kind,
        url,
    )
    if row:
        return int(row[0])
    row = db.one(
        "INSERT INTO people.attachment (person_id, kind, source_url, original_filename)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        person_id,
        kind,
        url,
        filename_from_url(url),
    )
    assert row is not None
    report.counts[f"attachment.{kind}.created"] += 1
    return int(row[0])


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


def import_members(
    conn: Any,
    members: Iterable[MemberRow],
    *,
    actor: str,
    filename: str | None,
    file_sha256: str | None,
    joined_on: date | None = None,
    dry_run: bool = False,
) -> ImportReport:
    """Upsert the roster. Matches people by email; accepts their pending application.

    ``joined_on`` is used only for member rows created by this run (the roster
    carries no join date); existing members keep theirs.
    """
    db = _Db(conn)
    report = ImportReport(dry_run=dry_run)
    rows = list(members)
    try:
        batch_id = db.open_batch(
            source="excel_upload",
            filename=filename,
            sha=file_sha256,
            actor=actor,
            note="members roster",
        )
        report.batch_id = batch_id
        teams = db.team_ids()
        for m in rows:
            _import_member(db, m, teams=teams, joined_on=joined_on, report=report)
        db.close_batch(batch_id, "applied", len(rows))
    except Exception:
        conn.rollback()
        raise
    if dry_run:
        conn.rollback()
    return report


def _import_member(
    db: _Db, m: MemberRow, *, teams: dict[str, int], joined_on: date | None, report: ImportReport
) -> None:
    label = f"{m.first_name} {m.last_name} <{m.email}>"
    found = db.find_person(m.email)
    if found is None:
        person_id = db.insert_person(m.first_name, m.last_name, m.email, "member")
        report.counts["person.created"] += 1
        report.note(f"{label}: not in any imported application; person created from the roster")
    else:
        person_id, old_first, old_last = found
        report.counts["person.existing"] += 1
        if (old_first, old_last) != (m.first_name, m.last_name):
            db.run(
                "UPDATE people.person SET first_name = %s, last_name = %s, person_type = 'member' WHERE id = %s",
                m.first_name,
                m.last_name,
                person_id,
            )
            report.note(
                f"{label}: name on roster differs from form ({old_first} {old_last}); roster wins"
            )
        else:
            db.run(
                "UPDATE people.person SET person_type = 'member' WHERE id = %s AND person_type IS DISTINCT FROM 'member'",
                person_id,
            )

    student_id = db.find_student(person_id)
    existing = db.one(
        "SELECT id, is_leadership, student_id FROM people.member WHERE person_id = %s AND deleted_at IS NULL",
        person_id,
    )
    if existing is None:
        row = db.one(
            "INSERT INTO people.member (person_id, student_id, is_leadership, joined_on)"
            " VALUES (%s, %s, %s, COALESCE(%s, CURRENT_DATE)) RETURNING id",
            person_id,
            student_id,
            m.is_leadership,
            joined_on,
        )
        assert row is not None
        member_id = int(row[0])
        report.counts["member.created"] += 1
    else:
        member_id = int(existing[0])
        if bool(existing[1]) != m.is_leadership or (existing[2] is None and student_id is not None):
            db.run(
                "UPDATE people.member SET is_leadership = %s, student_id = COALESCE(student_id, %s) WHERE id = %s",
                m.is_leadership,
                student_id,
                member_id,
            )
            report.counts["member.updated"] += 1
        else:
            report.counts["member.existing"] += 1

    _place_on_team(db, member_id, m.team, teams, report, label)

    # The application this member came in on: their most recent one still pending.
    pending = db.one(
        "SELECT id, cycle_term, cycle_year FROM people.application WHERE person_id = %s AND outcome = 'pending'"
        " AND deleted_at IS NULL ORDER BY submitted_at DESC LIMIT 1",
        person_id,
    )
    if pending:
        db.run(
            "UPDATE people.application SET outcome = 'accepted', stage = 'final' WHERE id = %s",
            pending[0],
        )
        report.counts["application.accepted"] += 1
        report.note(f"{label}: {pending[1]} {pending[2]} application marked accepted")


def _place_on_team(
    db: _Db,
    member_id: int,
    team: str | None,
    teams: dict[str, int],
    report: ImportReport,
    label: str,
) -> None:
    current = db.one(
        "SELECT mt.id, t.slug FROM people.member_team mt JOIN people.team t ON t.id = mt.team_id"
        " WHERE mt.member_id = %s AND mt.ended_on IS NULL AND mt.deleted_at IS NULL",
        member_id,
    )
    if team is None:
        if current:
            report.warn(
                f"{label}: roster gives no team but member is currently on {current[1]}; left as is"
            )
        return
    if current and current[1] == team:
        return
    if current:
        db.run("UPDATE people.member_team SET ended_on = CURRENT_DATE WHERE id = %s", current[0])
        report.note(f"{label}: moved {current[1]} -> {team}")
    db.run(
        "INSERT INTO people.member_team (member_id, team_id) VALUES (%s, %s)",
        member_id,
        teams[team],
    )
    report.counts["member_team.created"] += 1


# ---------------------------------------------------------------------------
# Closing a cycle
# ---------------------------------------------------------------------------


def close_cycle(
    conn: Any,
    *,
    cycle: Cycle,
    actor: str,
    dry_run: bool = False,
) -> ImportReport:
    """Reject every application of the cycle still pending. Run after the roster.

    Once accepted applicants are on the roster, "pending" only means "was not
    accepted", so the decision is recorded as such and the stage set to final.
    """
    db = _Db(conn)
    report = ImportReport(dry_run=dry_run)
    try:
        batch_id = db.open_batch(
            source="app_edit", filename=None, sha=None, actor=actor, note=f"close cycle {cycle}"
        )
        report.batch_id = batch_id
        rows = db.all(
            "SELECT a.id, p.first_name, p.last_name, p.email FROM people.application a"
            " JOIN people.person p ON p.id = a.person_id"
            " WHERE a.cycle_term = %s AND a.cycle_year = %s AND a.outcome = 'pending'"
            " AND a.deleted_at IS NULL ORDER BY a.id",
            cycle.term,
            cycle.year,
        )
        for app_id, first, last, email in rows:
            db.run(
                "UPDATE people.application SET outcome = 'rejected', stage = 'final' WHERE id = %s",
                app_id,
            )
            report.counts["application.rejected"] += 1
            report.note(f"{first} {last} <{email}>: {cycle} application marked rejected")
        db.close_batch(batch_id, "applied", len(rows))
    except Exception:
        conn.rollback()
        raise
    if dry_run:
        conn.rollback()
    return report
