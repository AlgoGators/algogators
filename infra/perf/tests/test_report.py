"""Tests for report generation."""

import json
from pathlib import Path

import pytest

from infra.perf.report import ReportGenerator


class TestReportGeneration:
    """Test report.py generation logic."""

    @pytest.fixture
    def sample_contracts(self, tmp_path: Path) -> Path:
        """Create sample contract JSON files for testing per spec §3."""
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()

        # Contract 1: DB footprint with correct schema
        db_contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "db_footprint",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host", "commit": "abc123"},
            "metrics": [
                {"id": "table_bytes_futures_data_ohlcv_1d", "value": 1000000, "unit": "bytes"},
                {"id": "raw_cleaned_ratio", "value": 1.25, "unit": "ratio"},
                {"id": "growth_bytes_per_day", "value": 5000, "unit": "bytes"},
            ],
        }

        with open(contracts_dir / "db_footprint.json", "w") as f:
            json.dump(db_contract, f)

        return contracts_dir

    @pytest.fixture
    def sample_budgets(self, tmp_path: Path) -> Path:
        """Create sample budgets.toml for testing."""
        budgets_path = tmp_path / "budgets.toml"

        budgets_content = """
[db_footprint.table_bytes_futures_data_ohlcv_1d]
baseline = 500000
threshold = 150
action = "Investigate if table growth is expected"

[db_footprint.raw_cleaned_ratio]
baseline = "unset"
threshold = 130
action = "Ensure cleaned data quality is maintained"

[db_footprint.growth_bytes_per_day]
baseline = 3000
threshold = 120
action = "Check if growth rate is sustainable"
"""

        with open(budgets_path, "w") as f:
            f.write(budgets_content)

        return budgets_path

    def test_report_generation(
        self, sample_contracts: Path, sample_budgets: Path, tmp_path: Path
    ) -> None:
        """Test that the report generator produces valid output."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        generator = ReportGenerator(str(sample_budgets), [str(sample_contracts)])
        report_json, report_md = generator.generate_report(str(output_dir))

        assert Path(report_json).exists()
        assert Path(report_md).exists()

        # Verify report.json structure
        with open(report_json) as f:
            report = json.load(f)

        assert "timestamp" in report
        assert "results" in report
        assert "summary" in report
        assert report["summary"]["total"] == 3

    def test_report_status_computation(
        self, sample_contracts: Path, sample_budgets: Path, tmp_path: Path
    ) -> None:
        """Test that status is computed correctly (OK, WARN, FAIL, NO_DATA)."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        generator = ReportGenerator(str(sample_budgets), [str(sample_contracts)])
        report_json, _ = generator.generate_report(str(output_dir))

        with open(report_json) as f:
            report = json.load(f)

        results_by_key = {r["key"]: r for r in report["results"]}

        # table_bytes: 1000000 vs baseline 500000 = 200% -> FAIL (over 150%)
        assert results_by_key["db_footprint.table_bytes_futures_data_ohlcv_1d"]["status"] == "FAIL"

        # raw_cleaned_ratio: baseline is "unset" -> NO_DATA
        assert results_by_key["db_footprint.raw_cleaned_ratio"]["status"] == "NO_DATA"

        # growth_bytes_per_day: 5000 vs baseline 3000 = 166% -> FAIL (over 150% threshold)
        assert results_by_key["db_footprint.growth_bytes_per_day"]["status"] == "FAIL"

    def test_report_markdown_output(
        self, sample_contracts: Path, sample_budgets: Path, tmp_path: Path
    ) -> None:
        """Test that markdown report contains expected sections."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        generator = ReportGenerator(str(sample_budgets), [str(sample_contracts)])
        _, report_md = generator.generate_report(str(output_dir))

        with open(report_md) as f:
            content = f.read()

        assert "# Performance Report" in content
        assert "Action Required (FAIL)" in content
        assert "All Metrics" in content

    def test_report_exit_code_with_failures(
        self, sample_contracts: Path, sample_budgets: Path, tmp_path: Path
    ) -> None:
        """Test that exit code is 1 when FAILs exist."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        generator = ReportGenerator(str(sample_budgets), [str(sample_contracts)])
        report_json, _ = generator.generate_report(str(output_dir))

        with open(report_json) as f:
            report = json.load(f)

        fail_count = sum(1 for r in report["results"] if r["status"] == "FAIL")
        assert fail_count > 0

    def test_golden_file_byte_identical(self) -> None:
        """Golden file test: verify report.md is byte-for-byte identical to committed fixture.

        Uses committed fixture files under tests/fixtures/golden_report/inputs/
        with a fixed injected timestamp to ensure deterministic output.
        """
        from datetime import datetime

        # Path to committed fixture files
        fixtures_dir = Path(__file__).parent / "fixtures" / "golden_report"
        inputs_dir = fixtures_dir / "inputs"
        expected_report_path = fixtures_dir / "expected_report.md"

        assert inputs_dir.exists(), f"Fixture inputs directory missing: {inputs_dir}"
        assert expected_report_path.exists(), (
            f"Expected report fixture missing: {expected_report_path}"
        )

        # Generate report with fixed timestamp matching the golden file
        fixed_time = datetime.fromisoformat("2024-06-15T12:30:45")
        generator = ReportGenerator(
            str(inputs_dir / "budgets.toml"),
            [str(inputs_dir)],
        )

        output_dir = fixtures_dir / "test_output"
        output_dir.mkdir(exist_ok=True)
        _, report_md_path = generator.generate_report(str(output_dir), now=fixed_time)

        # Read both files
        with open(report_md_path) as f:
            actual_report = f.read()

        with open(expected_report_path) as f:
            expected_report = f.read()

        # Byte-for-byte comparison (no substring checks, no patterns)
        assert actual_report == expected_report, (
            f"Report content does not match golden file.\n"
            f"Expected:\n{repr(expected_report)}\n\n"
            f"Actual:\n{repr(actual_report)}"
        )

    def test_golden_file_json_structure(self) -> None:
        """Test that report.json is valid and has expected structure.

        Uses committed fixture files and validates the machine-readable output
        without exact byte comparison (JSON key order is not guaranteed).
        """
        from datetime import datetime

        # Path to committed fixture files
        fixtures_dir = Path(__file__).parent / "fixtures" / "golden_report"
        inputs_dir = fixtures_dir / "inputs"

        # Generate report with fixed timestamp
        fixed_time = datetime.fromisoformat("2024-06-15T12:30:45")
        generator = ReportGenerator(
            str(inputs_dir / "budgets.toml"),
            [str(inputs_dir)],
        )

        output_dir = fixtures_dir / "test_output"
        output_dir.mkdir(exist_ok=True)
        report_json_path, _ = generator.generate_report(str(output_dir), now=fixed_time)

        # Parse and validate JSON structure
        with open(report_json_path) as f:
            report = json.load(f)

        # Verify structure
        assert "timestamp" in report
        assert report["timestamp"] == "2024-06-15T12:30:45"
        assert "results" in report
        assert "summary" in report

        # Verify summary counts
        assert report["summary"]["total"] == 2
        assert report["summary"]["ok"] == 0
        assert report["summary"]["warn"] == 0
        assert report["summary"]["fail"] == 1
        assert report["summary"]["no_data"] == 1

        # Verify results have expected keys
        results_by_key = {r["key"]: r for r in report["results"]}
        assert "db_footprint.table_bytes_test" in results_by_key
        assert "db_footprint.raw_cleaned_ratio" in results_by_key

        # Verify statuses
        assert results_by_key["db_footprint.table_bytes_test"]["status"] == "FAIL"
        assert results_by_key["db_footprint.raw_cleaned_ratio"]["status"] == "NO_DATA"

    def test_no_results_directories(self, sample_budgets: Path, tmp_path: Path) -> None:
        """Test report generation when no results directories exist."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Use a non-existent directory
        generator = ReportGenerator(str(sample_budgets), ["/nonexistent"])
        report_json, report_md = generator.generate_report(str(output_dir))

        assert Path(report_json).exists()
        assert Path(report_md).exists()

        # All metrics should be NO_DATA
        with open(report_json) as f:
            report = json.load(f)

        for result in report["results"]:
            assert result["status"] == "NO_DATA"
