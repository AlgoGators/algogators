"""wx: Open-Meteo Previous-Runs API (free, no key): archived model forecasts by lead day, hourly 2 m temperature.

temperature_2m_previous_dayN at valid hour H comes from a run with lead 24N..24N+23 h, i.e. initialised
<= H - 24N h. For climate day D (local standard) the daily max over D's hours at previous_day1 uses runs
initialised <= D 23:00 LST - 24 h; with ~6 h model latency it is available by ~D 05:00 LST.
previous_day2 is available by ~D-1 05:00 LST. (Point-in-time at day granularity.)
Models: gfs_seamless, ecmwf_ifs025, ncep_nbm_conus. Output data/vendor/wx/om/<model>_<code>.parquet
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, STATIONS  # noqa: E402

COORD = {"NYC": (40.7789, -73.9692), "MDW": (41.7861, -87.7522), "AUS": (30.1945, -97.6699), "MIA": (25.7881, -80.3169),
         "LAX": (33.9382, -118.3866), "DEN": (39.8466, -104.6562), "PHL": (39.8733, -75.2268), "EWR": (40.6827, -74.1693),
         "LAS": (36.0720, -115.1634), "SEA": (47.4447, -122.3144), "PHX": (33.4277, -112.0038), "MSY": (29.9934, -90.2580),
         "DFW": (32.8975, -97.0219), "SFO": (37.6197, -122.3647), "MSP": (44.8831, -93.2289), "OKC": (35.3889, -97.6006),
         "HOU": (29.6375, -95.2825), "TTN": (40.2767, -74.8156), "ATL": (33.6301, -84.4418), "SAT": (29.5443, -98.4839),
         "BOS": (42.3606, -71.0097), "SDF": (38.1811, -85.7392), "SAN": (32.7336, -117.1831), "DCA": (38.8483, -77.0342)}
MODELS = ["gfs_seamless", "ecmwf_ifs025", "ncep_nbm_conus"]
VARS = ["temperature_2m_previous_day1", "temperature_2m_previous_day2"]


def main(start="2024-10-01", end="2026-10-13"):
    od = OUT / "om"; od.mkdir(parents=True, exist_ok=True)
    for mdl in MODELS:
        for c, (la, lo) in COORD.items():
            p = od / f"{mdl}_{c}.parquet"
            if p.exists():
                continue
            for i in range(6):
                r = requests.get("https://previous-runs-api.open-meteo.com/v1/forecast", timeout=300, params=dict(
                    latitude=la, longitude=lo, hourly=",".join(VARS), temperature_unit="fahrenheit", timezone="GMT",
                    start_date=start, end_date=end, models=mdl))
                if r.status_code == 200:
                    break
                print("HTTP", r.status_code, r.text[:200], flush=True); time.sleep(30 * (i + 1))
            h = r.json()["hourly"]
            d = pd.DataFrame(h); d["time"] = pd.to_datetime(d.time)
            d.to_parquet(p, index=False)
            print(mdl, c, len(d), d[VARS].notna().mean().round(3).to_dict(), flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
