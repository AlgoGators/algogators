"""Local interactive data explorer (Streamlit): ``uv run predict-ngin viz``.

Needs the optional ``viz`` extra (``uv sync --extra viz``). Pages:

* ``overview``    - data status and monthly volume per venue and category;
* ``market``      - search a market, price chart with volume, latest trades;
* ``calibration`` - price band vs share resolved YES for resolved markets;
* ``book``        - replay recorded order-book snapshots with trades overlaid.

``queries`` (SQL builders) and ``sources`` (time-bounded file sources) have no Streamlit
dependency and are unit-tested; the page modules hold only layout. Every page works with any
data folder and explains how to build a dataset that is missing. Nothing else in
``predict_ngin`` imports this package (see ``.importlinter``).
"""
