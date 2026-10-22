import json
import sqlite3
from pathlib import Path

import pandas as pd

from trading.data_modules.data import load_manifold_data, load_markets


def _create_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE manifold_bets (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            contract_id TEXT,
            amount REAL,
            outcome TEXT,
            prob_before REAL,
            prob_after REAL,
            created_time INTEGER,
            datetime TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE manifold_markets (
            id TEXT PRIMARY KEY,
            data TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def test_load_manifold_data_from_db(tmp_path: Path) -> None:
    db_path = tmp_path / "pm.db"
    _create_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    t1 = int(pd.Timestamp("2026-01-01").timestamp() * 1000)
    cur.execute(
        """
        INSERT INTO manifold_bets
        (id, user_id, contract_id, amount, outcome, prob_before, prob_after, created_time, datetime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("b1", "u1", "m1", 10, "YES", 0.4, 0.5, t1, "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    df = load_manifold_data(
        data_dir=str(tmp_path / "missing"),
        db_path=str(db_path),
        auto_fetch=False,
        use_db=True,
    )

    assert len(df) == 1
    assert df.loc[0, "userId"] == "u1"


def test_load_markets_from_db(tmp_path: Path) -> None:
    db_path = tmp_path / "pm2.db"
    _create_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    market = {"id": "m1", "question": "Test market"}
    cur.execute(
        "INSERT INTO manifold_markets (id, data) VALUES (?, ?)",
        ("m1", json.dumps(market)),
    )
    conn.commit()
    conn.close()

    df = load_markets(
        data_dir=str(tmp_path / "missing"),
        db_path=str(db_path),
        use_db=True,
    )

    assert len(df) == 1
    assert df.loc[0, "id"] == "m1"
