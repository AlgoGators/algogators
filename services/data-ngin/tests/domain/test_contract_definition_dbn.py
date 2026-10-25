"""extract_definitions_from_dbn_bytes / _tar on a small DBN file of real definition records
(tests/fixtures/definitions_reused_id.dbn, built from the 2026-10-03..10-24 GLBX.MDP3 fetch):
GCZ6 (id 41271, expiring 2026-12-29), and three records on the same id: a class S record with the
same symbol expiring in November, a user-defined record with the same symbol expiring in October,
and an HGZ6 outright expiring 2026-12-28."""

import io
import tarfile
from datetime import date
from pathlib import Path

import zstandard
from data_ngin.domain.contract_definition import (
    build_definition_map,
    extract_definitions_from_dbn_bytes,
    extract_definitions_from_tar,
    lookup_expiration,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "definitions_reused_id.dbn"
BAR = date(2026, 10, 21)


def _check(index):
    assert set(index) == {("41271", "GCZ6"), ("41271", "HGZ6")}
    # the class S and the user-defined records must not join GCZ6's dates: with either kept,
    # the nearest month would give an October or November date
    assert [r.expiration for r in index[("41271", "GCZ6")]] == [date(2026, 12, 29)]
    assert lookup_expiration(index, "41271", "GCZ6", BAR) == date(2026, 12, 29)
    assert lookup_expiration(index, "41271", "HGZ6", BAR) == date(2026, 12, 28)


def test_dbn_bytes_keep_only_outright_futures_keyed_on_id_and_symbol():
    _check(extract_definitions_from_dbn_bytes(FIXTURE.read_bytes()))


def test_tar_archive_reads_its_definition_members(tmp_path):
    tar_path = tmp_path / "GLBX-TEST.tar"
    compressed = zstandard.ZstdCompressor().compress(FIXTURE.read_bytes())
    with tarfile.open(tar_path, "w") as tar:
        for name, data in (
            ("GLBX-TEST/manifest.json", b"{}"),
            ("GLBX-TEST/glbx-mdp3-20261001-20261024.definition.dbn.zst", compressed),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    _check(extract_definitions_from_tar(str(tar_path)))
    _check(build_definition_map([str(tar_path)]))
