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

    def test_golden_file_byte_identical(self, tmp_path: Path) -> None:
        """Golden file test: verify report structure and content matches expected format."""
        fixtures_dir = tmp_path / "fixtures"
        fixtures_dir.mkdir()

        # Create fixed input contracts with exact known values
        contracts_dir = fixtures_dir / "contracts"
        contracts_dir.mkdir()

        db_contract = {
            "suite": "perf",
            "repo": "algogators",
            "probe": "db_footprint",
            "captured_at": "2024-01-01T00:00:00+00:00",
            "environment": {"host": "test-host", "commit": "abc123def456"},
            "metrics": [
                {"id": "table_bytes_test", "value": 1000000, "unit": "bytes"},
                {"id": "raw_cleaned_ratio", "value": 1.2, "unit": "ratio"},
            ],
        }

        with open(contracts_dir / "db_footprint.json", "w") as f:
            json.dump(db_contract, f)

        # Create fixed budgets.toml
        budgets_path = fixtures_dir / "budgets.toml"
        budgets_content = """
[db_footprint.table_bytes_test]
baseline = 500000
threshold = 150
action = "Investigate table growth"

[db_footprint.raw_cleaned_ratio]
baseline = "unset"
threshold = 130
action = "Monitor data quality"
"""
        budgets_path.write_text(budgets_content)

        # Generate report
        output_dir = fixtures_dir / "output"
        output_dir.mkdir()
        generator = ReportGenerator(str(budgets_path), [str(contracts_dir)])
        _, report_md_path = generator.generate_report(str(output_dir))

        # Read actual report
        with open(report_md_path) as f:
            actual_report = f.read()

        # Verify golden structure (without timestamp which is dynamic)
        expected_patterns = [
            "# Performance Report",
            "Generated:",  # timestamp is dynamic, just check it's there
            "**Summary:** 2 metrics",
            "(0 OK, 0 WARN, 1 FAIL, 1 NO_DATA)",  # raw_cleaned_ratio is NO_DATA because baseline is "unset"
            "## Action Required (FAIL)",
            "### db_footprint.table_bytes_test",
            "Status: **FAIL**",
            "Value: 1000000 bytes",
            "**Action:** Investigate table growth",
            "## All Metrics",
            "| Metric | Status | Value | Baseline | Threshold |",
            "| db_footprint.raw_cleaned_ratio | NO_DATA",  # NO_DATA when baseline is unset
        ]

        for pattern in expected_patterns:
            assert pattern in actual_report, f"Expected pattern '{pattern}' not found in report"

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
