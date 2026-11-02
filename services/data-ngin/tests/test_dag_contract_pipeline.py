"""The per-contract task bodies of dags/new_data_pipeline_dag.py, run with airflow.sdk and pendulum
stubbed (Airflow is not a workspace dependency; the DAG ships to the Airflow image)."""

import importlib.util
import sys
import types
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from data_ngin.application import pipeline_tasks

DAG = Path(__file__).resolve().parents[1] / "dags" / "new_data_pipeline_dag.py"


def _load_dag(context):
    sdk = types.ModuleType("airflow.sdk")
    sdk.task = lambda f: f
    sdk.dag = lambda **_kw: lambda _f: lambda: None
    sdk.get_current_context = lambda: context
    airflow = types.ModuleType("airflow")
    airflow.sdk = sdk
    pendulum = types.ModuleType("pendulum")
    pendulum.timezone = lambda _name: None
    stubs = {"airflow": airflow, "airflow.sdk": sdk, "pendulum": pendulum}
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location("new_data_pipeline_dag_under_test", DAG)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _run(result, try_number):
    dag = _load_dag({"ti": SimpleNamespace(try_number=try_number, max_tries=3)})
    with (
        patch.object(pipeline_tasks, "bar_date_from_context", return_value=date(2026, 10, 21)),
        patch.object(pipeline_tasks, "run_contract_close", return_value=result),
    ):
        return dag.run_contract_pipeline()


def test_last_try_that_loads_no_root_fails_the_task():
    failed = {"skipped": False, "status": "failed", "loaded": 0, "expected": 40, "missing": ["CL"]}
    with pytest.raises(RuntimeError, match="no contract bars loaded"):
        _run(failed, try_number=4)


def test_last_try_with_some_roots_returns_the_partial_result():
    partial = {
        "skipped": False,
        "status": "partial",
        "loaded": 39,
        "expected": 40,
        "missing": ["CL"],
    }
    assert _run(partial, try_number=4) == partial


def test_earlier_try_with_missing_roots_raises_to_retry():
    partial = {
        "skipped": False,
        "status": "partial",
        "loaded": 39,
        "expected": 40,
        "missing": ["CL"],
    }
    with pytest.raises(RuntimeError, match="retrying"):
        _run(partial, try_number=1)
