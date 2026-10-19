"""wx workstream shared constants: Kalshi daily temperature series -> settlement station.

Station codes come from the rule text "(CLIxxx)" (data/vendor/wx/series_rules.csv) and, for the
legacy pre-KX series, from their NWS CLI product links. tz = the station's local STANDARD time
offset (CLI climate day = local standard midnight to midnight, also during DST).
"""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "wx"
OUT.mkdir(parents=True, exist_ok=True)

# CLI code -> (ICAO, IEM ASOS id, standard-time UTC offset hours, AFOS pil)
STATIONS = {
    "NYC": ("KNYC", "NYC", -5, "CLINYC"), "MDW": ("KMDW", "MDW", -6, "CLIMDW"),
    "AUS": ("KAUS", "AUS", -6, "CLIAUS"), "MIA": ("KMIA", "MIA", -5, "CLIMIA"),
    "LAX": ("KLAX", "LAX", -8, "CLILAX"), "DEN": ("KDEN", "DEN", -7, "CLIDEN"),
    "PHL": ("KPHL", "PHL", -5, "CLIPHL"), "EWR": ("KEWR", "EWR", -5, "CLIEWR"),
    "LAS": ("KLAS", "LAS", -8, "CLILAS"), "SEA": ("KSEA", "SEA", -8, "CLISEA"),
    "PHX": ("KPHX", "PHX", -7, "CLIPHX"), "MSY": ("KMSY", "MSY", -6, "CLIMSY"),
    "DFW": ("KDFW", "DFW", -6, "CLIDFW"), "SFO": ("KSFO", "SFO", -8, "CLISFO"),
    "MSP": ("KMSP", "MSP", -6, "CLIMSP"), "OKC": ("KOKC", "OKC", -6, "CLIOKC"),
    "HOU": ("KHOU", "HOU", -6, "CLIHOU"), "TTN": ("KTTN", "TTN", -5, "CLITTN"),
    "ATL": ("KATL", "ATL", -5, "CLIATL"), "SAT": ("KSAT", "SAT", -6, "CLISAT"),
    "BOS": ("KBOS", "BOS", -5, "CLIBOS"), "SDF": ("KSDF", "SDF", -5, "CLISDF"),
    "SAN": ("KSAN", "SAN", -8, "CLISAN"), "DCA": ("KDCA", "DCA", -5, "CLIDCA"),
}

# series -> (CLI code, measure)
SERIES = {
    "KXHIGHNY": ("NYC", "high"), "HIGHNY": ("NYC", "high"), "KXHIGHCHI": ("MDW", "high"), "HIGHCHI": ("MDW", "high"),
    "KXHIGHAUS": ("AUS", "high"), "HIGHAUS": ("AUS", "high"), "KXHIGHMIA": ("MIA", "high"), "HIGHMIA": ("MIA", "high"),
    "KXHIGHLAX": ("LAX", "high"), "KXHIGHDEN": ("DEN", "high"), "KXHIGHPHIL": ("PHL", "high"),
    "KXHIGHTEWR": ("EWR", "high"), "KXHIGHTLV": ("LAS", "high"), "KXHIGHTSEA": ("SEA", "high"),
    "KXHIGHTPHX": ("PHX", "high"), "KXHIGHTNOLA": ("MSY", "high"), "KXHIGHTDAL": ("DFW", "high"),
    "KXHIGHTSFO": ("SFO", "high"), "KXHIGHTMIN": ("MSP", "high"), "KXHIGHTOKC": ("OKC", "high"),
    "KXHIGHTHOU": ("HOU", "high"), "KXHIGHTTTN": ("TTN", "high"), "KXHIGHTATL": ("ATL", "high"),
    "KXHIGHTSATX": ("SAT", "high"), "KXHIGHTBOS": ("BOS", "high"), "KXHIGHTSDF": ("SDF", "high"),
    "KXHIGHTSAN": ("SAN", "high"), "KXHIGHTDC": ("DCA", "high"), "KXHIGHHOU": ("HOU", "high"),
    "KXLOWTNYC": ("NYC", "low"), "KXLOWTCHI": ("MDW", "low"), "KXLOWTAUS": ("AUS", "low"),
    "KXLOWTMIA": ("MIA", "low"), "KXLOWTLAX": ("LAX", "low"), "KXLOWTDEN": ("DEN", "low"),
    "KXLOWTPHIL": ("PHL", "low"), "KXLOWTEWR": ("EWR", "low"), "KXLOWTLV": ("LAS", "low"),
    "KXLOWTSEA": ("SEA", "low"), "KXLOWTPHX": ("PHX", "low"), "KXLOWTNOLA": ("MSY", "low"),
    "KXLOWTDAL": ("DFW", "low"), "KXLOWTSFO": ("SFO", "low"), "KXLOWTMIN": ("MSP", "low"),
    "KXLOWTOKC": ("OKC", "low"), "KXLOWTHOU": ("HOU", "low"), "KXLOWTTTN": ("TTN", "low"),
    "KXLOWTATL": ("ATL", "low"), "KXLOWTSATX": ("SAT", "low"), "KXLOWTBOS": ("BOS", "low"),
    "KXLOWTSDF": ("SDF", "low"), "KXLOWTSAN": ("SAN", "low"), "KXLOWTDC": ("DCA", "low"),
}

# Pre-registered splits (fixed 2026-10-14 before looking at any test-window result)
TRAIN_END = "2025-06-30"      # train: climate dates <= this
TEST_START = "2025-07-01"     # test 1: 2025-07-01 -> 2026-02-11 (local tape, maker + taker)
OOS2_START = "2026-08-15"     # test 2: 2026-08-15 -> 2026-10-12 (fin_E 1-min candles, taker only, TWC era)
