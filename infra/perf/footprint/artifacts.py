"""Artifacts probe for system-level performance measurement.

Measures:
1. algogauge results/ directory sizes (if path provided)
2. Docker image sizes
3. Docker container resource usage
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def get_algogauge_artifacts(
    algogauge_path: str | None = None, prune: bool = False
) -> list[dict[str, Any]]:
    """Get sizes of algogauge results directories.

    Args:
        algogauge_path: Path to algogauge repo root. If None, returns NO_DATA.
        prune: If True, delete all but the newest 5 runs per binary.

    Returns:
        List of metrics with id, value, unit.
    """
    metrics = []

    if algogauge_path is None:
        return metrics  # NO_DATA

    results_dir = Path(algogauge_path) / "results"
    if not results_dir.exists():
        return metrics  # NO_DATA

    # Get sizes per binary subdirectory
    for binary_dir in results_dir.iterdir():
        if not binary_dir.is_dir():
            continue

        runs = sorted(binary_dir.glob("run_*"), key=lambda p: p.name)

        if prune and len(runs) > 5:
            # Delete all but the newest 5
            to_delete = runs[:-5]
            for run_dir in to_delete:
                shutil.rmtree(run_dir)
            runs = runs[-5:]

        # Calculate total size
        total_size = 0
        for run_dir in runs:
            for item in run_dir.rglob("*"):
                if item.is_file():
                    total_size += item.stat().st_size

        if total_size > 0:
            metrics.append(
                {
                    "id": f"algogauge_results_{binary_dir.name}",
                    "value": total_size,
                    "unit": "bytes",
                }
            )

    return metrics


def get_docker_image_sizes() -> list[dict[str, Any]]:
    """Get Docker image sizes.

    Returns:
        List of metrics with id, value, unit. Empty if Docker is not available.
    """
    metrics = []

    try:
        result = subprocess.run(
            ["docker", "image", "ls", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            return metrics  # NO_DATA

        # Parse docker image ls output
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                repo = data.get("Repository", "unknown")
                tag = data.get("Tag", "latest")
                size = data.get("Size", "0B")

                # Parse size string (e.g., "1.2 GB" -> bytes)
                size_bytes = _parse_docker_size(size)
                if size_bytes > 0:
                    metrics.append(
                        {
                            "id": f"docker_image_{repo}_{tag}",
                            "value": size_bytes,
                            "unit": "bytes",
                        }
                    )
            except json.JSONDecodeError:
                continue

    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # NO_DATA

    return metrics


def get_docker_container_stats() -> list[dict[str, Any]]:
    """Get current Docker container stats.

    Returns:
        List of metrics with id, value, unit. Empty if no containers running.
    """
    metrics = []

    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            return metrics  # NO_DATA

        # Parse docker stats output
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                container = data.get("Container", "unknown")
                mem_usage = data.get("MemUsage", "0B")
                cpu_percent = data.get("CPUPerc", "0%")

                # Parse memory usage string (e.g., "256MiB / 2GiB")
                mem_bytes = _parse_docker_size(mem_usage.split("/")[0].strip())
                if mem_bytes > 0:
                    metrics.append(
                        {
                            "id": f"docker_container_mem_{container}",
                            "value": mem_bytes,
                            "unit": "bytes",
                        }
                    )

                # Parse CPU percent
                cpu_value = float(cpu_percent.rstrip("%")) if cpu_percent != "0%" else 0.0
                if cpu_value > 0:
                    metrics.append(
                        {
                            "id": f"docker_container_cpu_{container}",
                            "value": cpu_value,
                            "unit": "percent",
                        }
                    )

            except (json.JSONDecodeError, ValueError):
                continue

    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # NO_DATA

    return metrics


def _parse_docker_size(size_str: str) -> int:
    """Parse Docker size string (e.g., '1.2 GB') to bytes.

    Args:
        size_str: Size string like '256MiB' or '1.2 GB'.

    Returns:
        Size in bytes, or 0 if parsing fails.
    """
    size_str = size_str.strip()
    if not size_str or size_str == "0B":
        return 0

    # Mapping of units to multipliers
    units = {
        "B": 1,
        "KB": 1024,
        "KiB": 1024,
        "MB": 1024 * 1024,
        "MiB": 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
        "GiB": 1024 * 1024 * 1024,
        "TB": 1024 * 1024 * 1024 * 1024,
        "TiB": 1024 * 1024 * 1024 * 1024,
    }

    # Extract number and unit
    for unit, multiplier in sorted(units.items(), key=lambda x: -len(x[0])):
        if size_str.endswith(unit):
            try:
                number = float(size_str[: -len(unit)].strip())
                return int(number * multiplier)
            except ValueError:
                return 0

    return 0


def probe_artifacts(
    algogauge_path: str | None = None,
    prune: bool = False,
    host: str | None = None,
    commit: str | None = None,
) -> dict[str, Any]:
    """Run all artifact probes and return combined contract JSON per spec §3.

    Args:
        algogauge_path: Optional path to algogauge repo.
        prune: If True, prune old algogauge runs.
        host: Hostname or identifier (defaults to hostname).
        commit: Git commit hash (defaults to 'unknown').

    Returns:
        Contract JSON dict with all artifact metrics.
    """
    import socket
    from datetime import UTC, datetime

    metrics = []

    # Algogauge artifacts
    metrics.extend(get_algogauge_artifacts(algogauge_path, prune))

    # Docker images
    metrics.extend(get_docker_image_sizes())

    # Docker containers
    metrics.extend(get_docker_container_stats())

    # Build contract JSON per spec §3
    contract = {
        "suite": "perf",
        "repo": "algogators",
        "probe": "artifacts",
        "captured_at": datetime.now(UTC).isoformat(),
        "environment": {
            "host": host or socket.gethostname(),
            "commit": commit or "unknown",
        },
        "metrics": metrics,
    }

    return contract


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Run artifact probe")
    parser.add_argument(
        "--algogauge-path",
        default=None,
        help="Path to algogauge repo (optional)",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Delete old algogauge runs (keep newest 5 per binary)",
    )

    args = parser.parse_args()

    result = probe_artifacts(args.algogauge_path, args.prune)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
