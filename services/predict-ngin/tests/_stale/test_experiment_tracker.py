"""Unit tests for ExperimentTracker."""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from src.experiments.tracker import ExperimentTracker, ExperimentRun


@pytest.fixture
def temp_experiments_dir():
    """Create a temporary directory for experiments."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def tracker(temp_experiments_dir):
    """Create a tracker with temporary directory."""
    return ExperimentTracker(experiments_dir=temp_experiments_dir)


class TestExperimentRun:
    def test_to_dict(self):
        run = ExperimentRun(
            run_id="test_123",
            name="test_experiment",
            timestamp=datetime(2025, 1, 15, 12, 0, 0),
            parameters={"lr": 0.01, "epochs": 100},
            metrics={"accuracy": 0.95},
            tags=["test", "unit"],
        )

        d = run.to_dict()

        assert d["run_id"] == "test_123"
        assert d["name"] == "test_experiment"
        assert "parameters" in d
        assert "2025-01-15" in d["timestamp"]

    def test_from_row(self):
        row = {
            "run_id": "test_456",
            "name": "another_test",
            "timestamp": "2025-01-15T12:00:00",
            "parameters": '{"batch_size": 32}',
            "git_commit": "abc123",
            "metrics": '{"loss": 0.1}',
            "artifacts": "{}",
            "tags": '["production"]',
            "status": "completed",
            "end_time": None,
            "notes": "",
        }

        run = ExperimentRun.from_row(row)

        assert run.run_id == "test_456"
        assert run.parameters == {"batch_size": 32}
        assert run.metrics == {"loss": 0.1}
        assert run.tags == ["production"]


class TestExperimentTracker:
    def test_init(self, temp_experiments_dir):
        tracker = ExperimentTracker(experiments_dir=temp_experiments_dir)

        assert tracker.experiments_dir.exists()
        assert tracker.db_path.exists()
        assert tracker.artifacts_dir.exists()

    def test_run_context_manager(self, tracker):
        with tracker.run("test_run", {"param1": "value1"}) as run:
            assert run.name == "test_run"
            assert run.status == "running"
            assert run.parameters == {"param1": "value1"}

        # After context, run should be completed
        saved_run = tracker.get_run(run.run_id)
        assert saved_run.status == "completed"
        assert saved_run.end_time is not None

    def test_run_failure(self, tracker):
        with pytest.raises(ValueError):
            with tracker.run("failing_run", {}) as run:
                raise ValueError("Test error")

        saved_run = tracker.get_run(run.run_id)
        assert saved_run.status == "failed"
        assert "Test error" in saved_run.notes

    def test_log_metrics(self, tracker):
        with tracker.run("metrics_test", {}) as run:
            tracker.log_metrics(run.run_id, {"sharpe": 1.5, "win_rate": 0.65})

        saved_run = tracker.get_run(run.run_id)
        assert saved_run.metrics["sharpe"] == 1.5
        assert saved_run.metrics["win_rate"] == 0.65

    def test_log_artifact(self, tracker, temp_experiments_dir):
        # Create a test artifact
        artifact_path = Path(temp_experiments_dir) / "test_artifact.txt"
        artifact_path.write_text("test content")

        with tracker.run("artifact_test", {}) as run:
            stored_path = tracker.log_artifact(run.run_id, "test_file", str(artifact_path))

        saved_run = tracker.get_run(run.run_id)
        assert "test_file" in saved_run.artifacts
        assert Path(stored_path).exists()

    def test_search_runs(self, tracker):
        # Create multiple runs
        for i in range(3):
            with tracker.run(f"search_test_{i}", {"index": i}) as run:
                tracker.log_metrics(run.run_id, {"sharpe": i * 0.5})

        # Search by name pattern
        results = tracker.search_runs(name_pattern="search_test_*")
        assert len(results) == 3

        # Search by min sharpe
        results = tracker.search_runs(min_sharpe=0.5)
        assert len(results) >= 2

    def test_compare_runs(self, tracker):
        run_ids = []
        for i in range(2):
            with tracker.run(f"compare_test_{i}", {"lr": 0.01 * (i + 1)}) as run:
                tracker.log_metrics(run.run_id, {"accuracy": 0.9 + i * 0.05})
                run_ids.append(run.run_id)

        comparison = tracker.compare_runs(run_ids)

        assert len(comparison) == 2
        assert "accuracy" in comparison.columns
        assert "param_lr" in comparison.columns

    def test_get_best_run(self, tracker):
        for sharpe in [1.0, 2.0, 1.5]:
            with tracker.run("best_test", {}) as run:
                tracker.log_metrics(run.run_id, {"sharpe": sharpe})

        best = tracker.get_best_run(name_pattern="best_test", metric="sharpe")

        assert best is not None
        assert best.metrics["sharpe"] == 2.0

    def test_delete_run(self, tracker):
        with tracker.run("delete_test", {}) as run:
            run_id = run.run_id

        assert tracker.get_run(run_id) is not None

        deleted = tracker.delete_run(run_id)

        assert deleted is True
        assert tracker.get_run(run_id) is None
