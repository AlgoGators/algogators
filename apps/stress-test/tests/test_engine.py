import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from conftest import SETTINGS
from fake_tradengin import write_fake_engine
from stress_test import engine
from stress_test.snapshot import load_bars


@pytest.fixture(autouse=True)
def _forget_tradengin(monkeypatch: pytest.MonkeyPatch):
    """Each test imports its own engine module."""
    monkeypatch.delitem(sys.modules, "tradengin", raising=False)
    monkeypatch.delenv(engine.ENGINE_ENV, raising=False)
    yield
    sys.modules.pop("tradengin", None)


def test_the_published_module_runs_replays_with_two_caveats(tmp_path: Path) -> None:
    caps = engine.capabilities(engine.import_engine(write_fake_engine(tmp_path / "pub", False)))
    assert caps.config_runs and caps.missing_for_replay() == []
    assert not caps.bar_instrument_id and not caps.fee_per_contract
    assert len(caps.caveats()) == 2
    sys.modules.pop("tradengin")
    full = engine.capabilities(engine.import_engine(write_fake_engine(tmp_path / "full")))
    assert full.caveats() == []
    sys.modules.pop("tradengin")
    old = engine.capabilities(
        engine.import_engine(write_fake_engine(tmp_path / "old", config_runs=False))
    )
    assert old.missing_for_replay() == ["BacktestRunConfig.config_base_path / export_csv"]


def test_a_missing_engine_says_how_to_get_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "tradengin", None)  # makes the import fail
    with pytest.raises(SystemExit, match="pip install tradengin"):
        engine.import_engine(None)


def test_the_config_tree_is_the_recorded_settings_sized_on_the_account(tmp_path: Path) -> None:
    out = engine.write_config_tree(SETTINGS, tmp_path / "cfg", capital=489_000.0)
    book = json.loads((out / "portfolios" / engine.PORTFOLIO_DIR / "portfolio.json").read_text())
    risk = json.loads((out / "portfolios" / engine.PORTFOLIO_DIR / "risk.json").read_text())
    defaults = json.loads((out / "defaults.json").read_text())
    used = SETTINGS["settings_used"]["config"]
    assert risk == used["risk"] and "risk" not in book
    assert book["strategies"] == used["strategies"]
    assert book["initial_capital"] == book["starting_capital"] == 489_000.0
    assert book["backtest"]["store_trade_details"] is False  # never stores, whatever was recorded
    assert defaults["statistics"]["sessions_per_year"]["futures"] == 252.0
    assert defaults["database"]["host"].endswith(".invalid")
    unsized = engine.write_config_tree(SETTINGS, tmp_path / "plain")
    plain = json.loads(
        (unsized / "portfolios" / engine.PORTFOLIO_DIR / "portfolio.json").read_text()
    )
    assert plain["initial_capital"] == used["initial_capital"]
    with pytest.raises(SystemExit, match="no recorded settings"):
        engine.write_config_tree({}, tmp_path / "none")


def test_the_warm_up_is_the_longest_recorded_lookback() -> None:
    assert engine.warmup_sessions(SETTINGS) == 256
    assert engine.warmup_sessions({"settings_used": {"config": {}}}) == 256


def _spec(snapshot_dir: Path, tmp_path: Path, module_dir: Path) -> engine.RunSpec:
    bars = load_bars(snapshot_dir)
    last = bars.date.max()
    path = bars[bars.date == last].assign(date=last + pd.offsets.BDay(1))
    files = [
        engine.write_bars(bars, tmp_path / "history.csv"),
        engine.write_bars(path, tmp_path / "path.csv"),
    ]
    cfg = engine.write_config_tree(SETTINGS, tmp_path / "cfg", capital=1_000_000.0)
    return engine.RunSpec(
        bars=files,
        contracts=str(snapshot_dir / "contracts.csv"),
        config_dir=str(cfg),
        out_dir=str(tmp_path / "run"),
        start=(last - pd.Timedelta(30, unit="D")).date().isoformat(),
        end=(last + pd.offsets.BDay(1)).date().isoformat(),
        keep_from=last.date().isoformat(),
        module_dir=str(module_dir),
    )


def test_a_run_in_a_fresh_process_hands_the_engine_bars_fees_and_capital(
    snapshot_dir: Path, tmp_path: Path
) -> None:
    fake = write_fake_engine(tmp_path / "fake")
    spec = _spec(snapshot_dir, tmp_path, fake)
    result = engine.run_in_subprocess(spec)
    assert result.ok, result.error
    assert [d for d, _ in result.equity] == [spec.keep_from, spec.end]  # rows from keep_from on
    last_day = {(s, q) for d, s, q in result.positions if d == spec.end}
    assert last_day == {("ZN.v.0", -3.0), ("CL.v.0", 1.0)}
    handed = json.loads(next((tmp_path / "run" / "csv").glob("*/handed.json")).read_text())
    assert handed["capital"] == 1_000_000.0
    assert "" not in handed["ids"] and len(handed["ids"]) == 6  # every bar carries its contract
    assert handed["fees"]["ZN"] == 0.85
    assert (tmp_path / "run" / "spec.json").exists() and (tmp_path / "run" / "result.json").exists()


def test_the_published_module_gets_no_ids_or_fees(snapshot_dir: Path, tmp_path: Path) -> None:
    fake = write_fake_engine(tmp_path / "pub", ids_and_fees=False)
    result = engine.run_in_subprocess(_spec(snapshot_dir, tmp_path, fake))
    assert result.ok, result.error
    handed = json.loads(next((tmp_path / "run" / "csv").glob("*/handed.json")).read_text())
    assert handed["ids"] == [""] and handed["fees"]["ZN"] is None


def test_an_engine_error_comes_back_as_a_failed_result(snapshot_dir: Path, tmp_path: Path) -> None:
    fake = write_fake_engine(tmp_path / "fake")
    spec = _spec(snapshot_dir, tmp_path, fake)
    book = Path(spec.config_dir) / "portfolios" / engine.PORTFOLIO_DIR / "portfolio.json"
    conf = json.loads(book.read_text())
    conf["fake_book"] = {"NOPE.v.0": 1.0}
    book.write_text(json.dumps(conf))
    result = engine.run_in_subprocess(spec)
    assert not result.ok and "no bars for NOPE.v.0" in result.error


def test_an_engine_too_old_refuses_the_run(snapshot_dir: Path, tmp_path: Path) -> None:
    fake = write_fake_engine(tmp_path / "old", config_runs=False)
    result = engine.run_in_subprocess(_spec(snapshot_dir, tmp_path, fake))
    assert not result.ok and "config_base_path" in result.error


def test_a_crashed_run_reports_its_output(snapshot_dir: Path, tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "tradengin.py").write_text("raise SystemExit('the module would not load')\n")
    result = engine.run_in_subprocess(_spec(snapshot_dir, tmp_path, broken))
    assert not result.ok and "without a result" in result.error


def test_a_run_in_this_process_reads_the_export_back(
    snapshot_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # run_spec moves into the run's directory (the engine logs there)
    fake = write_fake_engine(tmp_path / "fake")
    result = engine.run_spec(_spec(snapshot_dir, tmp_path, fake))
    assert result.ok and result.seconds >= 0 and len(result.equity) == 2
