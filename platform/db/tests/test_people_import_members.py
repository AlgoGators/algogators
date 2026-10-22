"""Tests for reading the member roster CSV."""

from __future__ import annotations

from pathlib import Path

import pytest
from platform_db.people_import.members import read_members

HEADER = "first_name,last_name,email,team,is_leadership\n"


def test_reads_rows(tmp_path: Path) -> None:
    csv = tmp_path / "m.csv"
    csv.write_text(
        HEADER
        + "Dominick,Dupuy, Dominick.Dupuy@ufl.edu ,,true\nGannon,Stoner,g@ufl.edu,quant_research,false\n"
    )
    a, b = read_members(csv)
    assert (a.first_name, a.last_name, a.email) == ("Dominick", "Dupuy", "dominick.dupuy@ufl.edu")
    assert a.team is None
    assert a.is_leadership is True
    assert b.team == "quant_research"
    assert b.is_leadership is False


def test_missing_leadership_column_defaults_false(tmp_path: Path) -> None:
    csv = tmp_path / "m.csv"
    csv.write_text("first_name,last_name,email,team\nA,B,a@ufl.edu,quant_dev\n")
    [m] = read_members(csv)
    assert m.is_leadership is False


def test_unknown_team_is_an_error_naming_the_row(tmp_path: Path) -> None:
    csv = tmp_path / "m.csv"
    csv.write_text(HEADER + "A,B,a@ufl.edu,quant_ops,false\n")
    with pytest.raises(ValueError, match="quant_ops"):
        read_members(csv)


def test_duplicate_email_is_an_error(tmp_path: Path) -> None:
    csv = tmp_path / "m.csv"
    csv.write_text(HEADER + "A,B,a@ufl.edu,,false\nC,D,A@UFL.EDU,,false\n")
    with pytest.raises(ValueError, match=r"a@ufl.edu"):
        read_members(csv)


def test_blank_name_is_an_error(tmp_path: Path) -> None:
    csv = tmp_path / "m.csv"
    csv.write_text(HEADER + ",B,a@ufl.edu,,false\n")
    with pytest.raises(ValueError, match=r"a@ufl.edu"):
        read_members(csv)
