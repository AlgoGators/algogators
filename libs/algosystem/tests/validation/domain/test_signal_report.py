"""Tests for the SignalAnalysisReport domain result object."""

from __future__ import annotations

import dataclasses

import pytest
from algosystem.validation.domain.statistics.signal_analyzer import SignalAnalysisReport

SEPARATOR = "=" * 72


class _LinesStub:
    """Sub-result stub whose summary() returns fixed lines."""

    def __init__(self, lines):
        self._lines = list(lines)

    def summary(self):
        return list(self._lines)


class _OverfitStub(_LinesStub):
    def surface_summary(self):
        return ["SURFACE-SUM"]


class _PBOStub:
    """PBO summaries are a single newline-joined string, not a list."""

    def summary(self):
        return "PBO-1\nPBO-2"


def _minimal_report(**overrides):
    kwargs = {
        "strategy_name": "alpha",
        "n_signals": 2,
        "signal_names": ["fast", "slow"],
        "total_combinations": 1234,
    }
    kwargs.update(overrides)
    return SignalAnalysisReport(**kwargs)


def _full_report():
    return _minimal_report(
        overfit_results=_OverfitStub(["OVERFIT-SUM"]),
        psr_result=_LinesStub(["PSR-SUM"]),
        dsr_result=_LinesStub(["DSR-SUM"]),
        pbo_result=_PBOStub(),
        wf_result=_LinesStub(["WF-SUM"]),
        trial_tracker=_LinesStub(["TRACKER-SUM"]),
        figures=["fig-1", None, "fig-2"],
        verdict="GENUINE SIGNAL - proceed with caution",
        confidence=0.875,
    )


class TestSummary:
    def test_minimal_structure(self):
        lines = _minimal_report().summary()
        assert lines == [
            SEPARATOR,
            "SIGNAL ANALYSIS: alpha",
            "  Signals: 2 (fast, slow)",
            "  Combinations: 1,234",
            SEPARATOR,
            "",
            SEPARATOR,
            "VERDICT: ",
            "CONFIDENCE: 0.0%",
            SEPARATOR,
        ]

    def test_thousands_separator_in_combinations(self):
        lines = _minimal_report(total_combinations=1000000).summary()
        assert "  Combinations: 1,000,000" in lines

    def test_confidence_percent_formatting(self):
        lines = _minimal_report(confidence=0.125, verdict="MARGINAL").summary()
        assert "CONFIDENCE: 12.5%" in lines
        assert "VERDICT: MARGINAL" in lines

    def test_all_sections_present_and_ordered(self):
        lines = _full_report().summary()
        markers = [
            "OVERFIT-SUM",
            "SURFACE-SUM",
            "PSR-SUM",
            "DSR-SUM",
            "PBO-1",
            "PBO-2",
            "WF-SUM",
            "TRACKER-SUM",
        ]
        positions = [lines.index(marker) for marker in markers]
        assert positions == sorted(positions)
        assert lines.index("VERDICT: GENUINE SIGNAL - proceed with caution") > positions[-1]

    def test_pbo_string_summary_split_into_lines(self):
        lines = _full_report().summary()
        assert "PBO-1" in lines
        assert "PBO-2" in lines
        assert "PBO-1\nPBO-2" not in lines

    def test_blank_line_precedes_each_section(self):
        lines = _full_report().summary()
        for marker in ["OVERFIT-SUM", "PSR-SUM", "DSR-SUM", "PBO-1", "WF-SUM", "TRACKER-SUM"]:
            assert lines[lines.index(marker) - 1] == ""


class TestArtifactSummary:
    def test_counts_only_non_none_figures(self):
        artifact = _full_report().artifact_summary()
        assert artifact["figure_count"] == 2

    def test_report_lines_match_summary(self):
        report = _full_report()
        assert report.artifact_summary()["report_lines"] == report.summary()

    def test_empty_report_has_zero_figures(self):
        artifact = _minimal_report().artifact_summary()
        assert artifact["figure_count"] == 0


class TestDataclassBehavior:
    def test_frozen(self):
        report = _minimal_report()
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.strategy_name = "beta"

    def test_default_figures_not_shared_between_instances(self):
        first = _minimal_report()
        second = _minimal_report()
        assert first.figures == []
        assert first.figures is not second.figures
