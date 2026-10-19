"""
Database storage for prediction market data.

Converts JSON files to SQLite for structured querying.
"""

import json
import sqlite3
import glob
import hashlib
import random
import statistics
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import pandas as pd
try:
    from src.timeconv import to_epoch, to_epoch_seconds
except ImportError:  # src/ itself on sys.path
    from timeconv import to_epoch, to_epoch_seconds


DEFAULT_DB_PATH = "data/prediction_markets.db"


class PredictionMarketDB:
    """SQLite database for prediction market data."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self._init_schema()

    def _init_schema(self):
        """Initialize database schema."""
        cursor = self.conn.cursor()

        # Polymarket markets table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS polymarket_markets (
                id TEXT PRIMARY KEY,
                slug TEXT,
                question TEXT,
                volume REAL,
                volume_24hr REAL,
                liquidity REAL,
                end_date TEXT,
                outcomes TEXT,
                outcome_prices TEXT,
                token_id_yes TEXT,
                token_id_no TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Polymarket price history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS polymarket_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                outcome TEXT,
                timestamp INTEGER,
                price REAL,
                datetime TEXT,
                FOREIGN KEY (market_id) REFERENCES polymarket_markets(id)
            )
        """)

        # Create indexes for fast querying
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_prices_market_id
            ON polymarket_prices(market_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_prices_timestamp
            ON polymarket_prices(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_prices_market_outcome
            ON polymarket_prices(market_id, outcome)
        """)

        # Polymarket trades table (processed trades.csv)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS polymarket_trades (
                trade_id TEXT PRIMARY KEY,
                timestamp TEXT,
                timestamp_unix INTEGER,
                market_id TEXT,
                maker TEXT,
                taker TEXT,
                nonusdc_side TEXT,
                maker_direction TEXT,
                taker_direction TEXT,
                price REAL,
                usd_amount REAL,
                token_amount REAL,
                transaction_hash TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_market_id
            ON polymarket_trades(market_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp
            ON polymarket_trades(timestamp_unix)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_tx_hash
            ON polymarket_trades(transaction_hash)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_maker
            ON polymarket_trades(maker)
        """)

        # Manifold bets table (for future use)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS manifold_markets (
                id TEXT PRIMARY KEY,
                data TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_manifold_markets_id
            ON manifold_markets(id)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS manifold_bets (
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
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bets_user_id
            ON manifold_bets(user_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bets_contract_id
            ON manifold_bets(contract_id)
        """)

        self.conn.commit()

    def import_polymarket_trades_csv(
        self,
        trades_path: str = "data/poly_data/processed/trades.csv",
        chunk_size: int = 200000,
    ) -> Dict[str, int]:
        """
        Import processed Polymarket trades CSV into database.

        Args:
            trades_path: Path to processed trades.csv
            chunk_size: Rows to read per chunk

        Returns:
            Dict with counts of imported records
        """
        trades_file = Path(trades_path)
        if not trades_file.exists():
            print(f"Trades file not found: {trades_file}")
            return {"trades": 0, "skipped": 0}

        required_cols = [
            "timestamp",
            "market_id",
            "maker",
            "taker",
            "nonusdc_side",
            "maker_direction",
            "taker_direction",
            "price",
            "usd_amount",
            "token_amount",
            "transactionHash",
        ]

        print(f"Importing Polymarket trades from {trades_file}...")

        cursor = self.conn.cursor()
        total_imported = 0
        total_skipped = 0
        chunk_idx = 0

        for chunk in pd.read_csv(trades_file, chunksize=chunk_size):
            chunk_idx += 1
            missing = [c for c in required_cols if c not in chunk.columns]
            if missing:
                raise ValueError(f"Missing columns in trades CSV: {missing}")

            dt = pd.to_datetime(chunk["timestamp"], errors="coerce", utc=True)
            ts_unix = to_epoch_seconds(dt).astype("Int64")
            ts_unix = ts_unix.where(dt.notna(), None)

            key_cols = [
                "transactionHash",
                "market_id",
                "maker",
                "taker",
                "timestamp",
                "usd_amount",
                "token_amount",
            ]
            key_data = chunk[key_cols].fillna("").astype(str).agg("|".join, axis=1)
            trade_ids = key_data.map(lambda s: hashlib.md5(s.encode("utf-8")).hexdigest())

            before = self.conn.total_changes
            rows = list(zip(
                trade_ids,
                chunk["timestamp"].astype(str),
                ts_unix,
                chunk["market_id"].astype(str),
                chunk["maker"].astype(str),
                chunk["taker"].astype(str),
                chunk["nonusdc_side"].astype(str),
                chunk["maker_direction"].astype(str),
                chunk["taker_direction"].astype(str),
                pd.to_numeric(chunk["price"], errors="coerce"),
                pd.to_numeric(chunk["usd_amount"], errors="coerce"),
                pd.to_numeric(chunk["token_amount"], errors="coerce"),
                chunk["transactionHash"].astype(str),
            ))

            cursor.executemany("""
                INSERT OR IGNORE INTO polymarket_trades
                (trade_id, timestamp, timestamp_unix, market_id, maker, taker,
                 nonusdc_side, maker_direction, taker_direction, price,
                 usd_amount, token_amount, transaction_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            self.conn.commit()
            inserted = self.conn.total_changes - before
            total_imported += inserted
            total_skipped += len(rows) - inserted

            if chunk_idx % 10 == 0:
                print(
                    f"  Imported {total_imported:,} trades "
                    f"(skipped {total_skipped:,})..."
                )

        print("\nImport complete:")
        print(f"  Trades: {total_imported:,}")
        print(f"  Skipped (duplicates): {total_skipped:,}")

        return {
            "trades": total_imported,
            "skipped": total_skipped,
        }

    def import_polymarket_trades_parquet(
        self,
        trades_dir: str = "data/polymarket/trades",
        chunk_size: int = 200000,
    ) -> Dict[str, int]:
        """
        Import processed Polymarket trades parquet files into database.

        Args:
            trades_dir: Directory with trades_*.parquet files
            chunk_size: Rows to insert per batch
        """
        trades_path = Path(trades_dir)
        files = sorted(trades_path.glob("trades_*.parquet"))
        if not files:
            print(f"No parquet trades found in {trades_path}")
            return {"trades": 0, "skipped": 0}

        required_cols = [
            "timestamp",
            "market_id",
            "maker",
            "taker",
            "nonusdc_side",
            "maker_direction",
            "taker_direction",
            "price",
            "usd_amount",
            "token_amount",
            "transactionHash",
        ]

        print(f"Importing Polymarket trades from {trades_path}...")

        cursor = self.conn.cursor()
        total_imported = 0
        total_skipped = 0

        for filepath in files:
            try:
                df = pd.read_parquet(filepath)
            except Exception as e:
                print(f"  Error reading {filepath.name}: {e}")
                continue

            missing = [c for c in required_cols if c not in df.columns]
            if missing:
                raise ValueError(f"Missing columns in trades parquet: {missing}")

            # Ensure timestamps are parsed
            dt = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
            ts_unix = to_epoch_seconds(dt).astype("Int64")
            ts_unix = ts_unix.where(dt.notna(), None)

            key_cols = [
                "transactionHash",
                "market_id",
                "maker",
                "taker",
                "timestamp",
                "usd_amount",
                "token_amount",
            ]
            key_data = df[key_cols].fillna("").astype(str).agg("|".join, axis=1)
            trade_ids = key_data.map(lambda s: hashlib.md5(s.encode("utf-8")).hexdigest())

            rows = list(zip(
                trade_ids,
                df["timestamp"].astype(str),
                ts_unix,
                df["market_id"].astype(str),
                df["maker"].astype(str),
                df["taker"].astype(str),
                df["nonusdc_side"].astype(str),
                df["maker_direction"].astype(str),
                df["taker_direction"].astype(str),
                pd.to_numeric(df["price"], errors="coerce"),
                pd.to_numeric(df["usd_amount"], errors="coerce"),
                pd.to_numeric(df["token_amount"], errors="coerce"),
                df["transactionHash"].astype(str),
            ))

            for i in range(0, len(rows), chunk_size):
                batch = rows[i:i + chunk_size]
                before = self.conn.total_changes
                cursor.executemany("""
                    INSERT OR IGNORE INTO polymarket_trades
                    (trade_id, timestamp, timestamp_unix, market_id, maker, taker,
                     nonusdc_side, maker_direction, taker_direction, price,
                     usd_amount, token_amount, transaction_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch)
                self.conn.commit()
                inserted = self.conn.total_changes - before
                total_imported += inserted
                total_skipped += len(batch) - inserted

            print(
                f"  Imported {filepath.name}: {len(df):,} rows "
                f"(total: {total_imported:,}, skipped: {total_skipped:,})"
            )

        print("\nImport complete:")
        print(f"  Trades: {total_imported:,}")
        print(f"  Skipped (duplicates): {total_skipped:,}")

        return {"trades": total_imported, "skipped": total_skipped}

    def import_polymarket_clob(
        self,
        data_dir: str = "data/polymarket_clob",
        batch_size: int = 10000,
    ) -> Dict[str, int]:
        """
        Import Polymarket CLOB data from JSON files to database.

        Args:
            data_dir: Directory with market_*.json files
            batch_size: Rows to insert per batch

        Returns:
            Dict with counts of imported records
        """
        data_path = Path(data_dir)
        files = list(data_path.glob("market_*.json"))
        files = [f for f in files if "index" not in f.name]

        print(f"Importing {len(files)} Polymarket market files...")

        cursor = self.conn.cursor()
        markets_imported = 0
        prices_imported = 0

        for i, filepath in enumerate(files):
            try:
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)

                market_info = data.get("market_info", {})
                price_history = data.get("price_history", {})

                # Insert market
                token_ids = market_info.get("token_ids", [])
                cursor.execute("""
                    INSERT OR REPLACE INTO polymarket_markets
                    (id, slug, question, volume, volume_24hr, liquidity,
                     end_date, outcomes, outcome_prices, token_id_yes, token_id_no)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(market_info.get("id", "")),
                    market_info.get("slug", ""),
                    market_info.get("question", ""),
                    market_info.get("volume", 0),
                    market_info.get("volume24hr", 0),
                    market_info.get("liquidity", 0),
                    market_info.get("endDate", ""),
                    json.dumps(market_info.get("outcomes", [])),
                    json.dumps(market_info.get("outcomePrices", [])),
                    token_ids[0] if len(token_ids) > 0 else "",
                    token_ids[1] if len(token_ids) > 1 else "",
                ))
                markets_imported += 1

                # Insert price history
                market_id = str(market_info.get("id", ""))
                price_rows = []

                for outcome, hist_data in price_history.items():
                    for point in hist_data.get("history", []):
                        ts = point.get("t", 0)
                        price = point.get("p", 0)
                        dt = datetime.fromtimestamp(ts).isoformat() if ts else ""
                        price_rows.append((market_id, outcome, ts, price, dt))

                # Batch insert
                if price_rows:
                    cursor.executemany("""
                        INSERT INTO polymarket_prices
                        (market_id, outcome, timestamp, price, datetime)
                        VALUES (?, ?, ?, ?, ?)
                    """, price_rows)
                    prices_imported += len(price_rows)

                if (i + 1) % 50 == 0:
                    self.conn.commit()
                    print(f"  Imported {i + 1}/{len(files)} markets, {prices_imported:,} prices")

            except Exception as e:
                print(f"  Error importing {filepath.name}: {e}")
                continue

        self.conn.commit()
        print(f"\nImport complete:")
        print(f"  Markets: {markets_imported:,}")
        print(f"  Price points: {prices_imported:,}")

        return {
            "markets": markets_imported,
            "prices": prices_imported,
        }

    def import_manifold_bets(
        self,
        data_dir: str = "data/manifold",
        batch_size: int = 50000,
    ) -> int:
        """
        Import Manifold bets from JSON files to database.

        Args:
            data_dir: Directory with bets_*.json files
            batch_size: Rows to insert per batch

        Returns:
            Count of imported bets
        """
        data_path = Path(data_dir)
        files = sorted(data_path.glob("bets_*.json"))

        print(f"Importing {len(files)} Manifold bet files...")

        cursor = self.conn.cursor()
        total_imported = 0

        for filepath in files:
            try:
                with open(filepath, encoding="utf-8") as f:
                    bets = json.load(f)

                rows = []
                for bet in bets:
                    ts = bet.get("createdTime", 0)
                    dt = datetime.fromtimestamp(ts / 1000).isoformat() if ts else ""
                    rows.append((
                        bet.get("id", ""),
                        bet.get("userId", ""),
                        bet.get("contractId", ""),
                        bet.get("amount", 0),
                        bet.get("outcome", ""),
                        bet.get("probBefore", 0),
                        bet.get("probAfter", 0),
                        ts,
                        dt,
                    ))

                # Batch insert
                for j in range(0, len(rows), batch_size):
                    batch = rows[j:j + batch_size]
                    cursor.executemany("""
                        INSERT OR IGNORE INTO manifold_bets
                        (id, user_id, contract_id, amount, outcome,
                         prob_before, prob_after, created_time, datetime)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, batch)

                total_imported += len(rows)
                self.conn.commit()
                print(f"  Imported {filepath.name}: {len(rows):,} bets (total: {total_imported:,})")

            except Exception as e:
                print(f"  Error importing {filepath.name}: {e}")
                continue

        print(f"\nImport complete: {total_imported:,} bets")
        return total_imported

    def import_manifold_markets(
        self,
        data_dir: str = "data/manifold",
        batch_size: int = 5000,
    ) -> int:
        """
        Import Manifold markets from JSON files to database.

        Args:
            data_dir: Directory with markets_*.json files
            batch_size: Rows to insert per batch

        Returns:
            Count of imported markets
        """
        data_path = Path(data_dir)
        files = sorted(data_path.glob("markets_*.json"))

        print(f"Importing {len(files)} Manifold market files...")

        cursor = self.conn.cursor()
        total_imported = 0

        for filepath in files:
            try:
                with open(filepath, encoding="utf-8") as f:
                    markets = json.load(f)

                rows = []
                for market in markets:
                    mid = market.get("id", "")
                    if not mid:
                        continue
                    rows.append((mid, json.dumps(market)))

                # Batch insert
                for j in range(0, len(rows), batch_size):
                    batch = rows[j:j + batch_size]
                    cursor.executemany("""
                        INSERT OR REPLACE INTO manifold_markets
                        (id, data)
                        VALUES (?, ?)
                    """, batch)

                total_imported += len(rows)
                self.conn.commit()
                print(f"  Imported {filepath.name}: {len(rows):,} markets (total: {total_imported:,})")

            except Exception as e:
                print(f"  Error importing {filepath.name}: {e}")
                continue

        print(f"\nImport complete: {total_imported:,} markets")
        return total_imported

    def get_price_history(
        self,
        market_id: str,
        outcome: str = "YES",
    ) -> pd.DataFrame:
        """Get price history for a market."""
        query = """
            SELECT timestamp, price, datetime
            FROM polymarket_prices
            WHERE market_id = ? AND outcome = ?
            ORDER BY timestamp
        """
        return pd.read_sql_query(query, self.conn, params=(market_id, outcome))

    def get_all_markets(self) -> pd.DataFrame:
        """Get all Polymarket markets."""
        query = """
            SELECT * FROM polymarket_markets
            ORDER BY volume_24hr DESC
        """
        return pd.read_sql_query(query, self.conn)

    def get_price_changes(
        self,
        min_change: float = 0.05,
        time_window_minutes: int = 60,
    ) -> pd.DataFrame:
        """
        Find significant price movements.

        Args:
            min_change: Minimum price change (e.g., 0.05 = 5%)
            time_window_minutes: Time window for change detection

        Returns:
            DataFrame with market_id, start_price, end_price, change, etc.
        """
        query = f"""
            WITH price_windows AS (
                SELECT
                    market_id,
                    outcome,
                    timestamp,
                    price,
                    LAG(price, 1) OVER (
                        PARTITION BY market_id, outcome
                        ORDER BY timestamp
                    ) as prev_price,
                    LAG(timestamp, 1) OVER (
                        PARTITION BY market_id, outcome
                        ORDER BY timestamp
                    ) as prev_timestamp
                FROM polymarket_prices
            )
            SELECT
                market_id,
                outcome,
                prev_timestamp as start_time,
                timestamp as end_time,
                prev_price as start_price,
                price as end_price,
                (price - prev_price) as price_change,
                ABS(price - prev_price) as abs_change
            FROM price_windows
            WHERE prev_price IS NOT NULL
              AND ABS(price - prev_price) >= {min_change}
              AND (timestamp - prev_timestamp) <= {time_window_minutes * 60}
            ORDER BY abs_change DESC
        """
        return pd.read_sql_query(query, self.conn)

    def get_market_stats(self) -> pd.DataFrame:
        """Get statistics for each market."""
        query = """
            SELECT
                m.id,
                m.slug,
                m.question,
                m.volume_24hr,
                COUNT(p.id) as price_points,
                MIN(p.price) as min_price,
                MAX(p.price) as max_price,
                AVG(p.price) as avg_price,
                MIN(p.datetime) as first_price_time,
                MAX(p.datetime) as last_price_time
            FROM polymarket_markets m
            LEFT JOIN polymarket_prices p ON m.id = p.market_id AND p.outcome = 'YES'
            GROUP BY m.id
            ORDER BY m.volume_24hr DESC
        """
        return pd.read_sql_query(query, self.conn)

    def query(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        """Execute custom SQL query."""
        return pd.read_sql_query(sql, self.conn, params=params)

    def close(self):
        """Close database connection."""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def build_database(
    db_path: str = "data/prediction_markets.db",
    import_polymarket: bool = True,
    import_manifold: bool = True,
    import_manifold_markets: bool = True,
    import_polymarket_trades: bool = False,
    polymarket_trades_path: str = "data/polymarket/trades",
) -> PredictionMarketDB:
    """
    Build/update the prediction market database.

    Args:
        db_path: Path to SQLite database
        import_polymarket: Import Polymarket CLOB data
        import_manifold_markets: Import Manifold market metadata
        import_manifold: Import Manifold bets

    Returns:
        Database instance
    """
    print("=" * 60)
    print("BUILDING PREDICTION MARKET DATABASE")
    print("=" * 60)

    db = PredictionMarketDB(db_path)

    if import_polymarket:
        print("\n[1] Importing Polymarket CLOB data...")
        db.import_polymarket_clob()

    if import_polymarket_trades:
        trades_path = Path(polymarket_trades_path)
        if trades_path.is_dir() or trades_path.suffix == ".parquet":
            print("\n[1b] Importing Polymarket trades (parquet)...")
            db.import_polymarket_trades_parquet(str(trades_path))
        else:
            print("\n[1b] Importing Polymarket trades (processed CSV)...")
            db.import_polymarket_trades_csv(polymarket_trades_path)

    if import_manifold_markets:
        print("\n[2] Importing Manifold markets...")
        db.import_manifold_markets()

    if import_manifold:
        print("\n[3] Importing Manifold bets...")
        db.import_manifold_bets()

    print("\n" + "=" * 60)
    print("DATABASE BUILD COMPLETE")
    print("=" * 60)
    print(f"Database: {db_path}")

    # Show stats
    stats = db.query("SELECT COUNT(*) as markets FROM polymarket_markets").iloc[0]["markets"]
    print(f"Polymarket markets: {stats:,}")

    stats = db.query("SELECT COUNT(*) as prices FROM polymarket_prices").iloc[0]["prices"]
    print(f"Polymarket prices: {stats:,}")

    try:
        stats = db.query("SELECT COUNT(*) as trades FROM polymarket_trades").iloc[0]["trades"]
        print(f"Polymarket trades: {stats:,}")
    except Exception:
        print("Polymarket trades: 0")

    try:
        stats = db.query("SELECT COUNT(*) as markets FROM manifold_markets").iloc[0]["markets"]
        print(f"Manifold markets: {stats:,}")
    except Exception:
        print("Manifold markets: 0")

    stats = db.query("SELECT COUNT(*) as bets FROM manifold_bets").iloc[0]["bets"]
    print(f"Manifold bets: {stats:,}")

    return db


def run_data_quality_check(
    db_path: str = DEFAULT_DB_PATH,
    sample_markets: Optional[int] = 200,
    avg_gap_thresholds: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Run data quality checks on Polymarket price history and resolution coverage.

    Args:
        db_path: Path to SQLite database
        sample_markets: Number of markets to sample (None = full scan)
        avg_gap_thresholds: Thresholds (seconds) for average gap alerts

    Returns:
        Dict with summary statistics and issues
    """
    if avg_gap_thresholds is None:
        avg_gap_thresholds = [3600, 6 * 3600, 24 * 3600]

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Resolution coverage
    cursor.execute("PRAGMA table_info(polymarket_markets)")
    cols = {row[1] for row in cursor.fetchall()}
    has_resolution = "resolution_outcome" in cols
    has_closed_flag = "closed" in cols

    total_markets = cursor.execute(
        "SELECT COUNT(*) FROM polymarket_markets"
    ).fetchone()[0]

    resolution_stats = {
        "total_markets": total_markets,
        "resolution_columns_present": has_resolution,
        "closed_flag_present": has_closed_flag,
        "closed_markets": None,
        "resolved_markets": None,
        "resolution_outcomes": {},
        "resolution_coverage": None,
    }

    if has_resolution:
        if has_closed_flag:
            closed = cursor.execute(
                "SELECT COUNT(*) FROM polymarket_markets WHERE closed = 1"
            ).fetchone()[0]
        else:
            # Fallback: closed if end_date is in the past
            now = datetime.now(timezone.utc)
            closed = 0
            for end_date, in cursor.execute(
                "SELECT end_date FROM polymarket_markets"
            ).fetchall():
                if not end_date:
                    continue
                try:
                    end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                if end_dt <= now:
                    closed += 1

        resolved = cursor.execute(
            "SELECT COUNT(*) FROM polymarket_markets WHERE resolution_outcome IS NOT NULL"
        ).fetchone()[0]
        outcomes = dict(cursor.execute(
            "SELECT resolution_outcome, COUNT(*) FROM polymarket_markets "
            "WHERE resolution_outcome IS NOT NULL GROUP BY resolution_outcome"
        ).fetchall())

        resolution_stats.update({
            "closed_markets": closed,
            "resolved_markets": resolved,
            "resolution_outcomes": outcomes,
            "resolution_coverage": (resolved / closed) if closed else None,
        })

    # Price history coverage (sampled)
    market_rows = cursor.execute("SELECT id FROM polymarket_markets").fetchall()
    market_ids = [row[0] for row in market_rows]
    total_in_sample = len(market_ids)

    if sample_markets is not None and sample_markets < total_in_sample:
        market_ids = random.sample(market_ids, sample_markets)
    sample_size = len(market_ids)

    gap_stats = []
    missing_all = 0
    missing_any = 0
    earliest_ts = None
    latest_ts = None

    for idx, market_id in enumerate(market_ids, 1):
        rows = cursor.execute("""
            SELECT outcome, COUNT(*) as points,
                   MIN(timestamp) as min_ts,
                   MAX(timestamp) as max_ts
            FROM polymarket_prices
            WHERE market_id = ?
            GROUP BY outcome
        """, (market_id,)).fetchall()

        if not rows:
            missing_all += 1
            missing_any += 1
            continue

        outcomes_present = {row[0] for row in rows if row[0]}
        if "YES" not in outcomes_present or "NO" not in outcomes_present:
            missing_any += 1

        for outcome, points, min_ts, max_ts in rows:
            if points and points > 1 and min_ts is not None and max_ts is not None:
                avg_gap = (max_ts - min_ts) / (points - 1)
            else:
                avg_gap = None

            gap_stats.append({
                "market_id": market_id,
                "outcome": outcome,
                "points": points,
                "min_ts": min_ts,
                "max_ts": max_ts,
                "avg_gap": avg_gap,
            })

            if min_ts is not None:
                earliest_ts = min_ts if earliest_ts is None else min(earliest_ts, min_ts)
            if max_ts is not None:
                latest_ts = max_ts if latest_ts is None else max(latest_ts, max_ts)

        if idx % 50 == 0:
            print(f"  Processed {idx}/{sample_size} markets...")

    avg_gaps = [g["avg_gap"] for g in gap_stats if g["avg_gap"] is not None]
    avg_gaps_sorted = sorted(avg_gaps, reverse=True)

    gap_threshold_counts = {}
    for threshold in avg_gap_thresholds:
        gap_threshold_counts[threshold] = sum(
            1 for g in avg_gaps if g > threshold
        )

    price_stats = {
        "sampled": sample_markets is not None and sample_size < total_in_sample,
        "sample_size": sample_size,
        "total_markets": total_in_sample,
        "markets_missing_all_price_data": missing_all,
        "markets_missing_any_outcome": missing_any,
        "pairs_checked": len(gap_stats),
        "avg_gap_median": statistics.median(avg_gaps) if avg_gaps else None,
        "avg_gap_p95": (
            avg_gaps_sorted[int(len(avg_gaps_sorted) * 0.05)]
            if len(avg_gaps_sorted) >= 20 else None
        ),
        "avg_gap_max": avg_gaps_sorted[0] if avg_gaps_sorted else None,
        "avg_gap_threshold_counts": gap_threshold_counts,
        "earliest_ts": earliest_ts,
        "latest_ts": latest_ts,
        "sparsest_pairs": sorted(
            [g for g in gap_stats if g["avg_gap"] is not None],
            key=lambda x: x["avg_gap"],
            reverse=True
        )[:10],
    }

    conn.close()

    return {
        "resolution": resolution_stats,
        "price_history": price_stats,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--build":
        db = build_database()
        db.close()
    elif len(sys.argv) > 1 and sys.argv[1] == "--stats":
        sample_markets = 200
        if "--full" in sys.argv:
            sample_markets = None
        if "--sample" in sys.argv:
            idx = sys.argv.index("--sample")
            if len(sys.argv) > idx + 1:
                sample_markets = int(sys.argv[idx + 1])

        print("=" * 60)
        print("DATA QUALITY CHECK")
        print("=" * 60)
        print(f"Database: {DEFAULT_DB_PATH}")
        if sample_markets is None:
            print("Mode: full scan (all markets)")
        else:
            print(f"Mode: sample {sample_markets} markets")

        stats = run_data_quality_check(sample_markets=sample_markets)

        res = stats["resolution"]
        print("\nResolution coverage:")
        print(f"  total_markets: {res['total_markets']:,}")
        print(f"  resolution_columns_present: {res['resolution_columns_present']}")
        if res["resolution_columns_present"]:
            print(f"  closed_markets: {res['closed_markets']:,}")
            print(f"  resolved_markets: {res['resolved_markets']:,}")
            print(f"  resolution_outcomes: {res['resolution_outcomes']}")
            if res["resolution_coverage"] is not None:
                print(f"  resolution_coverage: {res['resolution_coverage']:.2%}")

        price = stats["price_history"]
        print("\nPrice history coverage:")
        print(f"  sampled: {price['sampled']}")
        print(f"  sample_size: {price['sample_size']:,}/{price['total_markets']:,}")
        print(f"  markets_missing_all_price_data: {price['markets_missing_all_price_data']:,}")
        print(f"  markets_missing_any_outcome: {price['markets_missing_any_outcome']:,}")
        print(f"  pairs_checked: {price['pairs_checked']:,}")
        if price["avg_gap_median"] is not None:
            print(f"  avg_gap_median: {price['avg_gap_median'] / 3600:.2f}h")
        if price["avg_gap_p95"] is not None:
            print(f"  avg_gap_p95: {price['avg_gap_p95'] / 3600:.2f}h")
        if price["avg_gap_max"] is not None:
            print(f"  avg_gap_max: {price['avg_gap_max'] / 3600:.2f}h")
        print("  avg_gap_threshold_counts:")
        for threshold, count in price["avg_gap_threshold_counts"].items():
            print(f"    > {threshold / 3600:.0f}h: {count:,}")

        print("\n  sparsest_pairs (top 10 by avg gap):")
        for row in price["sparsest_pairs"]:
            avg_gap = row["avg_gap"]
            if avg_gap is None:
                continue
            print(
                f"    {row['market_id']} {row['outcome']} "
                f"points={row['points']:,} avg_gap={avg_gap / 3600:.2f}h"
            )
    else:
        print("Usage: python -m src.trading.data_modules.database --build")
        print("\nOr use in code:")
        print("  from src.trading.data_modules.database import PredictionMarketDB")
        print("  db = PredictionMarketDB()")
        print("  df = db.get_all_markets()")
