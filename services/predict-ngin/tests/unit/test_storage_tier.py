"""scripts/data/storage_tier.py: no cold drive -> nothing moves; with one, demote/promote round-trips via a link."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("storage_tier", ROOT / "scripts/data/storage_tier.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "old" / "sub").mkdir(parents=True)
    (data / "old" / "a.parquet").write_bytes(b"x" * 100)
    (data / "old" / "sub" / "b.csv").write_text("k,v\n1,2\n")
    monkeypatch.setenv("PREDICT_NGIN_DATA", str(data))
    monkeypatch.delenv("PREDICT_NGIN_COLD", raising=False)
    return tmp_path, data


def test_no_cold_drive_keeps_data_in_place(env, monkeypatch):
    tmp, data = env
    st = _load()
    monkeypatch.setattr(st, "cold_root", lambda: None)  # ignore any machine-local config
    assert st.main(["demote", "old"]) == 0
    assert (data / "old" / "a.parquet").is_file() and not st.is_link(data / "old")
    assert not (data / "storage_tiers.json").exists()


def test_demote_promote_round_trip(env, monkeypatch):
    tmp, data = env
    cold = tmp / "cold"
    monkeypatch.setenv("PREDICT_NGIN_COLD", str(cold))
    st = _load()
    assert st.main(["demote", "data/old"]) == 0          # leading data/ accepted
    assert st.is_link(data / "old")
    assert (cold / "data" / "old" / "a.parquet").read_bytes() == b"x" * 100
    assert (data / "old" / "sub" / "b.csv").read_text() == "k,v\n1,2\n"   # still readable at the old path
    assert st.load_manifest()["old"]["tier"] == "cold"

    assert st.main(["demote", "old"]) == 0               # already demoted: skipped, no error
    assert st.main(["promote", "old"]) == 0
    assert not st.is_link(data / "old") and (data / "old" / "a.parquet").is_file()
    assert not (cold / "data" / "old").exists()
    assert st.load_manifest()["old"]["tier"] == "hot"
