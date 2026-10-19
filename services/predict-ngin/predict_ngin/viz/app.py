"""Streamlit entry point of the data explorer.

    uv run predict-ngin viz [--port 8501]
    # or directly:
    uv run streamlit run predict_ngin/viz/app.py

Streamlit runs this file as a script, so imports are absolute. The sidebar picks a page; each
page lives in its own module with a ``render()`` function (docstrings there explain what it
shows).
"""

from __future__ import annotations

import streamlit as st

from predict_ngin.shared.paths import data_root
from predict_ngin.viz import _ui, book, calibration, market, overview, paper_monitor

PAGES = {
    "Overview": overview.render,
    "Market explorer": market.render,
    "Calibration": calibration.render,
    "Order book replay": book.render,
    "Paper trading": paper_monitor.render,
}


def main() -> None:
    st.set_page_config(page_title="predict-ngin data explorer", layout="wide")
    st.sidebar.title("predict-ngin")
    page = st.sidebar.radio("Page", list(PAGES), key="page")
    st.sidebar.caption(f"Data folder: `{data_root()}`")
    _ui.reload_button()
    st.sidebar.caption(
        "Times are UTC. Queries are cached; *Reload data* picks up newly built lake months."
    )
    PAGES[page]()


main()
