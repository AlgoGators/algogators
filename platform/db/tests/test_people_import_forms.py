"""Tests for reading a Microsoft Forms .xlsx export into Submission records."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest
from platform_db.people_import.forms import read_decisions, read_submissions
from platform_db.people_import.model import (
    filename_from_url,
    month_year_to_term_year,
    parse_team_preferences,
    split_fields,
)

ANALYST_HEADER = [
    "Id",
    "Start time",
    "Completion time",
    "Email",
    "Name",
    "First Name",
    "Last Name",
    "UF Email",
    "LinkedIn URL",
    "Phone Number (e.g 123-456-789)",
    "Graduation Year",
    "Graduation Semester",
    "Graduation Classification",
    "Expected Date of Graduation (eg. May, 2028)",
    "Date of Birth",
    "Major(s)",
    "Minor(s)",
    "Resume (PDF)",
    "How did you hear about AlgoGators?",
    "Trading Stategy",
    "Which team are you most interested in joining?\nPlease rank your selection",
    "If accepted into the fund for the current semester, I agree to: Attend",
]

DOC_URL = (
    "https://uflorida-my.sharepoint.com/personal/x_ufl_edu/_layouts/15/Doc.aspx"
    "?sourcedoc=%7BABC%7D&file=Resume_Minh%20Nguyen.docx&action=default"
)
PATH_URL = (
    "https://uflorida-my.sharepoint.com/personal/x_ufl_edu/Documents/Apps/"
    "Microsoft%20Forms/App/Resume/Zhijun%20Zhang%27s%20resume_Zhang%20Zhijun%201.pdf"
)


def write_workbook(path: Path, sheets: dict[str, Sequence[Sequence[object]]]) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(list(row))
    wb.save(path)
    return path


def analyst_row(**overrides: object) -> list[object]:
    base: dict[str, object] = {
        "Id": 1,
        "Start time": datetime(2026, 8, 18, 0, 10, 52),
        "Completion time": datetime(2026, 8, 18, 0, 22, 51),
        "Email": "login@ufl.edu",
        "Name": "Minh Nguyen",
        "First Name": "Minh",
        "Last Name": "Nguyen",
        "UF Email": "Nguyen.BA@ufl.edu ",
        "LinkedIn URL": "https://linkedin.com/in/x",
        "Phone Number (e.g 123-456-789)": "2399104521",
        "Graduation Year": "2029",
        "Graduation Semester": "Spring",
        "Graduation Classification": None,
        "Expected Date of Graduation (eg. May, 2028)": None,
        "Date of Birth": datetime(2006, 12, 27),
        "Major(s)": "Computer Science + Math",
        "Minor(s)": "Mathematics",
        "Resume (PDF)": DOC_URL,
        "How did you hear about AlgoGators?": "friend",
        "Trading Stategy": PATH_URL,
        "Which team are you most interested in joining?\nPlease rank your selection": (
            "Quant Dev;Quant Trading;Quant Research;"
        ),
        "If accepted into the fund for the current semester, I agree to: Attend": "MN",
    }
    base.update(overrides)
    return [base[h] for h in ANALYST_HEADER]


class TestHelpers:
    def test_parse_team_preferences_drops_trailing_separator(self) -> None:
        assert parse_team_preferences("Quant Dev;Quant Trading;Quant Research;") == (
            "quant_dev",
            "quant_trading",
            "quant_research",
        )

    def test_parse_team_preferences_rejects_unknown_team(self) -> None:
        with pytest.raises(ValueError, match="Quant Ops"):
            parse_team_preferences("Quant Ops;Quant Dev")

    def test_parse_team_preferences_empty(self) -> None:
        assert parse_team_preferences(None) == ()
        assert parse_team_preferences("") == ()

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Computer Science + Math", ("Computer Science", "Math")),
            ("Economics and PPEL", ("Economics", "PPEL")),
            ("Graphic Design & Statistics (BA)", ("Graphic Design", "Statistics (BA)")),
            ("Statistics, Economics", ("Statistics", "Economics")),
            ("Professional Sales/AI Certificate", ("Professional Sales/AI Certificate",)),
            (
                "Mathematics (Projected or Double Major)",
                ("Mathematics (Projected or Double Major)",),
            ),
            ("  Computer Science ", ("Computer Science",)),
            (None, ()),
            ("", ()),
        ],
    )
    def test_split_fields(self, raw: str | None, expected: tuple[str, ...]) -> None:
        assert split_fields(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("May, 2029", ("spring", 2029)),
            ("December 2027", ("fall", 2027)),
            ("Aug 2028", ("summer", 2028)),
        ],
    )
    def test_month_year_to_term_year(self, raw: str, expected: tuple[str, int]) -> None:
        assert month_year_to_term_year(raw) == expected

    def test_month_year_to_term_year_unparseable(self) -> None:
        assert month_year_to_term_year("soon") is None

    def test_filename_from_doc_url_uses_file_param(self) -> None:
        assert filename_from_url(DOC_URL) == "Resume_Minh Nguyen.docx"

    def test_filename_from_path_url_uses_last_segment(self) -> None:
        assert filename_from_url(PATH_URL) == "Zhijun Zhang's resume_Zhang Zhijun 1.pdf"


class TestReadSubmissions:
    def test_reads_one_row(self, tmp_path: Path) -> None:
        wb = write_workbook(tmp_path / "a.xlsx", {"Submissions": [ANALYST_HEADER, analyst_row()]})
        [s] = read_submissions(wb)
        assert s.response_id == "1"
        assert s.first_name == "Minh"
        assert s.last_name == "Nguyen"
        # UF Email wins over the login Email, and is trimmed + lowercased.
        assert s.email == "nguyen.ba@ufl.edu"
        assert s.grad_term == "spring"
        assert s.grad_year == 2029
        assert s.class_standing is None
        assert s.majors == ("Computer Science", "Math")
        assert s.minors == ("Mathematics",)
        assert s.resume_url == DOC_URL
        assert s.strategy_url == PATH_URL
        assert s.team_preferences == ("quant_dev", "quant_trading", "quant_research")
        # Naive Forms timestamps are read as America/New_York.
        assert s.submitted_at.isoformat() == "2026-08-18T00:22:51-04:00"

    def test_raw_response_is_json_safe_and_complete(self, tmp_path: Path) -> None:
        wb = write_workbook(tmp_path / "a.xlsx", {"Submissions": [ANALYST_HEADER, analyst_row()]})
        [s] = read_submissions(wb)
        assert s.raw["Date of Birth"] == "2006-12-27T00:00:00"
        assert s.raw["Phone Number (e.g 123-456-789)"] == "2399104521"
        assert s.raw["Id"] == 1
        assert set(s.raw) == set(ANALYST_HEADER)

    def test_defaults_to_first_sheet_when_sheet_not_named(self, tmp_path: Path) -> None:
        wb = write_workbook(
            tmp_path / "a.xlsx",
            {"Sheet1": [ANALYST_HEADER, analyst_row()], "Other": [["x"], ["y"]]},
        )
        assert len(read_submissions(wb)) == 1

    def test_named_sheet(self, tmp_path: Path) -> None:
        wb = write_workbook(
            tmp_path / "a.xlsx",
            {"Junk": [["x"], ["y"]], "Submissions": [ANALYST_HEADER, analyst_row()]},
        )
        assert len(read_submissions(wb, sheet="Submissions")) == 1

    def test_falls_back_to_login_email(self, tmp_path: Path) -> None:
        wb = write_workbook(
            tmp_path / "a.xlsx",
            {"Submissions": [ANALYST_HEADER, analyst_row(**{"UF Email": None})]},
        )
        [s] = read_submissions(wb)
        assert s.email == "login@ufl.edu"

    def test_falls_back_to_login_email_when_uf_email_is_not_an_address(
        self, tmp_path: Path
    ) -> None:
        wb = write_workbook(
            tmp_path / "a.xlsx",
            {"Submissions": [ANALYST_HEADER, analyst_row(**{"UF Email": "99124499"})]},
        )
        [s] = read_submissions(wb)
        assert s.email == "login@ufl.edu"

    def test_month_year_fallback_for_graduation(self, tmp_path: Path) -> None:
        row = analyst_row(
            **{
                "Graduation Year": None,
                "Graduation Semester": None,
                "Expected Date of Graduation (eg. May, 2028)": "May, 2029",
                "Graduation Classification": "Sophomore",
            }
        )
        wb = write_workbook(tmp_path / "a.xlsx", {"Submissions": [ANALYST_HEADER, row]})
        [s] = read_submissions(wb)
        assert (s.grad_term, s.grad_year, s.class_standing) == ("spring", 2029, "Sophomore")

    def test_missing_graduation_is_none_not_error(self, tmp_path: Path) -> None:
        row = analyst_row(**{"Graduation Year": None, "Graduation Semester": None})
        wb = write_workbook(tmp_path / "a.xlsx", {"Submissions": [ANALYST_HEADER, row]})
        [s] = read_submissions(wb)
        assert s.grad_term is None
        assert s.grad_year is None

    def test_relations_export_header_typo(self, tmp_path: Path) -> None:
        header = [
            "Id",
            "Start time",
            "Completion time",
            "Email",
            "Name",
            "First Name",
            "Last Name",
            "UF Email",
            "LinkedIn URL",
            "Phone Number\xa0(e.g 123-456-789)",
            "Gradation Year",
            "Graduation Semester",
            "Date of Birth",
            "Major(s)",
            "Minor(s)",
            "Resume (PDF)",
            "Why are you interested?",
        ]
        row: list[object] = [
            1,
            datetime(2026, 8, 18, 18, 52, 8),
            datetime(2026, 8, 18, 18, 59, 11),
            "simonmatias@ufl.edu",
            "Matias Simon",
            "Matias",
            "Simon",
            "simonmatias@ufl.edu",
            None,
            "(305)216-3346",
            "2027",
            "Fall",
            None,
            "Business Administration",
            None,
            PATH_URL,
            "because",
        ]
        wb = write_workbook(tmp_path / "r.xlsx", {"Sheet1": [header, row]})
        [s] = read_submissions(wb)
        assert (s.grad_term, s.grad_year) == ("fall", 2027)
        assert s.team_preferences == ()
        assert s.strategy_url is None
        assert s.majors == ("Business Administration",)

    def test_blank_rows_are_skipped(self, tmp_path: Path) -> None:
        wb = write_workbook(
            tmp_path / "a.xlsx",
            {"Submissions": [ANALYST_HEADER, analyst_row(), [None] * len(ANALYST_HEADER)]},
        )
        assert len(read_submissions(wb)) == 1

    def test_missing_email_is_an_error_naming_the_row(self, tmp_path: Path) -> None:
        row = analyst_row(**{"UF Email": None, "Email": None})
        wb = write_workbook(tmp_path / "a.xlsx", {"Submissions": [ANALYST_HEADER, row]})
        with pytest.raises(ValueError, match="Id 1"):
            read_submissions(wb)


class TestReadDecisions:
    def test_reads_interview_and_fund_flags(self, tmp_path: Path) -> None:
        header: list[object] = ["Name", "Email", "Interview Invite", "Fund Invite"]
        rows: list[list[object]] = [
            header,
            ["A", "A@ufl.edu", "Y", "N "],
            ["B", "b@ufl.edu", "Y", "Y"],
            ["C", "c@ufl.edu", None, None],
            [None, None, None, None],
        ]
        wb = write_workbook(tmp_path / "a.xlsx", {"Composite Score": rows})
        d = read_decisions(wb, sheet="Composite Score")
        assert d["a@ufl.edu"].interview is True
        assert d["a@ufl.edu"].fund == "N"
        assert d["b@ufl.edu"].fund == "Y"
        assert d["c@ufl.edu"].interview is False
        assert d["c@ufl.edu"].fund is None
        assert len(d) == 3

    def test_missing_sheet_returns_empty(self, tmp_path: Path) -> None:
        wb = write_workbook(tmp_path / "a.xlsx", {"Sheet1": [["x"], ["y"]]})
        assert read_decisions(wb, sheet="Composite Score") == {}
