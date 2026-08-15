"""Runs the shipped end-to-end example against the fake provider (no network)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from algoterminal.data.universe import UniverseStore
from algoterminal.research import storage

from .conftest import FakeProvider

_EXAMPLE_PATH = Path(__file__).parent.parent / "examples" / "end_to_end.py"


def _load_example_module():
    spec = importlib.util.spec_from_file_location("algoterminal_example_end_to_end", _EXAMPLE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_end_to_end_example_offline(monkeypatch, capsys):
    example = _load_example_module()
    monkeypatch.setattr(example, "default_provider", lambda: FakeProvider(n=300))

    example.main()

    assert "demo-tech" in {u.name for u in UniverseStore().list()}
    slug = "aapl-sma-crossover-momentum"
    assert slug in storage.list_slugs()
    record = storage.latest_record(slug)
    assert record.strategy_path.exists()
    assert record.backtest_results_path.exists()
    assert record.writeup_path.exists()
    out = capsys.readouterr().out
    assert "Backtest" in out
