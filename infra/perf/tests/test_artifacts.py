"""Tests for artifacts.py probe functions.

Tests the parsing logic for Docker image sizes, container stats, and algogauge
artifacts using captured sample output as fixtures (no live Docker calls).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from infra.perf.footprint.artifacts import (
    get_algogauge_artifacts,
    get_docker_container_stats,
    get_docker_image_sizes,
    probe_artifacts,
)


class TestDockerImageSizeParser:
    """Test Docker image size parsing."""

    @pytest.fixture
    def docker_image_ls_output(self) -> str:
        """Sample output from 'docker image ls --format json'."""
        return '{"Containers":"3","CreatedAt":"2024-01-15 10:30:15 +0000 UTC","CreatedSince":"2 weeks ago","Digest":"sha256:abc123","ID":"sha256:abc123def456","Repository":"algogators","Size":"512MB","Tag":"latest","VirtualSize":"512MB"}\n{"Containers":"0","CreatedAt":"2024-01-14 15:20:10 +0000 UTC","CreatedSince":"2 weeks ago","Digest":"sha256:def456","ID":"sha256:def456ghi789","Repository":"postgres","Size":"256MB","Tag":"16-alpine","VirtualSize":"256MB"}\n'  # noqa: E501

    @pytest.fixture
    def docker_image_empty_output(self) -> str:
        """Empty output from 'docker image ls --format json' (no images)."""
        return ""

    @pytest.fixture
    def docker_image_malformed_output(self) -> str:
        """Malformed output that should not crash."""
        return '{"Containers":"3","Repository":"algogators","Size":"512MB"}\nNOT_JSON_LINE\n{"Containers":"0","Repository":"postgres","Size":"256MB"}\n'

    def test_parse_docker_images_normal_output(self, docker_image_ls_output: str) -> None:
        """Test parsing normal docker image ls output with multiple images."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=docker_image_ls_output)
            metrics = get_docker_image_sizes()

            # Should parse 2 images successfully
            assert len(metrics) == 2
            assert metrics[0]["id"] == "docker_image_algogators_latest"
            assert metrics[0]["value"] == 512 * 1024 * 1024  # 512MB in bytes
            assert metrics[0]["unit"] == "bytes"
            assert metrics[1]["id"] == "docker_image_postgres_16-alpine"
            assert metrics[1]["value"] == 256 * 1024 * 1024  # 256MB in bytes

    def test_parse_docker_images_empty_output(self, docker_image_empty_output: str) -> None:
        """Test parsing empty docker image ls output (no images)."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=docker_image_empty_output)
            metrics = get_docker_image_sizes()

            # Should return empty list (NO_DATA)
            assert metrics == []

    def test_parse_docker_images_malformed_output(self, docker_image_malformed_output: str) -> None:
        """Test parsing malformed output (should skip bad lines, not crash)."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=docker_image_malformed_output)
            metrics = get_docker_image_sizes()

            # Should parse 2 good images, skip malformed line
            assert len(metrics) == 2
            assert metrics[0]["id"] == "docker_image_algogators_latest"
            assert metrics[1]["id"] == "docker_image_postgres_latest"

    def test_docker_image_unavailable(self) -> None:
        """Test when Docker is not available (returncode != 0)."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            metrics = get_docker_image_sizes()

            # Should return empty (NO_DATA)
            assert metrics == []

    def test_docker_image_timeout(self) -> None:
        """Test when Docker call times out."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("docker", 10)
            metrics = get_docker_image_sizes()

            # Should return empty (NO_DATA)
            assert metrics == []

    def test_docker_image_not_found(self) -> None:
        """Test when Docker command is not found."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            metrics = get_docker_image_sizes()

            # Should return empty (NO_DATA)
            assert metrics == []


class TestDockerContainerStatsParser:
    """Test Docker container stats parsing."""

    @pytest.fixture
    def docker_stats_output(self) -> str:
        """Sample output from 'docker stats --no-stream --format json'."""
        return '{"BlockIO":"0B / 0B","CPUPerc":"2.50%","Container":"abc123def456","ID":"abc123def456","MemPerc":"5.25%","MemUsage":"512MiB / 4GiB","Name":"postgres","NetIO":"0B / 0B","PIDs":"15"}\n{"BlockIO":"0B / 0B","CPUPerc":"0.75%","Container":"ghi789jkl012","ID":"ghi789jkl012","MemPerc":"2.10%","MemUsage":"256MiB / 4GiB","Name":"redis","NetIO":"0B / 0B","PIDs":"8"}\n'  # noqa: E501

    @pytest.fixture
    def docker_stats_empty_output(self) -> str:
        """Empty output from 'docker stats --no-stream --format json' (no containers)."""
        return ""

    @pytest.fixture
    def docker_stats_malformed_output(self) -> str:
        """Malformed stats output."""
        return '{"Container":"abc123","MemUsage":"512MiB / 4GiB","CPUPerc":"1.5%"}\nNOT_JSON\n{"Container":"ghi789","MemUsage":"256MiB / 4GiB","CPUPerc":"0.5%"}\n'

    def test_parse_docker_stats_normal_output(self, docker_stats_output: str) -> None:
        """Test parsing normal docker stats output."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=docker_stats_output)
            metrics = get_docker_container_stats()

            # Should parse 2 containers with memory and CPU
            # Each container has 2 metrics (mem + cpu) = 4 total
            assert len(metrics) == 4

            # Check first container memory
            mem_metrics = [m for m in metrics if "mem" in m["id"]]
            assert len(mem_metrics) == 2
            assert mem_metrics[0]["value"] == 512 * 1024 * 1024  # 512MiB in bytes

            # Check first container CPU
            cpu_metrics = [m for m in metrics if "cpu" in m["id"]]
            assert len(cpu_metrics) == 2
            assert cpu_metrics[0]["value"] == 2.5  # 2.50%

    def test_parse_docker_stats_empty_output(self, docker_stats_empty_output: str) -> None:
        """Test parsing empty docker stats output (no containers)."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=docker_stats_empty_output)
            metrics = get_docker_container_stats()

            # Should return empty (NO_DATA)
            assert metrics == []

    def test_parse_docker_stats_malformed_output(self, docker_stats_malformed_output: str) -> None:
        """Test parsing malformed stats output."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=docker_stats_malformed_output)
            metrics = get_docker_container_stats()

            # Should parse 2 good entries, skip malformed
            assert len(metrics) == 4  # 2 containers * 2 metrics each

    def test_docker_stats_unavailable(self) -> None:
        """Test when Docker stats is not available."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            metrics = get_docker_container_stats()

            assert metrics == []


class TestAlgogaugeArtifacts:
    """Test algogauge directory parsing."""

    def test_algogauge_no_path_given(self) -> None:
        """Test when no algogauge path is provided."""
        metrics = get_algogauge_artifacts(algogauge_path=None)

        # Should return empty (NO_DATA)
        assert metrics == []

    def test_algogauge_path_not_exists(self, tmp_path: Path) -> None:
        """Test when algogauge path doesn't exist."""
        nonexistent = tmp_path / "nonexistent" / "algogauge"
        metrics = get_algogauge_artifacts(algogauge_path=str(nonexistent))

        # Should return empty (NO_DATA)
        assert metrics == []

    def test_algogauge_results_directory_exists(self, tmp_path: Path) -> None:
        """Test parsing algogauge results directory with files."""
        # Create fake algogauge structure
        algogauge_root = tmp_path / "algogauge"
        results_dir = algogauge_root / "results"
        results_dir.mkdir(parents=True)

        binary_dir = results_dir / "my_binary"
        binary_dir.mkdir()

        # Create fake run directories with files
        for i in range(3):
            run_dir = binary_dir / f"run_{i:03d}"
            run_dir.mkdir()
            # Create a few files with known sizes
            for j in range(2):
                test_file = run_dir / f"file_{j}.dat"
                test_file.write_text("x" * 1000)  # 1000 bytes each

        metrics = get_algogauge_artifacts(algogauge_path=str(algogauge_root))

        # Should have 1 metric for the binary
        assert len(metrics) == 1
        assert metrics[0]["id"] == "algogauge_results_my_binary"
        # 3 runs * 2 files * 1000 bytes = 6000 bytes
        assert metrics[0]["value"] == 6000
        assert metrics[0]["unit"] == "bytes"

    def test_algogauge_prune_keeps_newest_five(self, tmp_path: Path) -> None:
        """Test that prune flag keeps only newest 5 runs."""
        algogauge_root = tmp_path / "algogauge"
        results_dir = algogauge_root / "results"
        results_dir.mkdir(parents=True)

        binary_dir = results_dir / "my_binary"
        binary_dir.mkdir()

        # Create 10 run directories
        for i in range(10):
            run_dir = binary_dir / f"run_{i:03d}"
            run_dir.mkdir()
            test_file = run_dir / f"file.dat"
            test_file.write_text("x" * 1000)

        # Prune to keep only newest 5
        metrics = get_algogauge_artifacts(algogauge_path=str(algogauge_root), prune=True)

        # Verify only 5 runs remain
        remaining_runs = list((binary_dir).glob("run_*"))
        assert len(remaining_runs) == 5
        # Should be run_005 through run_009
        run_names = sorted([d.name for d in remaining_runs])
        assert run_names == ["run_005", "run_006", "run_007", "run_008", "run_009"]

        # Metric should reflect only remaining files
        # 5 runs * 1 file * 1000 bytes = 5000 bytes
        assert metrics[0]["value"] == 5000

    def test_algogauge_empty_results_directory(self, tmp_path: Path) -> None:
        """Test when results directory exists but is empty."""
        algogauge_root = tmp_path / "algogauge"
        results_dir = algogauge_root / "results"
        results_dir.mkdir(parents=True)

        metrics = get_algogauge_artifacts(algogauge_path=str(algogauge_root))

        # Should return empty (no binaries found)
        assert metrics == []


class TestProbeArtifacts:
    """Test the combined probe_artifacts function."""

    def test_probe_artifacts_combined(self, tmp_path: Path) -> None:
        """Test probe_artifacts combines all sources into valid contract JSON."""
        with patch("subprocess.run") as mock_run:
            # Mock docker image ls
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"Repository":"test","Tag":"v1","Size":"100MB"}\n',
            )

            result = probe_artifacts(host="test-server", commit="abc123def456")

            # Verify contract structure per spec §3
            assert result["suite"] == "perf"
            assert result["repo"] == "algogators"
            assert result["probe"] == "artifacts"
            assert "captured_at" in result
            assert result["environment"]["host"] == "test-server"
            assert result["environment"]["commit"] == "abc123def456"
            assert isinstance(result["metrics"], list)

    def test_probe_artifacts_default_values(self) -> None:
        """Test probe_artifacts uses defaults for host and commit."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")

            result = probe_artifacts()

            # Should use socket hostname by default
            assert result["environment"]["host"] is not None
            assert result["environment"]["commit"] == "unknown"
