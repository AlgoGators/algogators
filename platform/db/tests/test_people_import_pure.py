"""Database-free tests for the importer's decision logic, report, and CLI parser."""

from __future__ import annotations

from pathlib import Path

import pytest
from platform_db.people_import.__main__ import _sha256, build_parser
from platform_db.people_import.importer import ImportReport, _stage_outcome
from platform_db.people_import.model import Cycle, Decision


class TestCycle:
    @pytest.mark.parametrize("text", ["fall-2026", "Fall 2026", "FALL_2026", " spring2027 "])
    def test_parse_accepts_common_spellings(self, text: str) -> None:
        c = Cycle.parse(text)
        assert c.term in ("fall", "spring")
        assert c.year in (2026, 2027)

    @pytest.mark.parametrize("text", ["summer-2026", "2026", "fall", "fall-26"])
    def test_parse_rejects(self, text: str) -> None:
        with pytest.raises(ValueError, match="cycle"):
            Cycle.parse(text)

    def test_only_recruiting_terms(self) -> None:
        with pytest.raises(ValueError, match="fall"):
            Cycle("summer", 2026)
        with pytest.raises(ValueError, match="range"):
            Cycle("fall", 1800)

    def test_str(self) -> None:
        assert str(Cycle("fall", 2026)) == "fall 2026"


class TestStageOutcome:
    def test_no_decision(self) -> None:
        assert _stage_outcome(None) == ("submitted", "pending")

    def test_fund_yes_is_final_accepted(self) -> None:
        assert _stage_outcome(Decision(interview=True, fund="Y")) == ("final", "accepted")

    def test_fund_no_is_final_rejected(self) -> None:
        assert _stage_outcome(Decision(interview=False, fund="N")) == ("final", "rejected")

    def test_interview_only(self) -> None:
        assert _stage_outcome(Decision(interview=True, fund=None)) == ("interview", "pending")

    def test_nothing_decided(self) -> None:
        assert _stage_outcome(Decision(interview=False, fund=None)) == ("submitted", "pending")


class TestImportReport:
    def test_summary_lists_counts_notes_and_warnings(self) -> None:
        r = ImportReport(batch_id=7)
        r.counts["person.created"] += 2
        r.note("a note")
        r.warn("a warning")
        text = r.summary()
        assert "import_batch 7" in text
        assert "person.created" in text and " 2" in text
        assert "- a note" in text
        assert "! a warning" in text

    def test_dry_run_header(self) -> None:
        assert ImportReport(dry_run=True).summary().startswith("DRY RUN")


class TestCli:
    def test_applications_arguments(self) -> None:
        args = build_parser().parse_args(
            [
                "applications",
                "--workbook",
                "x.xlsx",
                "--track",
                "analyst",
                "--cycle",
                "fall-2026",
                "--dry-run",
            ]
        )
        assert args.command == "applications"
        assert args.workbook == Path("x.xlsx")
        assert args.track == "analyst"
        assert args.dry_run is True
        assert args.decisions_sheet == "Composite Score"
        assert args.timezone == "America/New_York"

    def test_members_arguments(self) -> None:
        args = build_parser().parse_args(["members", "--csv", "m.csv", "--joined-on", "2026-09-01"])
        assert args.command == "members"
        assert args.joined_on.isoformat() == "2026-09-01"
        assert args.dry_run is False

    def test_close_cycle_arguments(self) -> None:
        args = build_parser().parse_args(["close-cycle", "--cycle", "fall-2026", "--dry-run"])
        assert args.command == "close-cycle"
        assert args.cycle == "fall-2026"
        assert args.dry_run is True

    def test_track_is_validated(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                ["applications", "--workbook", "x", "--track", "ops", "--cycle", "fall-2026"]
            )

    def test_sha256(self, tmp_path: Path) -> None:
        f = tmp_path / "f.bin"
        f.write_bytes(b"abc")
        assert _sha256(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
