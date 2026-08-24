"""Report generator for performance metrics.

Reads budgets.toml and contract JSON files, joins metrics against budgets,
and generates both machine-readable and human-readable reports.
"""

import json
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any


class ReportGenerator:
    """Generates performance reports from budgets and metrics."""

    def __init__(self, budgets_path: str, results_dirs: list[str] | None = None):
        """Initialize the report generator.

        Args:
            budgets_path: Path to budgets.toml file.
            results_dirs: List of directories to search for contract JSON files.
                         Defaults to [infra/perf/footprint/snapshots/].
        """
        self.budgets_path = Path(budgets_path)
        self.results_dirs = [Path(d) for d in (results_dirs or ["infra/perf/footprint/snapshots/"])]

        # Load budgets
        with open(self.budgets_path, "rb") as f:
            self.budgets = tomllib.load(f)

    def _flatten_budgets(self) -> dict[str, dict[str, Any]]:
        """Flatten nested budget TOML structure to dotted keys.

        Converts {"probe": {"metric_id": {...}}} to {"probe.metric_id": {...}}

        Returns:
            Flattened budget dict.
        """
        flat = {}
        for key, value in self.budgets.items():
            if isinstance(value, dict) and not isinstance(value.get("baseline"), str):
                # This looks like a nested table (probe level)
                for subkey, subvalue in value.items():
                    if isinstance(subvalue, dict):
                        flat[f"{key}.{subkey}"] = subvalue
            else:
                # Top-level key with values
                flat[key] = value
        return flat

    def collect_metrics(self) -> dict[str, dict[str, Any]]:
        """Collect all metrics from contract JSON files.

        Returns:
            Dict mapping 'repo.probe.metric_id' -> {value, unit, timestamp, ...}
        """
        metrics = {}

        for results_dir in self.results_dirs:
            if not results_dir.exists():
                continue

            for json_file in results_dir.glob("*.json"):
                try:
                    with open(json_file) as f:
                        contract = json.load(f)

                    repo = contract.get("repo", "unknown")
                    probe = contract.get("probe_name", "unknown")
                    timestamp = contract.get("timestamp")

                    for metric in contract.get("metrics", []):
                        metric_id = metric.get("id", "unknown")
                        key = f"{repo}.{probe}.{metric_id}"

                        metrics[key] = {
                            "value": metric.get("value"),
                            "unit": metric.get("unit"),
                            "timestamp": timestamp,
                            "status": metric.get("status"),
                        }

                except (json.JSONDecodeError, KeyError):
                    continue

        return metrics

    def generate_report(self, output_dir: str | None = None) -> tuple[str, str]:
        """Generate report.json and report.md.

        Args:
            output_dir: Directory to write reports. Defaults to current directory.

        Returns:
            Tuple of (report_json_path, report_md_path).
        """
        output_dir = Path(output_dir or ".")
        output_dir.mkdir(parents=True, exist_ok=True)

        metrics = self.collect_metrics()

        # Build report structure
        report = {
            "timestamp": datetime.now().isoformat(),
            "results": [],
            "summary": {
                "total": 0,
                "ok": 0,
                "warn": 0,
                "fail": 0,
                "no_data": 0,
            },
        }

        # Flatten nested budget keys to handle toml structure
        # budgets.toml has [probe.metric_id] which becomes {"probe": {"metric_id": {...}}}
        flat_budgets = self._flatten_budgets()

        # Process each budget entry
        for dotted_key, budget_info in flat_budgets.items():
            # Try multiple key formats to find matching metric
            metric_key = f"algogators.{dotted_key}"
            metric_data = metrics.get(metric_key)

            # Determine status
            status = "NO_DATA"
            if metric_data is not None:
                value = metric_data.get("value")
                baseline = budget_info.get("baseline")

                if value is None or metric_data.get("status") == "NO_DATA":
                    status = "NO_DATA"
                elif baseline == "unset":
                    # Baseline not yet captured
                    status = "NO_DATA"
                else:
                    # Compare against budget threshold
                    threshold = budget_info.get("threshold", 100)
                    warn_threshold = threshold * 0.8

                    if isinstance(baseline, (int, float)) and isinstance(value, (int, float)):
                        percentage = (value / baseline * 100) if baseline > 0 else 0
                        if percentage > threshold:
                            status = "FAIL"
                        elif percentage >= warn_threshold:
                            status = "WARN"
                        else:
                            status = "OK"

            result = {
                "key": dotted_key,
                "status": status,
                "value": metric_data.get("value") if metric_data else None,
                "unit": metric_data.get("unit") if metric_data else None,
                "baseline": budget_info.get("baseline"),
                "threshold": budget_info.get("threshold"),
                "action": budget_info.get("action", ""),
            }

            report["results"].append(result)
            report["summary"]["total"] += 1
            report["summary"][status.lower()] += 1

        # Write report.json
        report_json_path = output_dir / "report.json"
        with open(report_json_path, "w") as f:
            json.dump(report, f, indent=2)

        # Write report.md
        report_md_path = output_dir / "report.md"
        md_content = self._generate_markdown(report)
        with open(report_md_path, "w") as f:
            f.write(md_content)

        return str(report_json_path), str(report_md_path)

    def _generate_markdown(self, report: dict[str, Any]) -> str:
        """Generate markdown report content.

        Args:
            report: Report structure from generate_report().

        Returns:
            Markdown formatted report.
        """
        lines = ["# Performance Report\n"]

        # Summary section
        summary = report["summary"]
        lines.append(f"Generated: {report['timestamp']}\n")
        lines.append(f"**Summary:** {summary['total']} metrics")
        lines.append(
            f"({summary['ok']} OK, {summary['warn']} WARN, {summary['fail']} FAIL, {summary['no_data']} NO_DATA)\n"
        )

        # FAILs first
        fails = [r for r in report["results"] if r["status"] == "FAIL"]
        if fails:
            lines.append("## Action Required (FAIL)\n")
            for result in fails:
                lines.append(f"### {result['key']}")
                lines.append("\nStatus: **FAIL**")
                if result["value"] is not None:
                    lines.append(f"Value: {result['value']} {result['unit']}")
                if result["action"]:
                    lines.append(f"\n**Action:** {result['action']}")
                lines.append("")

        # Then WARNs
        warns = [r for r in report["results"] if r["status"] == "WARN"]
        if warns:
            lines.append("## Review Recommended (WARN)\n")
            for result in warns:
                lines.append(f"### {result['key']}")
                lines.append("\nStatus: **WARN**")
                if result["value"] is not None:
                    lines.append(f"Value: {result['value']} {result['unit']}")
                if result["action"]:
                    lines.append(f"\n**Action:** {result['action']}")
                lines.append("")

        # Then everything else in a table
        others = [r for r in report["results"] if r["status"] not in ("FAIL", "WARN")]
        if others:
            lines.append("## All Metrics\n")
            lines.append("| Metric | Status | Value | Baseline | Threshold |")
            lines.append("|--------|--------|-------|----------|-----------|")
            for result in others:
                value_str = str(result["value"]) if result["value"] is not None else "-"
                baseline_str = str(result["baseline"]) if result["baseline"] is not None else "-"
                threshold_str = str(result["threshold"]) if result["threshold"] is not None else "-"
                lines.append(
                    f"| {result['key']} | {result['status']} | {value_str} | {baseline_str} | {threshold_str} |"
                )
            lines.append("")

        return "\n".join(lines)


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate performance report")
    parser.add_argument(
        "--budgets",
        default="infra/perf/budgets.toml",
        help="Path to budgets.toml",
    )
    parser.add_argument(
        "--results-dir",
        action="append",
        dest="results_dirs",
        help="Directory with contract JSON files (can be repeated)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to write reports",
    )

    args = parser.parse_args()

    generator = ReportGenerator(args.budgets, args.results_dirs)
    report_json, report_md = generator.generate_report(args.output_dir)

    print("Reports written:")
    print(f"  {report_json}")
    print(f"  {report_md}")

    # Exit with code 1 if any FAILs
    with open(report_json) as f:
        report = json.load(f)

    fail_count = sum(1 for r in report["results"] if r["status"] == "FAIL")
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
