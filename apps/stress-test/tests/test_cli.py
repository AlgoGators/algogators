from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from stress_test import cli


def test_shock_writes_the_report_with_an_edit(
    snapshot_dir: Path, package_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    code = cli.main(
        [
            "shock",
            str(snapshot_dir),
            "--events",
            str(package_dir),
            "--edit",
            "ZT=0",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    folder = out / "TEST_PORTFOLIO_2017-06-01"
    assert (folder / "report.md").exists()
    results = pd.read_csv(folder / "results.csv")
    assert set(results.book) == {"A system = B proposal", "C final", "Edit"}
    assert "C final with ZT=0" in capsys.readouterr().out


def test_shock_rejects_an_edit_it_cannot_price(
    snapshot_dir: Path, package_dir: Path, tmp_path: Path
) -> None:
    args = ["shock", str(snapshot_dir), "--events", str(package_dir), "--out", str(tmp_path)]
    assert cli.main([*args, "--edit", "NOPE=1"]) == 2


def test_shock_can_run_a_subset_of_events(
    snapshot_dir: Path, package_dir: Path, tmp_path: Path
) -> None:
    cli.main(
        [
            "shock",
            str(snapshot_dir),
            "--events",
            str(package_dir),
            "--only",
            "evt-s",
            "--out",
            str(tmp_path),
        ]
    )
    results = pd.read_csv(tmp_path / "TEST_PORTFOLIO_2017-06-01" / "results.csv")
    assert set(results.event) == {"EVT-S"}


def test_selfcheck_prints_the_mismatch(
    snapshot_dir: Path, package_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["selfcheck", str(snapshot_dir), "--events", str(package_dir)]) == 0
    out = capsys.readouterr().out
    assert "6 event x instrument measurements" in out and "5 within 0.001" in out
    assert "EVT-W" in out


def test_snapshot_command_hands_its_arguments_to_the_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def fake_export(portfolio, day, out, db_config):
        seen.update(portfolio=portfolio, day=day, out=out, db_config=db_config)
        return out / "done"

    monkeypatch.setattr(cli, "export_snapshot", fake_export)
    cfg = tmp_path / "defaults.json"
    assert (
        cli.main(
            ["snapshot", "--date", "2026-10-13", "--out", str(tmp_path), "--db-config", str(cfg)]
        )
        == 0
    )
    assert seen["portfolio"] == "QT_CONSERVATIVE_PORTFOLIO" and str(seen["day"]) == "2026-10-13"
    assert seen["db_config"] == cfg
    assert str(tmp_path / "done") in capsys.readouterr().out


def _fresh_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from stress_test import engine

    monkeypatch.delitem(sys.modules, "tradengin", raising=False)
    monkeypatch.delenv(engine.ENGINE_ENV, raising=False)


def test_engine_info_reports_the_module_and_its_caveats(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from fake_tradengin import write_fake_engine

    _fresh_engine(monkeypatch)
    published = write_fake_engine(tmp_path / "pub", ids_and_fees=False)
    assert cli.main(["engine-info", "--engine", str(published)]) == 0
    out = capsys.readouterr().out
    assert "replay: available" in out and "bar_instrument_id: no" in out
    assert "roll's price gap taken out" in out


def test_replay_refuses_an_engine_that_cannot_run_a_config(
    snapshot_dir: Path, package_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fake_tradengin import write_fake_engine

    _fresh_engine(monkeypatch)
    old = write_fake_engine(tmp_path / "old", config_runs=False)
    assert cli.main(["engine-info", "--engine", str(old)]) == 3
    args = ["replay", str(snapshot_dir), "--events", str(package_dir), "--engine", str(old)]
    assert cli.main([*args, "--out", str(tmp_path / "out")]) == 3
    assert cli.main(["replay-check", str(snapshot_dir), "--engine", str(old)]) == 3


def test_replay_and_check_run_end_to_end_on_the_published_engine(
    snapshot_dir: Path,
    package_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fake_tradengin import write_fake_engine

    _fresh_engine(monkeypatch)
    fake = write_fake_engine(tmp_path / "pub", ids_and_fees=False)
    out = tmp_path / "out"
    common = ["--engine", str(fake), "--out", str(out), "--lead-in", "3"]
    code = cli.main(
        [
            "replay",
            str(snapshot_dir),
            "--events",
            str(package_dir),
            "--only",
            "EVT-W",
            "--edit",
            "ZT=0",
            "--jobs",
            "4",
            "--difference",
            "held",
            *common,
        ]
    )
    assert code == 0
    folder = out / "TEST_PORTFOLIO_2017-06-01"
    table = pd.read_csv(folder / "replay.csv")
    assert set(table.book) == {"A system = B proposal", "C final", "Edit"} and table.ok.all()
    assert set(table.strategy_start_book) == {"CL +1, ZN -1"}  # the stand-in strategy's book
    report = (folder / "replay.md").read_text()
    assert "Tier 2" in report and "held through the" in report
    bad = ["replay", str(snapshot_dir), "--events", str(package_dir), "--edit", "NOPE=1"]
    assert cli.main([*bad, *common]) == 2
    # The stand-in strategy holds CL +1, ZN -1 (and ZN -3 on a run's last day): never the
    # stored system book, and the check says by how much.
    assert cli.main(["replay-check", str(snapshot_dir), *common]) == 0
    printed = capsys.readouterr().out
    assert "equals the stored system book on 0 of 3 stored days" in printed
    assert (folder / "replay_check.csv").exists()


def test_output_goes_beside_the_tool_in_a_checkout_and_nowhere_near_a_repo_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(cli.OUT_ENV, raising=False)
    tool = Path(cli.__file__).resolve().parents[2]
    assert cli.default_out() == tool / "out"  # gitignored by the tool's own .gitignore
    assert "out/" in (tool / ".gitignore").read_text()
    monkeypatch.setenv(cli.OUT_ENV, str(tmp_path / "mine"))
    assert cli.default_out() == tmp_path / "mine"
    monkeypatch.delenv(cli.OUT_ENV)
    installed = tmp_path / "site-packages" / "stress_test" / "cli.py"
    monkeypatch.setattr(cli, "__file__", str(installed))
    assert cli.default_out() == Path.home() / ".stress-test" / "out"


def test_the_database_config_is_named_or_found_in_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from stress_test import snapshot

    monkeypatch.setenv(snapshot.DB_CONFIG_ENV, str(tmp_path / "db.json"))
    assert snapshot.default_db_config() == tmp_path / "db.json"
    monkeypatch.delenv(snapshot.DB_CONFIG_ENV)
    checkout = tmp_path / "repo"
    (checkout / snapshot.ENGINE_DB_CONFIG).parent.mkdir(parents=True)
    (checkout / snapshot.ENGINE_DB_CONFIG).write_text("{}")
    (checkout / "work").mkdir()
    monkeypatch.chdir(checkout / "work")
    monkeypatch.setattr(snapshot, "__file__", str(tmp_path / "elsewhere" / "snapshot.py"))
    assert snapshot.default_db_config() == checkout / snapshot.ENGINE_DB_CONFIG
    monkeypatch.chdir(tmp_path)
    assert snapshot.default_db_config() is None
    monkeypatch.setattr(cli, "default_db_config", lambda: None)
    assert cli.main(["snapshot", "--out", str(tmp_path / "out")]) == 2
    assert "pass --db-config" in capsys.readouterr().err
