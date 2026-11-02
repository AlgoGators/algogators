"""Tests for Universe and UniverseStore (YAML persistence under tmp home)."""

from __future__ import annotations

import pytest
from algoterminal.data.provider import AssetClass
from algoterminal.data.universe import _BUILTIN_UNIVERSES, Universe, UniverseStore


class TestUniverseModel:
    def test_roundtrip(self):
        u = Universe(
            name="my-basket",
            asset_class=AssetClass.CRYPTO,
            symbols=["BTC-USD", "ETH-USD"],
            description="demo",
            source="market",
        )
        again = Universe.from_dict(u.to_dict())
        assert again == u

    def test_from_dict_defaults(self):
        u = Universe.from_dict(
            {"name": "x", "asset_class": "equity", "symbols": ["AAA"]},
        )
        assert u.description == ""
        assert u.source == "market"
        assert u.asset_class is AssetClass.EQUITY


class TestUniverseStore:
    def test_seeds_builtins_on_first_use(self):
        store = UniverseStore()
        names = {u.name for u in store.list()}
        assert {"g10-fx", "spx-tech", "crypto-majors"} <= names
        assert len(names) == len(_BUILTIN_UNIVERSES)

    def test_save_load_delete(self):
        store = UniverseStore()
        u = Universe(name="scratch", asset_class=AssetClass.FX, symbols=["EURUSD=X"])
        store.save(u)
        assert store.load("scratch") == u

        store.delete("scratch")
        with pytest.raises(KeyError):
            store.load("scratch")
        store.delete("scratch")  # deleting a missing universe is a no-op

    def test_seeding_does_not_clobber_user_edits(self):
        store = UniverseStore()
        edited = store.load("g10-fx")
        edited.symbols = ["EURUSD=X"]
        store.save(edited)

        again = UniverseStore()  # re-seeding runs here
        assert again.load("g10-fx").symbols == ["EURUSD=X"]

    def test_resolve_universe_name(self):
        store = UniverseStore()
        assert store.resolve("g10-fx") == store.load("g10-fx").symbols

    def test_resolve_symbol_list(self):
        store = UniverseStore()
        assert store.resolve("AAA, BBB ,,CCC") == ["AAA", "BBB", "CCC"]

    def test_builtin_alt_data_universes_have_sources(self):
        store = UniverseStore()
        by_name = {u.name: u for u in store.list()}
        assert by_name["us-macro-indicators"].source == "fred"
        assert by_name["seismic-activity"].source == "usgs-earthquake"
        assert by_name["crypto-sentiment"].asset_class is AssetClass.ALT_DATA
