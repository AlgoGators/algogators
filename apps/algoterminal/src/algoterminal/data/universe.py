"""Named baskets of instruments ("universes") that can be referenced by name
anywhere in the tool instead of re-typing ticker lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from algoterminal.config import UNIVERSE_DIR, ensure_dirs
from algoterminal.data.provider import AssetClass


@dataclass
class Universe:
    name: str
    asset_class: AssetClass
    symbols: list[str]
    description: str = ""
    # Which DataProvider fetches this universe's symbols: "market" (yfinance/
    # Stooq, the default) or one of the alt-data source keys registered in
    # `algoterminal.data.altdata.registry` (e.g. "nasa-power", "fred"). Kept as
    # a plain string rather than an enum so new sources don't require a code
    # change here.
    source: str = "market"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "asset_class": self.asset_class.value,
            "symbols": self.symbols,
            "description": self.description,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Universe":
        return cls(
            name=data["name"],
            asset_class=AssetClass(data["asset_class"]),
            symbols=list(data["symbols"]),
            description=data.get("description", ""),
            source=data.get("source", "market"),
        )


# Shipped so the tool is useful out of the box without any setup.
#
# `source` is "market" (yfinance/Stooq) unless noted otherwise. The
# alt-data universes below use non-price sources registered in
# `algoterminal.data.altdata.registry` — NASA POWER (satellite weather/solar),
# USGS (seismic activity), FRED and World Bank (macroeconomic indicators),
# alternative.me (crypto sentiment), and Wikimedia (pageview attention) —
# each with its own symbol vocabulary (see that source's module for the
# full code -> description mapping).
_BUILTIN_UNIVERSES: list[Universe] = [
    # --- FX ---------------------------------------------------------------
    Universe(
        name="g10-fx",
        asset_class=AssetClass.FX,
        symbols=["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X", "USDCHF=X", "NZDUSD=X"],
        description="G10 currency pairs vs. USD",
    ),
    Universe(
        name="em-fx",
        asset_class=AssetClass.FX,
        symbols=["USDMXN=X", "USDBRL=X", "USDZAR=X", "USDTRY=X", "USDINR=X", "USDCNH=X", "USDKRW=X"],
        description="Emerging-market currency pairs vs. USD",
    ),
    # --- Equities -----------------------------------------------------------
    Universe(
        name="spx-tech",
        asset_class=AssetClass.EQUITY,
        symbols=["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "AVGO"],
        description="Large-cap S&P 500 technology names",
    ),
    Universe(
        name="us-sector-etfs",
        asset_class=AssetClass.EQUITY,
        symbols=["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"],
        description="SPDR Select Sector ETFs covering all 11 S&P 500 GICS sectors",
    ),
    Universe(
        name="semiconductors",
        asset_class=AssetClass.EQUITY,
        symbols=["SOXX", "SMH", "NVDA", "TSM", "ASML", "AMD", "INTC"],
        description="Semiconductor sector ETFs and leading chipmakers",
    ),
    Universe(
        name="credit-markets",
        asset_class=AssetClass.EQUITY,
        symbols=["LQD", "HYG", "JNK", "BND"],
        description="Investment-grade and high-yield corporate credit ETFs",
    ),
    Universe(
        name="precious-metals-etfs",
        asset_class=AssetClass.EQUITY,
        symbols=["GLD", "SLV", "PPLT", "PALL"],
        description="Physically-backed precious metals ETFs (gold, silver, platinum, palladium)",
    ),
    Universe(
        name="real-estate",
        asset_class=AssetClass.EQUITY,
        symbols=["VNQ", "IYR", "REM"],
        description="US real estate investment trust (REIT) ETFs",
    ),
    Universe(
        name="global-shipping-freight",
        asset_class=AssetClass.EQUITY,
        symbols=["BDRY", "ZIM", "MATX", "SBLK", "GOGL", "FRO", "STNG"],
        description=(
            "Dry-bulk freight rate ETF (BDRY) plus container and tanker shipping "
            "equities — the closest free, keyless proxy for container/freight markets"
        ),
    ),
    Universe(
        name="thematic-resources",
        asset_class=AssetClass.EQUITY,
        symbols=["DBA", "WOOD", "PHO", "IGF"],
        description="Agriculture, timber, water and global infrastructure thematic ETFs",
    ),
    Universe(
        name="developed-intl-equity",
        asset_class=AssetClass.EQUITY,
        symbols=["EFA", "VEA", "EWJ", "EWG", "EWU"],
        description="Developed international equity market ETFs, ex-US",
    ),
    Universe(
        name="emerging-markets-equity",
        asset_class=AssetClass.EQUITY,
        symbols=["EEM", "VWO", "FXI", "EWZ", "INDA"],
        description="Emerging-market equity ETFs by broad index and by country",
    ),
    # --- Indices / volatility / rates (not directly tradable tickers) -----
    Universe(
        name="global-equity-indices",
        asset_class=AssetClass.CUSTOM,
        symbols=["^GSPC", "^DJI", "^IXIC", "^RUT", "^FTSE", "^GDAXI", "^FCHI", "^N225", "^HSI", "^STOXX50E"],
        description="Major global equity market benchmark indices",
    ),
    Universe(
        name="volatility",
        asset_class=AssetClass.CUSTOM,
        symbols=["^VIX", "VXX", "UVXY", "SVXY"],
        description="CBOE volatility index and VIX-linked exchange-traded products",
    ),
    Universe(
        name="us-rates-bonds",
        asset_class=AssetClass.CUSTOM,
        symbols=["TLT", "IEF", "SHY", "TIP", "^TNX", "^TYX", "^IRX"],
        description="US Treasury ETFs across the curve plus yield benchmarks",
    ),
    # --- Futures ------------------------------------------------------------
    Universe(
        name="us-rates-futures",
        asset_class=AssetClass.FUTURE,
        symbols=["ZN=F", "ZB=F", "ZT=F"],
        description="US Treasury futures (10Y, 30Y, 2Y)",
    ),
    Universe(
        name="energy-futures",
        asset_class=AssetClass.FUTURE,
        symbols=["CL=F", "BZ=F", "NG=F", "RB=F", "HO=F"],
        description="Crude oil (WTI, Brent), natural gas and refined product futures",
    ),
    Universe(
        name="metals-futures",
        asset_class=AssetClass.FUTURE,
        symbols=["GC=F", "SI=F", "HG=F", "PL=F", "PA=F"],
        description="Precious and industrial metals futures",
    ),
    Universe(
        name="agriculture-futures",
        asset_class=AssetClass.FUTURE,
        symbols=["ZC=F", "ZS=F", "ZW=F", "KC=F", "CT=F", "SB=F", "CC=F"],
        description="Grain, soft commodity and agricultural futures",
    ),
    # --- Crypto ---------------------------------------------------------
    Universe(
        name="crypto-majors",
        asset_class=AssetClass.CRYPTO,
        symbols=["BTC-USD", "ETH-USD", "SOL-USD"],
        description="Major crypto assets vs. USD",
    ),
    Universe(
        name="crypto-alts",
        asset_class=AssetClass.CRYPTO,
        symbols=["BNB-USD", "XRP-USD", "ADA-USD", "DOGE-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "DOT-USD"],
        description="Large-cap alternative cryptoassets vs. USD",
    ),
    # --- Alt-data: NASA POWER (satellite weather/solar) --------------------
    Universe(
        name="nasa-power-global-cities",
        asset_class=AssetClass.ALT_DATA,
        source="nasa-power",
        symbols=[
            "NYC", "LONDON", "TOKYO", "SINGAPORE", "SHANGHAI", "MUMBAI",
            "SAOPAULO", "SYDNEY", "DUBAI", "HOUSTON", "ROTTERDAM",
        ],
        description=(
            "Daily satellite-derived temperature, solar irradiance, wind and precipitation "
            "for major financial and trade-hub cities, via NASA's POWER API"
        ),
    ),
    Universe(
        name="nasa-power-ag-regions",
        asset_class=AssetClass.ALT_DATA,
        source="nasa-power",
        symbols=["IOWA", "MATOGROSSO", "PUNJAB", "PERTH"],
        description=(
            "Daily weather and solar data for major agricultural and mining regions "
            "relevant to commodity markets, via NASA's POWER API"
        ),
    ),
    # --- Alt-data: USGS earthquakes -----------------------------------------
    Universe(
        name="seismic-activity",
        asset_class=AssetClass.ALT_DATA,
        source="usgs-earthquake",
        symbols=["GLOBAL", "CALIFORNIA", "JAPAN", "INDONESIA", "CHILE", "ALASKA", "TURKEY"],
        description="Daily M4.5+ earthquake counts and peak magnitude by seismically active region (USGS)",
    ),
    # --- Alt-data: FRED macro -----------------------------------------------
    Universe(
        name="us-macro-indicators",
        asset_class=AssetClass.ALT_DATA,
        source="fred",
        symbols=[
            "UNRATE", "CPIAUCSL", "DFF", "DGS10", "DGS2", "GDP",
            "PAYEMS", "INDPRO", "M2SL", "UMCSENT", "HOUST", "VIXCLS",
        ],
        description="Core US macroeconomic and monetary policy time series, from FRED",
    ),
    # --- Alt-data: World Bank -------------------------------------------
    Universe(
        name="global-gdp-growth",
        asset_class=AssetClass.ALT_DATA,
        source="world-bank",
        symbols=[
            "USA:NY.GDP.MKTP.KD.ZG", "CHN:NY.GDP.MKTP.KD.ZG", "JPN:NY.GDP.MKTP.KD.ZG",
            "DEU:NY.GDP.MKTP.KD.ZG", "IND:NY.GDP.MKTP.KD.ZG", "BRA:NY.GDP.MKTP.KD.ZG",
        ],
        description="Annual real GDP growth rate by major economy (World Bank)",
    ),
    Universe(
        name="global-inflation",
        asset_class=AssetClass.ALT_DATA,
        source="world-bank",
        symbols=[
            "USA:FP.CPI.TOTL.ZG", "CHN:FP.CPI.TOTL.ZG", "GBR:FP.CPI.TOTL.ZG",
            "IND:FP.CPI.TOTL.ZG", "BRA:FP.CPI.TOTL.ZG", "ZAF:FP.CPI.TOTL.ZG",
        ],
        description="Annual consumer price inflation by major economy (World Bank)",
    ),
    # --- Alt-data: alternative.me Crypto Fear & Greed -----------------------
    Universe(
        name="crypto-sentiment",
        asset_class=AssetClass.ALT_DATA,
        source="fear-greed",
        symbols=["CRYPTO"],
        description="Daily Crypto Fear & Greed Index (0=Extreme Fear, 100=Extreme Greed)",
    ),
    # --- Alt-data: Wikipedia pageviews (public-attention proxy) -------------
    Universe(
        name="wiki-attention-meme-stocks",
        asset_class=AssetClass.ALT_DATA,
        source="wiki-pageviews",
        symbols=["GAMESTOP", "AMC", "TESLA", "NVIDIA", "BITCOIN"],
        description="Daily Wikipedia pageviews for high-retail-attention companies/assets",
    ),
    Universe(
        name="wiki-attention-macro-topics",
        asset_class=AssetClass.ALT_DATA,
        source="wiki-pageviews",
        symbols=["RECESSION", "INFLATION", "FEDERAL_RESERVE", "ARTIFICIAL_INTELLIGENCE"],
        description="Daily Wikipedia pageviews for macro/thematic topics, a public-attention sentiment proxy",
    ),
    # --- More equities --------------------------------------------------
    Universe(
        name="airlines",
        asset_class=AssetClass.EQUITY,
        symbols=["DAL", "UAL", "AAL", "LUV", "JBLU"],
        description="Major US airline equities",
    ),
    Universe(
        name="homebuilders",
        asset_class=AssetClass.EQUITY,
        symbols=["ITB", "XHB", "DHI", "LEN", "PHM"],
        description="Homebuilder ETFs and major US homebuilders",
    ),
    Universe(
        name="biotech",
        asset_class=AssetClass.EQUITY,
        symbols=["XBI", "IBB", "ARKG"],
        description="Biotechnology sector ETFs",
    ),
    Universe(
        name="clean-energy",
        asset_class=AssetClass.EQUITY,
        symbols=["ICLN", "TAN", "FAN", "PBW"],
        description="Clean energy and renewables ETFs (solar, wind, broad clean-tech)",
    ),
    Universe(
        name="cybersecurity",
        asset_class=AssetClass.EQUITY,
        symbols=["CIBR", "HACK", "BUG"],
        description="Cybersecurity sector ETFs",
    ),
    Universe(
        name="defense-aerospace",
        asset_class=AssetClass.EQUITY,
        symbols=["ITA", "XAR", "LMT", "RTX", "NOC"],
        description="Aerospace & defense ETFs and prime contractors",
    ),
    Universe(
        name="regional-banks",
        asset_class=AssetClass.EQUITY,
        symbols=["KRE", "KBE"],
        description="Regional and community bank ETFs",
    ),
    # --- More FX --------------------------------------------------------
    Universe(
        name="asia-fx",
        asset_class=AssetClass.FX,
        symbols=["USDSGD=X", "USDHKD=X", "USDTHB=X", "USDIDR=X", "USDPHP=X", "USDMYR=X"],
        description="Asian currency pairs vs. USD",
    ),
    # --- More crypto ------------------------------------------------------
    Universe(
        name="crypto-defi",
        asset_class=AssetClass.CRYPTO,
        symbols=["UNI-USD", "AAVE-USD", "MKR-USD", "CRV-USD", "LDO-USD"],
        description="Decentralized finance (DeFi) protocol tokens vs. USD",
    ),
]


class UniverseStore:
    """Reads/writes universe YAML files under ~/.algoterminal/universes."""

    def __init__(self) -> None:
        ensure_dirs()
        self._seed_builtins()

    def _seed_builtins(self) -> None:
        for u in _BUILTIN_UNIVERSES:
            path = self._path(u.name)
            if not path.exists():
                self.save(u)

    @staticmethod
    def _path(name: str):
        return UNIVERSE_DIR / f"{name}.yaml"

    def save(self, universe: Universe) -> None:
        with open(self._path(universe.name), "w", encoding="utf-8") as f:
            yaml.safe_dump(universe.to_dict(), f, sort_keys=False)

    def load(self, name: str) -> Universe:
        path = self._path(name)
        if not path.exists():
            raise KeyError(f"no such universe: {name!r}")
        with open(path, encoding="utf-8") as f:
            return Universe.from_dict(yaml.safe_load(f))

    def delete(self, name: str) -> None:
        path = self._path(name)
        if path.exists():
            path.unlink()

    def list(self) -> list[Universe]:
        return [self.load(p.stem) for p in sorted(UNIVERSE_DIR.glob("*.yaml"))]

    def resolve(self, name_or_symbols: str) -> list[str]:
        """Resolve a universe name to its symbols, or treat the input as a
        comma-separated symbol list if it isn't a known universe.
        """
        try:
            return self.load(name_or_symbols).symbols
        except KeyError:
            return [s.strip() for s in name_or_symbols.split(",") if s.strip()]
