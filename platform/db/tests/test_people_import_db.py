"""Integration tests for the importer against a real Postgres with the people schema.

Skipped unless PEOPLE_TEST_DSN points at a database that already has
migrations/001_people_schema.sql applied, e.g.

    docker run -d --name people-dev -p 55432:5432 -e POSTGRES_PASSWORD=pw \
        -e POSTGRES_DB=new_algo_data postgres:16-alpine
    psql ... -f platform/db/migrations/001_people_schema.sql
    PEOPLE_TEST_DSN=postgresql://postgres:pw@localhost:55432/new_algo_data uv run pytest ...
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from platform_db.people_import.importer import (
    close_cycle,
    import_applications,
    import_members,
)
from platform_db.people_import.model import Cycle, Decision, MemberRow, Submission

DSN = os.environ.get("PEOPLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PEOPLE_TEST_DSN not set")

TABLES = (
    "change_log",
    "import_batch",
    "application_team_preference",
    "application",
    "application_form",
    "member_team",
    "member",
    "attachment",
    "student_major",
    "student",
    "investor",
    "person",
)


@pytest.fixture
def conn() -> Iterator[Any]:
    import psycopg2

    c = psycopg2.connect(DSN)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(f"people.{t}" for t in TABLES) + " RESTART IDENTITY")
    c.autocommit = False
    yield c
    c.rollback()
    c.close()


def q(conn: Any, sql: str, *args: object) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())


def sub(**over: object) -> Submission:
    base: dict[str, Any] = {
        "response_id": "1",
        "first_name": "Minh",
        "last_name": "Nguyen",
        "email": "nguyen.ba@ufl.edu",
        "grad_term": "spring",
        "grad_year": 2029,
        "class_standing": None,
        "majors": ("Computer Science", "Math"),
        "minors": ("Mathematics",),
        "resume_url": "https://x/Resume_Minh.pdf",
        "strategy_url": "https://x/Pitch_Minh.pdf",
        "team_preferences": ("quant_dev", "quant_trading", "quant_research"),
        "submitted_at": datetime(2026, 8, 18, 4, 22, 51, tzinfo=UTC),
        "raw": {"Id": 1, "Date of Birth": "2006-12-27T00:00:00"},
    }
    base.update(over)
    return Submission(**base)


FALL26 = Cycle("fall", 2026)
COMMON: dict[str, Any] = {"actor": "tester", "filename": "a.xlsx", "file_sha256": "a" * 64}


class TestImportApplications:
    def test_creates_the_full_graph(self, conn: Any) -> None:
        report = import_applications(
            conn, [sub()], cycle=FALL26, track="analyst", decisions={}, form_url="f", **COMMON
        )
        conn.commit()
        assert report.counts["person.created"] == 1
        assert report.counts["application.created"] == 1
        [(email, first, last)] = q(conn, "SELECT email, first_name, last_name FROM people.person")
        assert (email, first, last) == ("nguyen.ba@ufl.edu", "Minh", "Nguyen")
        [(term, year)] = q(conn, "SELECT grad_term, grad_year FROM people.student")
        assert (term, year) == ("spring", 2029)
        majors = q(conn, "SELECT field, kind FROM people.student_major ORDER BY id")
        assert majors == [
            ("Computer Science", "major"),
            ("Math", "major"),
            ("Mathematics", "minor"),
        ]
        atts = q(
            conn, "SELECT kind, source_url, original_filename FROM people.attachment ORDER BY id"
        )
        assert atts == [
            ("resume", "https://x/Resume_Minh.pdf", "Resume_Minh.pdf"),
            ("trading_strategy", "https://x/Pitch_Minh.pdf", "Pitch_Minh.pdf"),
        ]
        [(term, year, ext, stage, outcome, resume_id, raw)] = q(
            conn,
            "SELECT cycle_term, cycle_year, external_response_id, stage, outcome,"
            " resume_attachment_id, raw_response FROM people.application",
        )
        assert (term, year, ext, stage, outcome) == ("fall", 2026, "1", "submitted", "pending")
        assert resume_id == 1
        assert raw["Date of Birth"] == "2006-12-27T00:00:00"
        prefs = q(
            conn,
            "SELECT t.slug, p.rank FROM people.application_team_preference p"
            " JOIN people.team t ON t.id = p.team_id ORDER BY p.rank",
        )
        assert prefs == [("quant_dev", 1), ("quant_trading", 2), ("quant_research", 3)]
        [(track, qmap)] = q(conn, "SELECT track, question_map FROM people.application_form")
        assert track == "analyst"
        assert "UF Email" in qmap
        [(status, rows, batch_id)] = q(
            conn, "SELECT status, row_count, id FROM people.import_batch"
        )
        assert (status, rows) == ("applied", 1)
        # Every logged write in this run carries the batch and the actor.
        assert q(conn, "SELECT DISTINCT batch_id, actor FROM people.change_log") == [
            (batch_id, "tester")
        ]

    def test_reimport_is_idempotent(self, conn: Any) -> None:
        import_applications(
            conn, [sub()], cycle=FALL26, track="analyst", decisions={}, form_url="f", **COMMON
        )
        conn.commit()
        report = import_applications(
            conn, [sub()], cycle=FALL26, track="analyst", decisions={}, form_url="f", **COMMON
        )
        conn.commit()
        assert report.counts.get("person.created", 0) == 0
        assert report.counts.get("application.created", 0) == 0
        assert q(conn, "SELECT count(*) FROM people.person") == [(1,)]
        assert q(conn, "SELECT count(*) FROM people.application") == [(1,)]
        assert q(conn, "SELECT count(*) FROM people.student_major") == [(3,)]
        assert q(conn, "SELECT count(*) FROM people.attachment") == [(2,)]
        assert q(conn, "SELECT count(*) FROM people.application_team_preference") == [(3,)]
        assert q(conn, "SELECT count(*) FROM people.application_form") == [(1,)]

    def test_decisions_set_stage_and_outcome(self, conn: Any) -> None:
        decisions = {
            "nguyen.ba@ufl.edu": Decision(interview=True, fund="Y"),
            "b@ufl.edu": Decision(interview=True, fund="N"),
            "c@ufl.edu": Decision(interview=True, fund=None),
        }
        subs = [
            sub(),
            sub(response_id="2", email="b@ufl.edu", first_name="B"),
            sub(response_id="3", email="c@ufl.edu", first_name="C"),
            sub(response_id="4", email="d@ufl.edu", first_name="D"),
        ]
        import_applications(
            conn, subs, cycle=FALL26, track="analyst", decisions=decisions, form_url="f", **COMMON
        )
        conn.commit()
        rows = q(
            conn,
            "SELECT external_response_id, stage, outcome FROM people.application ORDER BY id",
        )
        assert rows == [
            ("1", "final", "accepted"),
            ("2", "final", "rejected"),
            ("3", "interview", "pending"),
            ("4", "submitted", "pending"),
        ]

    def test_stage_never_moves_backwards(self, conn: Any) -> None:
        import_applications(
            conn,
            [sub()],
            cycle=FALL26,
            track="analyst",
            decisions={"nguyen.ba@ufl.edu": Decision(interview=True, fund=None)},
            form_url="f",
            **COMMON,
        )
        conn.commit()
        import_applications(
            conn, [sub()], cycle=FALL26, track="analyst", decisions={}, form_url="f", **COMMON
        )
        conn.commit()
        assert q(conn, "SELECT stage FROM people.application") == [("interview",)]

    def test_second_cycle_is_a_second_application(self, conn: Any) -> None:
        import_applications(
            conn,
            [sub()],
            cycle=Cycle("spring", 2026),
            track="analyst",
            decisions={},
            form_url="f",
            **COMMON,
        )
        conn.commit()
        import_applications(
            conn,
            [sub(response_id="7")],
            cycle=FALL26,
            track="analyst",
            decisions={},
            form_url="g",
            **COMMON,
        )
        conn.commit()
        assert q(conn, "SELECT count(*) FROM people.person") == [(1,)]
        assert q(conn, "SELECT times_applied, cycles FROM people.applicant_history") == [
            (2, ["spring 2026", "fall 2026"])
        ]

    def test_duplicate_submission_latest_wins(self, conn: Any) -> None:
        early = sub(response_id="3", submitted_at=datetime(2026, 8, 20, tzinfo=UTC))
        late = sub(response_id="11", submitted_at=datetime(2026, 9, 4, tzinfo=UTC))
        report = import_applications(
            conn,
            [early, late],
            cycle=FALL26,
            track="analyst",
            decisions={},
            form_url="f",
            **COMMON,
        )
        conn.commit()
        assert report.counts["application.superseded"] == 1
        rows = q(
            conn,
            "SELECT external_response_id, deleted_at IS NULL FROM people.application ORDER BY id",
        )
        assert rows == [("3", False), ("11", True)]
        assert q(conn, "SELECT times_applied FROM people.applicant_history") == [(1,)]
        # Re-importing in the export's order does not flip it back.
        import_applications(
            conn,
            [early, late],
            cycle=FALL26,
            track="analyst",
            decisions={},
            form_url="f",
            **COMMON,
        )
        conn.commit()
        assert q(conn, "SELECT count(*) FROM people.application WHERE deleted_at IS NULL") == [(1,)]

    def test_missing_graduation_skips_student_with_warning(self, conn: Any) -> None:
        report = import_applications(
            conn,
            [sub(grad_term=None, grad_year=None)],
            cycle=FALL26,
            track="analyst",
            decisions={},
            form_url="f",
            **COMMON,
        )
        conn.commit()
        assert q(conn, "SELECT count(*) FROM people.student") == [(0,)]
        assert any("graduation" in w for w in report.warnings)

    def test_dry_run_writes_nothing(self, conn: Any) -> None:
        report = import_applications(
            conn,
            [sub()],
            cycle=FALL26,
            track="analyst",
            decisions={},
            form_url="f",
            dry_run=True,
            **COMMON,
        )
        assert report.counts["person.created"] == 1
        assert q(conn, "SELECT count(*) FROM people.person") == [(0,)]
        assert q(conn, "SELECT count(*) FROM people.import_batch") == [(0,)]


def member(**over: object) -> MemberRow:
    base: dict[str, Any] = {
        "first_name": "Minh",
        "last_name": "Nguyen",
        "email": "nguyen.ba@ufl.edu",
        "team": "quant_dev",
        "is_leadership": False,
    }
    base.update(over)
    return MemberRow(**base)


class TestImportMembers:
    def test_matches_applicant_and_accepts_their_application(self, conn: Any) -> None:
        import_applications(
            conn, [sub()], cycle=FALL26, track="analyst", decisions={}, form_url="f", **COMMON
        )
        conn.commit()
        report = import_members(
            conn, [member(email="NGUYEN.BA@ufl.edu")], joined_on=date(2026, 9, 1), **COMMON
        )
        conn.commit()
        assert report.counts["member.created"] == 1
        assert report.counts["application.accepted"] == 1
        assert q(conn, "SELECT count(*) FROM people.person") == [(1,)]
        assert q(conn, "SELECT person_type FROM people.person") == [("member",)]
        [(stu_id, lead, joined)] = q(
            conn, "SELECT student_id, is_leadership, joined_on FROM people.member"
        )
        assert stu_id == 1
        assert lead is False
        assert joined == date(2026, 9, 1)
        assert q(conn, "SELECT stage, outcome FROM people.application") == [("final", "accepted")]
        assert q(conn, "SELECT team, active FROM people.roster_public") == [
            ("Quantitative Development", True)
        ]

    def test_member_without_application_is_created_and_reported(self, conn: Any) -> None:
        report = import_members(
            conn, [member(email="new@ufl.edu", team=None, is_leadership=True)], **COMMON
        )
        conn.commit()
        assert report.counts["person.created"] == 1
        assert any("new@ufl.edu" in n for n in report.notes)
        assert q(conn, "SELECT is_leadership, student_id FROM people.member") == [(True, None)]
        assert q(conn, "SELECT count(*) FROM people.member_team") == [(0,)]

    def test_rerun_is_idempotent_and_team_change_keeps_history(self, conn: Any) -> None:
        import_members(conn, [member()], **COMMON)
        conn.commit()
        import_members(conn, [member()], **COMMON)
        conn.commit()
        assert q(conn, "SELECT count(*) FROM people.member") == [(1,)]
        assert q(conn, "SELECT count(*) FROM people.member_team") == [(1,)]
        import_members(conn, [member(team="quant_research")], **COMMON)
        conn.commit()
        rows = q(
            conn,
            "SELECT t.slug, mt.ended_on IS NULL FROM people.member_team mt"
            " JOIN people.team t ON t.id = mt.team_id ORDER BY mt.id",
        )
        assert rows == [("quant_dev", False), ("quant_research", True)]

    def test_name_from_roster_wins_and_is_reported(self, conn: Any) -> None:
        import_applications(
            conn,
            [sub(first_name="Zhengmao")],
            cycle=FALL26,
            track="analyst",
            decisions={},
            form_url="f",
            **COMMON,
        )
        conn.commit()
        report = import_members(conn, [member(first_name="Zhengmao (Bill)")], **COMMON)
        conn.commit()
        assert q(conn, "SELECT first_name FROM people.person") == [("Zhengmao (Bill)",)]
        assert any("Zhengmao" in n for n in report.notes)

    def test_dry_run_writes_nothing(self, conn: Any) -> None:
        import_members(conn, [member()], dry_run=True, **COMMON)
        assert q(conn, "SELECT count(*) FROM people.person") == [(0,)]


class TestCloseCycle:
    def test_pending_becomes_rejected_only_for_that_cycle(self, conn: Any) -> None:
        subs = [sub(), sub(response_id="2", email="b@ufl.edu", first_name="B")]
        import_applications(
            conn, subs, cycle=FALL26, track="analyst", decisions={}, form_url="f", **COMMON
        )
        import_applications(
            conn,
            [sub(response_id="9", email="c@ufl.edu", first_name="C")],
            cycle=Cycle("spring", 2026),
            track="analyst",
            decisions={},
            form_url="g",
            **COMMON,
        )
        conn.commit()
        import_members(conn, [member()], **COMMON)
        conn.commit()
        report = close_cycle(conn, cycle=FALL26, actor="tester")
        conn.commit()
        assert report.counts["application.rejected"] == 1
        rows = q(
            conn,
            "SELECT external_response_id, stage, outcome FROM people.application ORDER BY id",
        )
        assert rows == [
            ("1", "final", "accepted"),
            ("2", "final", "rejected"),
            ("9", "submitted", "pending"),
        ]

    def test_dry_run_writes_nothing(self, conn: Any) -> None:
        import_applications(
            conn, [sub()], cycle=FALL26, track="analyst", decisions={}, form_url="f", **COMMON
        )
        conn.commit()
        report = close_cycle(conn, cycle=FALL26, actor="tester", dry_run=True)
        assert report.counts["application.rejected"] == 1
        assert q(conn, "SELECT outcome FROM people.application") == [("pending",)]
