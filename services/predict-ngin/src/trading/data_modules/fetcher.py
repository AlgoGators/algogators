"""
Data fetching from prediction market APIs.

Supports:
- Manifold Markets (free, no auth required)
- Polymarket Gamma API (market metadata, no auth required)
- Polymarket CLOB API (historical price data, no auth required)
"""

import json
import time
import requests
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Set
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import threading
import sys

import pandas as pd

try:  # Kalshi normalisation helpers (offset-aware timestamps, candle parsing)
    from ..arbitrage.kalshi_data import candle_to_row, parse_created_time_to_unix
except ImportError:  # pragma: no cover - when imported outside the package
    from trading.arbitrage.kalshi_data import candle_to_row, parse_created_time_to_unix

_REPO_ROOT = str(Path(__file__).resolve().parents[3])  # predict_ngin lives at the repo root
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
from predict_ngin.markets.polymarket_tokens import parse_list_field  # noqa: E402
from predict_ngin.venues.polymarket.endpoints import CLOB_URL as _PM_CLOB_URL  # noqa: E402


# API endpoints
MANIFOLD_API = "https://api.manifold.markets/v0"
POLYMARKET_API = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_API = _PM_CLOB_URL
POLYMARKET_ORDERBOOK_API = (
    "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/orderbook-subgraph/0.0.1/gn"
)
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"

# CLOB API limits - max ~7 days per request
CLOB_MAX_RANGE_SECONDS = 7 * 24 * 60 * 60

# Concurrency settings
MAX_WORKERS = 20  # Concurrent requests
CLOB_REQUEST_DELAY = 0.05  # Delay between requests per thread

# Rate limiting
REQUEST_DELAY = 0.1  # seconds between requests

# Default order-filled fields from the Goldsky orderbook subgraph
DEFAULT_ORDER_FILLED_FIELDS = [
    "id",
    "timestamp",
    "maker",
    "makerAssetId",
    "makerAmountFilled",
    "taker",
    "takerAssetId",
    "takerAmountFilled",
    "transactionHash",
    "orderHash",
    "fee",
]


class DataFetcher:
    """Fetches and caches prediction market data."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PredictionMarketResearch/1.0"
        })
        self._local = threading.local()

    def _get_session(self) -> requests.Session:
        """Get thread-local session for concurrent requests."""
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
            self._local.session.headers.update({
                "User-Agent": "PredictionMarketResearch/1.0"
            })
        return self._local.session

    def _rate_limit(self):
        time.sleep(REQUEST_DELAY)

    def _save_json(self, data: list, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _require_pyarrow(self):
        try:
            import pyarrow as pa  # noqa: F401
            import pyarrow.parquet as pq  # noqa: F401
        except Exception as exc:
            raise RuntimeError("pyarrow is required for parquet output") from exc

    def _load_state(
        self,
        state_path: Path,
        defaults: Dict[str, Any],
        reset: bool = False,
    ) -> Dict[str, Any]:
        if reset and state_path.exists():
            state_path.unlink()
        if state_path.exists():
            with state_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return defaults

    def _save_state(self, state_path: Path, state: Dict[str, Any]) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f)
        tmp.replace(state_path)

    # =========================================================================
    # MANIFOLD MARKETS
    # =========================================================================

    def fetch_manifold_bets(
        self,
        limit: int = 5000000,
        batch_size: int = 1000,
        before_id: Optional[str] = None,
    ) -> int:
        """
        Fetch bets from Manifold Markets API.

        Args:
            limit: Maximum total bets to fetch
            batch_size: Bets per API request (max 1000)
            before_id: Start fetching before this bet ID

        Returns:
            Total bets fetched
        """
        output_dir = self.data_dir / "manifold"
        output_dir.mkdir(parents=True, exist_ok=True)

        total_fetched = 0
        file_num = 1
        current_batch = []

        print(f"Fetching Manifold bets (target: {limit:,})...")

        while total_fetched < limit:
            self._rate_limit()

            params = {"limit": min(batch_size, 1000)}
            if before_id:
                params["before"] = before_id

            try:
                response = self.session.get(
                    f"{MANIFOLD_API}/bets",
                    params=params
                )
                response.raise_for_status()
                bets = response.json()

                if not bets:
                    break

                current_batch.extend(bets)
                total_fetched += len(bets)
                before_id = bets[-1]["id"]

                # Save every 5000 bets
                if len(current_batch) >= 5000:
                    self._save_json(
                        current_batch,
                        output_dir / f"bets_{file_num}.json"
                    )
                    print(f"  Saved bets_{file_num}.json ({len(current_batch):,} bets, total: {total_fetched:,})")
                    current_batch = []
                    file_num += 1

                if len(bets) < batch_size:
                    break

            except requests.RequestException as e:
                print(f"  Error fetching bets: {e}")
                break

        # Save remaining
        if current_batch:
            self._save_json(
                current_batch,
                output_dir / f"bets_{file_num}.json"
            )
            print(f"  Saved bets_{file_num}.json ({len(current_batch):,} bets)")

        print(f"  Total: {total_fetched:,} bets")
        return total_fetched

    def fetch_manifold_markets(
        self,
        limit: int = 200000,
        batch_size: int = 1000,
    ) -> int:
        """
        Fetch markets from Manifold Markets API.

        Args:
            limit: Maximum markets to fetch
            batch_size: Markets per request (max 1000)

        Returns:
            Total markets fetched
        """
        output_dir = self.data_dir / "manifold"
        output_dir.mkdir(parents=True, exist_ok=True)

        total_fetched = 0
        file_num = 1
        current_batch = []
        before_id = None

        print(f"Fetching Manifold markets (target: {limit:,})...")

        while total_fetched < limit:
            self._rate_limit()

            params = {"limit": min(batch_size, 1000)}
            if before_id:
                params["before"] = before_id

            try:
                response = self.session.get(
                    f"{MANIFOLD_API}/markets",
                    params=params
                )
                response.raise_for_status()
                markets = response.json()

                if not markets:
                    break

                current_batch.extend(markets)
                total_fetched += len(markets)
                before_id = markets[-1]["id"]

                # Save every 5000 markets
                if len(current_batch) >= 5000:
                    self._save_json(
                        current_batch,
                        output_dir / f"markets_{file_num}.json"
                    )
                    print(f"  Saved markets_{file_num}.json ({len(current_batch):,} markets)")
                    current_batch = []
                    file_num += 1

                if len(markets) < batch_size:
                    break

            except requests.RequestException as e:
                print(f"  Error fetching markets: {e}")
                break

        # Save remaining
        if current_batch:
            self._save_json(
                current_batch,
                output_dir / f"markets_{file_num}.json"
            )
            print(f"  Saved markets_{file_num}.json ({len(current_batch):,} markets)")

        print(f"  Total: {total_fetched:,} markets")
        return total_fetched

    # =========================================================================
    # POLYMARKET
    # =========================================================================

    def fetch_polymarket_markets(
        self,
        limit: int = 200000,
        batch_size: int = 100,
        output_format: str = "json",
        parquet_dir: Optional[str] = None,
    ) -> int:
        """
        Fetch markets from Polymarket Gamma API.

        Args:
            limit: Maximum markets to fetch
            batch_size: Markets per request (max 100)
            output_format: "json" or "parquet"
            parquet_dir: Output dir for parquet (default: data/polymarket)

        Returns:
            Total markets fetched
        """
        output_format = (output_format or "json").strip().lower()
        if output_format not in {"json", "parquet"}:
            raise ValueError(f"Unsupported output_format: {output_format}")

        output_dir = self.data_dir / "polymarket"
        output_dir.mkdir(parents=True, exist_ok=True)

        if output_format == "parquet":
            self._require_pyarrow()
            import pyarrow as pa
            import pyarrow.parquet as pq

            parquet_output_dir = Path(parquet_dir) if parquet_dir else Path("data/polymarket")
            parquet_output_dir.mkdir(parents=True, exist_ok=True)

            def _save_parquet_batch(rows: List[Dict[str, Any]], file_num: int) -> None:
                if not rows:
                    return
                normalized = [self._normalize_market_row(r) for r in rows]
                table = pa.Table.from_pylist(normalized)
                pq.write_table(
                    table,
                    parquet_output_dir / f"markets_{file_num}.parquet",
                    compression="zstd",
                    compression_level=3,
                    row_group_size=500_000,  # Optimize row groups for predicate pushdown
                )

        total_fetched = 0
        file_num = 1
        current_batch = []
        offset = 0

        print(f"Fetching Polymarket markets (target: {limit:,})...")

        while total_fetched < limit:
            self._rate_limit()

            try:
                response = self.session.get(
                    f"{POLYMARKET_API}/markets",
                    params={"limit": batch_size, "offset": offset}
                )
                response.raise_for_status()
                markets = response.json()

                if not markets:
                    break

                current_batch.extend(markets)
                total_fetched += len(markets)
                offset += len(markets)

                # Save every 5000 markets
                if len(current_batch) >= 5000:
                    if output_format == "json":
                        self._save_json(
                            current_batch,
                            output_dir / f"markets_{file_num}.json"
                        )
                        print(f"  Saved markets_{file_num}.json ({len(current_batch):,} markets)")
                    else:
                        _save_parquet_batch(current_batch, file_num)
                        print(f"  Saved markets_{file_num}.parquet ({len(current_batch):,} markets)")
                    current_batch = []
                    file_num += 1

                if len(markets) < batch_size:
                    break

            except requests.RequestException as e:
                print(f"  Error fetching Polymarket markets: {e}")
                break

        # Save remaining
        if current_batch:
            if output_format == "json":
                self._save_json(
                    current_batch,
                    output_dir / f"markets_{file_num}.json"
                )
                print(f"  Saved markets_{file_num}.json ({len(current_batch):,} markets)")
            else:
                _save_parquet_batch(current_batch, file_num)
                print(f"  Saved markets_{file_num}.parquet ({len(current_batch):,} markets)")

        print(f"  Total: {total_fetched:,} markets")
        return total_fetched

    def _normalize_market_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, (list, dict)):
                out[k] = json.dumps(v)
            else:
                out[k] = v
        return out

    def _parse_token_list(self, tokens_val: Any) -> List[str]:
        """clobTokenIds in any on-disk encoding (predict_ngin.markets.polymarket_tokens)."""
        return parse_list_field(tokens_val)

    def _load_polymarket_token_map(self, markets_dir: Path) -> Dict[str, Tuple[str, str]]:
        """
        Build token_id -> (market_id, side) map from markets parquet files.
        """
        token_map: Dict[str, Tuple[str, str]] = {}
        if not markets_dir.exists():
            return token_map

        files = sorted(markets_dir.glob("*.parquet"))
        if not files:
            return token_map

        try:
            import pandas as pd
        except Exception as exc:
            raise RuntimeError("pandas is required to load parquet markets") from exc

        for f in files:
            try:
                df = pd.read_parquet(f)
            except Exception:
                continue

            cols = set(df.columns)
            token_col = None
            for c in ("clobTokenIds", "clob_token_ids"):
                if c in cols:
                    token_col = c
                    break
            if token_col is None:
                continue

            if "id" in cols:
                id_col = "id"
            elif "gamma_id" in cols:
                id_col = "gamma_id"
            else:
                continue

            for _, row in df[[id_col, token_col]].iterrows():
                market_id = str(row.get(id_col))
                tokens = self._parse_token_list(row.get(token_col))
                if len(tokens) < 2 or not market_id:
                    continue
                token_map[tokens[0]] = (market_id, "token1")
                token_map[tokens[1]] = (market_id, "token2")

        return token_map

    def _fetch_market_by_token(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single market by token_id (Gamma API)."""
        try:
            response = self.session.get(
                f"{POLYMARKET_API}/markets",
                params={"clob_token_ids": token_id},
                timeout=30,
            )
            response.raise_for_status()
            markets = response.json()
            if markets:
                return markets[0]
        except requests.RequestException:
            return None
        return None

    def _load_markets_from_parquet(
        self,
        parquet_path: str,
        min_volume: float = 0,
        include_closed: bool = True,
        max_markets: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Load markets from parquet file and convert to expected format.
        
        Args:
            parquet_path: Path to markets.parquet file
            min_volume: Minimum volume filter (default: 0 = no filter)
            include_closed: Include closed markets (default: True)
            max_markets: Maximum markets to return (default: None = all)
            
        Returns:
            DataFrame with markets in expected format
        """
        parquet_file = Path(parquet_path)
        if not parquet_file.exists():
            raise FileNotFoundError(f"Markets parquet file not found: {parquet_path}")
        
        print(f"Loading markets from {parquet_path}...")
        df = pd.read_parquet(parquet_file)
        print(f"  Loaded {len(df):,} markets from parquet")
        
        # Filter by closed status
        if not include_closed:
            if "closed" in df.columns:
                df = df[df["closed"] == False]
                print(f"  Filtered to {len(df):,} active markets")
            # If "closed" column doesn't exist, assume all markets are active (don't filter)
        
        # Filter by volume (convert volume column to float)
        if min_volume > 0:
            if "volume" in df.columns:
                df["volume_float"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
                df = df[df["volume_float"] >= min_volume]
                print(f"  Filtered to {len(df):,} markets with volume >= ${min_volume:,.0f}")
        
        # Map columns to expected format
        markets_list = []
        for _, row in df.iterrows():
            # Extract token IDs
            token_ids = []
            if pd.notna(row.get("token1")):
                token_ids.append(str(row["token1"]))
            if pd.notna(row.get("token2")):
                token_ids.append(str(row["token2"]))
            
            # Skip if no tokens
            if not token_ids:
                continue
            
            # Parse outcomes
            outcomes = []
            outcome_prices = []
            if pd.notna(row.get("answer1")):
                outcomes.append(str(row["answer1"]))
            if pd.notna(row.get("answer2")):
                outcomes.append(str(row["answer2"]))
            
            # Parse outcome prices if available
            if pd.notna(row.get("outcome_prices")):
                try:
                    import ast
                    prices_str = str(row["outcome_prices"])
                    if prices_str.startswith('[') and prices_str.endswith(']'):
                        outcome_prices = ast.literal_eval(prices_str)
                    else:
                        outcome_prices = [0, 0]
                except Exception:
                    outcome_prices = [0, 0]
            
            # Get volume (prefer volume_float if we created it, otherwise parse)
            volume_val = row.get("volume_float", 0)
            if volume_val == 0 and pd.notna(row.get("volume")):
                volume_val = pd.to_numeric(row["volume"], errors="coerce")
                if pd.isna(volume_val):
                    volume_val = 0
            
            market = {
                "id": str(row["id"]),
                "question": str(row.get("question", ""))[:100],
                "slug": str(row.get("slug", "")),
                "token_ids": token_ids,
                "volume24hr": float(volume_val),  # Use total volume as proxy for 24hr volume
                "volume": float(volume_val),
                "liquidity": float(pd.to_numeric(row.get("liquidity", 0), errors="coerce") or 0),
                "outcomes": outcomes,
                "outcomePrices": outcome_prices,
                "endDate": str(row.get("end_date", "")),
            }
            markets_list.append(market)
        
        result_df = pd.DataFrame(markets_list)
        
        # Sort by volume and limit if requested
        if len(result_df) > 0:
            result_df = result_df.sort_values("volume24hr", ascending=False)
            if max_markets is not None:
                result_df = result_df.head(max_markets)
        
        return result_df

    def _append_markets_parquet(self, markets: List[Dict[str, Any]], markets_dir: Path) -> int:
        if not markets:
            return 0
        self._require_pyarrow()
        import pyarrow as pa
        import pyarrow.parquet as pq

        markets_dir.mkdir(parents=True, exist_ok=True)
        normalized = [self._normalize_market_row(m) for m in markets]
        table = pa.Table.from_pylist(normalized)

        # Align schema with existing markets parquet if present.
        existing = sorted(markets_dir.glob("*.parquet"))
        if existing:
            schema = pq.read_schema(existing[0])
            if table.schema != schema:
                table = table.cast(schema)

        filename = markets_dir / f"markets_missing_{int(time.time())}.parquet"
        pq.write_table(
            table,
            filename,
            compression="zstd",
            compression_level=3,
            row_group_size=500_000,  # Optimize row groups for predicate pushdown
        )
        return len(markets)

    def fetch_polymarket_price_history(
        self,
        token_id: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        max_days: int = 365,
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical price data for a Polymarket token from CLOB API.

        Args:
            token_id: The CLOB token ID
            start_ts: Start timestamp (default: max_days ago)
            end_ts: End timestamp (default: now)
            max_days: Maximum days of history to fetch

        Returns:
            List of {t: timestamp, p: price} entries
        """
        session = self._get_session()
        now = int(time.time())
        if end_ts is None:
            end_ts = now
        if start_ts is None:
            start_ts = now - (max_days * 24 * 60 * 60)

        all_history = []
        current_start = start_ts

        while current_start < end_ts:
            current_end = min(current_start + CLOB_MAX_RANGE_SECONDS, end_ts)
            time.sleep(CLOB_REQUEST_DELAY)  # Lighter rate limit for CLOB

            try:
                response = session.get(
                    f"{POLYMARKET_CLOB_API}/prices-history",
                    params={
                        "market": token_id,
                        "startTs": current_start,
                        "endTs": current_end,
                    },
                    timeout=30,
                )

                if response.status_code == 200:
                    data = response.json()
                    history = data.get("history", [])
                    all_history.extend(history)
                elif response.status_code == 429:
                    # Rate limited, wait and retry
                    time.sleep(1)
                    continue

            except requests.RequestException:
                pass

            current_start = current_end

        return all_history

    def _load_checkpoint(self, checkpoint_path: Path) -> Set[str]:
        """Load processed market IDs from checkpoint file."""
        if checkpoint_path.exists():
            try:
                with open(checkpoint_path, 'r') as f:
                    checkpoint = json.load(f)
                    processed_ids = set(checkpoint.get("processed_market_ids", []))
                    print(f"  Loaded checkpoint: {len(processed_ids):,} markets already processed")
                    return processed_ids
            except (json.JSONDecodeError, IOError) as e:
                print(f"  Warning: Could not load checkpoint: {e}")
        return set()

    def _save_checkpoint(self, checkpoint_path: Path, processed_ids: Set[str], last_market_id: Optional[str] = None) -> None:
        """Save checkpoint with processed market IDs."""
        try:
            checkpoint = {
                "processed_market_ids": sorted(list(processed_ids)),
                "last_market_id": last_market_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            # Write to temp file first, then rename (atomic on most filesystems)
            temp_path = checkpoint_path.with_suffix('.tmp')
            with open(temp_path, 'w') as f:
                json.dump(checkpoint, f, indent=2)
            temp_path.replace(checkpoint_path)
        except IOError as e:
            print(f"  Warning: Could not save checkpoint: {e}")

    def _append_to_parquet(
        self,
        new_data: pd.DataFrame,
        filepath: Path,
    ) -> None:
        """Append new data to existing parquet file, or create new file if it doesn't exist.
        
        Note: For very large existing files, this reads the entire file into memory.
        Since we flush frequently, files should remain manageable in size.
        """
        try:
            from trading.data_modules.parquet_utils import write_prices_optimized
            USE_OPTIMIZED = True
        except ImportError:
            USE_OPTIMIZED = False
            import pyarrow.parquet as pq
            import pyarrow as pa

        if new_data.empty:
            return

        # Read existing data if file exists
        if filepath.exists():
            try:
                # Check file size (warn if > 500MB uncompressed)
                file_size_mb = filepath.stat().st_size / (1024 * 1024)
                if file_size_mb > 500:
                    print(f"    Warning: Large parquet file {filepath.name} ({file_size_mb:.1f}MB). Reading may use significant memory.")
                
                existing_df = pd.read_parquet(filepath)
                # Combine and deduplicate (in case of resume)
                combined_df = pd.concat([existing_df, new_data], ignore_index=True)
                # Remove duplicates based on (market_id, outcome, timestamp)
                combined_df = combined_df.drop_duplicates(
                    subset=["market_id", "outcome", "timestamp"],
                    keep="last"  # Keep newer data if duplicates exist
                )
            except MemoryError as e:
                print(f"    Error: Memory error reading {filepath.name}. File may be too large.")
                print(f"    Consider reducing flush_interval or max_memory_rows to flush more frequently.")
                raise
            except Exception as e:
                print(f"    Warning: Could not read existing parquet {filepath.name}: {e}")
                combined_df = new_data
        else:
            combined_df = new_data

        # Sort and write
        combined_df = combined_df.sort_values(["market_id", "outcome", "timestamp"]).reset_index(drop=True)

        if USE_OPTIMIZED:
            write_prices_optimized(combined_df, filepath)
        else:
            try:
                table = pa.Table.from_pandas(combined_df, preserve_index=False)
                pq.write_table(
                    table,
                    filepath,
                    compression="zstd",
                    compression_level=3,
                    row_group_size=500_000,
                )
            except ImportError:
                combined_df.to_parquet(filepath, index=False, compression="snappy")

    def _flush_monthly_data(
        self,
        monthly_data: Dict[str, List[Dict[str, Any]]],
        parquet_dir: Path,
        lock: Optional[threading.Lock] = None,
    ) -> Tuple[int, int]:
        """Flush accumulated monthly data to parquet files."""
        if lock:
            with lock:
                # Copy data to avoid holding lock during I/O
                data_to_flush = {k: list(v) for k, v in monthly_data.items()}
                monthly_data.clear()
        else:
            data_to_flush = monthly_data
            monthly_data.clear()

        written_files = 0
        total_rows = 0

        for month, rows in data_to_flush.items():
            if not rows:
                continue

            df = pd.DataFrame(rows)
            output_file = parquet_dir / f"prices_{month}.parquet"
            self._append_to_parquet(df, output_file)
            written_files += 1
            total_rows += len(rows)

        return written_files, total_rows

    def _fetch_single_market_history(
        self,
        market: Dict[str, Any],
        output_dir: Path,
        max_days: int,
        progress: Dict[str, int],
        lock: threading.Lock,
        monthly_data: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> Tuple[bool, int]:
        """Fetch price history for a single market (thread-safe).
        
        Accumulates price data in monthly_data dict for parquet writing.
        """
        try:
            market_id = str(market["id"])
            total_points = 0

            # Fetch history for each token (YES/NO outcomes)
            for j, token_id in enumerate(market["token_ids"][:2]):
                history = self.fetch_polymarket_price_history(
                    token_id,
                    max_days=max_days,
                )
                outcome = "YES" if j == 0 else "NO"
                
                # Accumulate price data grouped by month
                if monthly_data is not None:
                    for point in history:
                        ts = point.get("t", 0)
                        price = point.get("p", 0)
                        if ts and price:
                            # Convert timestamp to date for grouping
                            try:
                                date_str = pd.Timestamp(ts, unit="s").strftime("%Y-%m")
                                with lock:
                                    monthly_data[date_str].append({
                                        "market_id": market_id,
                                        "outcome": outcome,
                                        "timestamp": ts,
                                        "price": price,
                                    })
                            except (ValueError, OSError):
                                # Skip invalid timestamps
                                continue
                
                total_points += len(history)

            with lock:
                progress["fetched"] += 1
                progress["total_points"] += total_points
                if progress["fetched"] % 20 == 0:
                    print(f"  Progress: {progress['fetched']}/{progress['total']} markets, {progress['total_points']:,} data points")

            return True, total_points

        except Exception as e:
            with lock:
                progress["errors"] += 1
            return False, 0

    def fetch_polymarket_clob_data(
        self,
        min_volume: float = 10000,  # Default: $10k minimum volume
        max_markets: Optional[int] = None,
        max_days: int = 1180,  # Default: ~3.2 years (matches existing trades data: Nov 21, 2022 - Dec 30, 2025)
        workers: int = MAX_WORKERS,
        include_closed: bool = True,  # Default: include closed markets for maximum historical data
        markets_parquet_path: Optional[str] = None,  # Path to markets.parquet file
        flush_interval: int = 10,  # Flush every N markets (reduce for low memory)
        max_memory_rows: int = 100_000,  # Flush when accumulator exceeds this many rows
    ) -> int:
        """
        Fetch historical price data for Polymarket markets with memory management and resumability.
        
        Writes price data directly to monthly parquet partitions instead of JSON.
        Supports checkpoint/resume: if interrupted, can continue where it left off.
        Flushes data periodically to avoid memory issues.
        
        If markets_parquet_path is provided, loads markets from parquet file instead of API.

        Args:
            min_volume: Minimum 24hr volume to include market (default: 10000 = $10k)
            max_markets: Maximum number of markets to fetch (default: None = unlimited, ignored if markets_parquet_path provided)
            max_days: Days of history per market (default: 1180 = ~3.2 years, matches existing trades data: Nov 21, 2022 - Dec 30, 2025)
            workers: Number of concurrent workers (default: 20)
            include_closed: Include closed markets in the scan (default: True for maximum historical data)
            markets_parquet_path: Path to markets.parquet file (default: None = fetch from API)
            flush_interval: Flush data to disk every N markets (default: 50)
            max_memory_rows: Maximum rows to accumulate before flushing (default: 1,000,000)

        Returns:
            Total markets fetched
        """
        self._require_pyarrow()
        
        # Store price data in data/polymarket/prices/ to keep all Polymarket data together
        polymarket_dir = self.data_dir / "polymarket"
        parquet_dir = polymarket_dir / "prices"
        parquet_dir.mkdir(parents=True, exist_ok=True)
        
        # Checkpoint file for resumability (also in polymarket directory)
        checkpoint_path = polymarket_dir / "price_fetch_checkpoint.json"
        
        # Keep market metadata index in polymarket directory
        metadata_dir = polymarket_dir

        # Load checkpoint to resume if interrupted
        processed_market_ids = self._load_checkpoint(checkpoint_path)

        # Load markets from parquet file if provided, otherwise fetch from API
        if markets_parquet_path:
            markets_df = self._load_markets_from_parquet(markets_parquet_path, min_volume, include_closed, max_markets)
            markets = markets_df.to_dict('records')
            print(f"Loaded {len(markets):,} markets from {markets_parquet_path}")
            
            # Filter out already-processed markets
            if processed_market_ids:
                original_count = len(markets)
                markets = [m for m in markets if str(m["id"]) not in processed_market_ids]
                skipped = original_count - len(markets)
                if skipped > 0:
                    print(f"  Resuming: Skipping {skipped:,} already-processed markets")
        else:
            # First, get list of markets with volume from API
            print("Fetching Polymarket markets from API...")
            markets_by_id: Dict[str, Dict[str, Any]] = {}
            # Use smaller batch size for closed markets to avoid memory issues
            batch_size = 50 if include_closed else 100
            closed_flags = ["false", "true"] if include_closed else ["false"]

            for closed_flag in closed_flags:
                offset = 0
                consecutive_empty_batches = 0
                max_consecutive_empty = 3  # Stop after 3 empty batches in a row
                # Fetch enough markets: if max_markets specified, fetch 2x to filter; otherwise fetch all
                target_count = (max_markets * 2) if max_markets else float('inf')
                while len(markets_by_id) < target_count:
                    self._rate_limit()
                    try:
                        response = self.session.get(
                            f"{POLYMARKET_API}/markets",
                            params={
                                "limit": batch_size,
                                "offset": offset,
                                "closed": closed_flag,
                            },
                            timeout=60,  # Increase timeout for large responses
                        )
                        response.raise_for_status()
                        
                        # Check response size before parsing (warn if > 50MB)
                        content_length = response.headers.get('content-length')
                        if content_length:
                            size_mb = int(content_length) / (1024 * 1024)
                            if size_mb > 50:
                                print(f"  Warning: Large response ({size_mb:.1f}MB) for closed={closed_flag}, offset={offset}")
                        
                        # Try to parse JSON with memory error handling
                        try:
                            batch = response.json()
                        except MemoryError:
                            print(f"  MemoryError parsing response (closed={closed_flag}, offset={offset})")
                            print(f"  Response size: {len(response.content) / (1024*1024):.1f}MB")
                            print(f"  Reducing batch size and retrying...")
                            # Reduce batch size and retry
                            batch_size = max(10, batch_size // 2)
                            print(f"  New batch size: {batch_size}")
                            continue
                        except json.JSONDecodeError as e:
                            print(f"  JSON decode error (closed={closed_flag}, offset={offset}): {e}")
                            print(f"  Response preview: {response.text[:500]}")
                            break

                        if not batch:
                            consecutive_empty_batches += 1
                            if consecutive_empty_batches >= max_consecutive_empty:
                                print(f"  Stopping after {max_consecutive_empty} consecutive empty batches")
                                break
                            offset += batch_size  # Still increment offset to try next batch
                            continue
                        else:
                            consecutive_empty_batches = 0  # Reset counter on successful batch

                        for m in batch:
                            vol = float(m.get("volume24hr", 0) or 0)
                            tokens = m.get("clobTokenIds", "[]")
                            if isinstance(tokens, str):
                                try:
                                    tokens = json.loads(tokens)
                                except json.JSONDecodeError:
                                    tokens = []

                            if vol >= min_volume and tokens:
                                market_id = str(m.get("id"))
                                # Skip if already processed
                                if market_id not in processed_market_ids:
                                    markets_by_id[market_id] = {
                                        "id": m.get("id"),
                                        "question": m.get("question", "")[:100],
                                        "slug": m.get("slug"),
                                        "token_ids": tokens,
                                        "volume24hr": vol,
                                        "volume": float(m.get("volume", 0) or 0),
                                        "liquidity": float(m.get("liquidity", 0) or 0),
                                        "outcomes": m.get("outcomes"),
                                        "outcomePrices": m.get("outcomePrices"),
                                        "endDate": m.get("endDate"),
                                    }

                        offset += len(batch)
                        print(
                            f"  Scanned {offset:,} markets (closed={closed_flag}), "
                            f"found {len(markets_by_id):,} with volume >= ${min_volume:,.0f}"
                        )

                        # Stop if we got fewer markets than requested (end of results)
                        if len(batch) < batch_size:
                            print(f"  Reached end of results (got {len(batch)} < {batch_size} markets)")
                            break

                    except requests.RequestException as e:
                        print(f"  Error fetching markets (closed={closed_flag}): {e}")
                        break
                    except MemoryError as e:
                        print(f"  MemoryError fetching markets (closed={closed_flag}, offset={offset}): {e}")
                        print(f"  Reducing batch size and retrying...")
                        batch_size = max(10, batch_size // 2)
                        print(f"  New batch size: {batch_size}")
                        continue

            markets = list(markets_by_id.values())
            label = "active + closed" if include_closed else "active"
            print(f"  Found {len(markets)} {label} markets with volume >= ${min_volume:,.0f}")

            # Warn if we got exactly 1000 markets (suspiciously round number)
            if len(markets) == 1000 and max_markets is None:
                print(f"  ⚠️  WARNING: Found exactly 1000 markets. This may indicate a pagination limit.")
                print(f"     If you expected more markets, the API may have a limit or pagination issue.")

            if not markets:
                return 0

            # Sort by volume and limit (if max_markets specified)
            markets.sort(key=lambda x: x.get("volume24hr", 0), reverse=True)
            if max_markets is not None:
                markets = markets[:max_markets]
                print(f"  Limited to top {len(markets):,} markets by volume")

        if not markets:
            if processed_market_ids:
                print(f"  All markets already processed. Total: {len(processed_market_ids):,}")
            return len(processed_market_ids)

        # Process markets sequentially in batches with periodic flushing
        print(f"Fetching price history for {len(markets)} markets...")
        print(f"  Writing to parquet: {parquet_dir}")
        print(f"  Flush interval: every {flush_interval} markets or {max_memory_rows:,} rows")

        progress = {"fetched": 0, "errors": 0, "total_points": 0, "total": len(markets)}
        lock = threading.Lock()
        
        # Thread-safe accumulator for monthly price data
        monthly_data: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        # Process markets in batches to enable checkpointing
        batch_size = min(workers, flush_interval)
        total_flushed = 0
        markets_processed_since_flush = 0

        try:
            for batch_start in range(0, len(markets), batch_size):
                batch_markets = markets[batch_start:batch_start + batch_size]
                
                # Fetch batch concurrently
                with ThreadPoolExecutor(max_workers=min(workers, len(batch_markets))) as executor:
                    futures = {
                        executor.submit(
                            self._fetch_single_market_history,
                            market,
                            metadata_dir,
                            max_days,
                            progress,
                            lock,
                            monthly_data,
                        ): market
                        for market in batch_markets
                    }

                    for future in as_completed(futures):
                        market = futures[future]
                        market_id = str(market["id"])
                        try:
                            success, points = future.result()
                            if success:
                                processed_market_ids.add(market_id)
                                markets_processed_since_flush += 1
                        except Exception as e:
                            print(f"  Error processing market {market_id}: {e}")
                            with lock:
                                progress["errors"] += 1

                # Check if we should flush (by count or memory)
                with lock:
                    total_rows = sum(len(rows) for rows in monthly_data.values())
                should_flush = (
                    (markets_processed_since_flush >= flush_interval) or
                    (total_rows >= max_memory_rows)
                )

                if should_flush and monthly_data:
                    written_files, flushed_rows = self._flush_monthly_data(monthly_data, parquet_dir, lock)
                    total_flushed += flushed_rows
                    print(f"  Flushed {flushed_rows:,} rows to {written_files} parquet files (total: {progress['fetched']}/{progress['total']} markets)")
                    markets_processed_since_flush = 0
                    
                    # Save checkpoint after flush
                    last_market_id = str(batch_markets[-1]["id"]) if batch_markets else None
                    self._save_checkpoint(checkpoint_path, processed_market_ids, last_market_id)

        except KeyboardInterrupt:
            print("\n  Interrupted by user. Flushing accumulated data...")
            # Flush remaining data
            if monthly_data:
                written_files, flushed_rows = self._flush_monthly_data(monthly_data, parquet_dir, lock)
                total_flushed += flushed_rows
                print(f"  Flushed {flushed_rows:,} rows before exit")
            # Save checkpoint
            self._save_checkpoint(checkpoint_path, processed_market_ids)
            print(f"  Checkpoint saved. Processed {len(processed_market_ids):,} markets.")
            print(f"  To resume, run the same command again.")
            raise

        # Final flush of any remaining data
        if monthly_data:
            written_files, flushed_rows = self._flush_monthly_data(monthly_data, parquet_dir)
            total_flushed += flushed_rows
            print(f"  Final flush: {flushed_rows:,} rows")

        # Save final checkpoint
        self._save_checkpoint(checkpoint_path, processed_market_ids)
        
        # Save market index (metadata) in polymarket directory
        all_markets = markets + [{"id": mid} for mid in processed_market_ids if mid not in {str(m["id"]) for m in markets}]
        index = [{
            "id": m.get("id", m),
            "slug": m.get("slug", ""),
            "question": m.get("question", "")[:100],
            "volume24hr": m.get("volume24hr", 0),
        } for m in all_markets if isinstance(m, dict)]
        self._save_json(index, polymarket_dir / "market_index.json")

        print(f"\n  Complete: {progress['fetched']} markets, {progress['total_points']:,} data points")
        print(f"  Written: {total_flushed:,} total rows to parquet files")
        if progress["errors"] > 0:
            print(f"  Errors: {progress['errors']}")

        return progress["fetched"]

    def fetch_polymarket_order_filled_events(
        self,
        output_dir: str = "data/polymarket/order_filled_events",
        state_path: Optional[str] = None,
        endpoint: str = POLYMARKET_ORDERBOOK_API,
        entity: str = "orderFilledEvents",
        fields: Optional[List[str]] = None,
        batch_size: int = 1000,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        sleep: float = 0.2,
        max_batches: int = 0,
        reset: bool = False,
    ) -> Dict[str, Any]:
        """
        Backfill Polymarket order-filled events from present to past (resume-safe).

        Writes parquet part files into output_dir and stores state for resume.
        """
        self._require_pyarrow()
        import pyarrow as pa
        import pyarrow.parquet as pq

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        state_file = Path(state_path) if state_path else out_dir / "_state.json"

        if fields is None:
            fields = list(DEFAULT_ORDER_FILLED_FIELDS)
        if "id" not in fields:
            fields = ["id"] + fields
        if "timestamp" not in fields:
            fields = ["timestamp"] + fields

        start_ts = start_ts or int(time.time())
        end_ts = end_ts or None

        existing_state = state_file.exists() and not reset
        state = self._load_state(
            state_file,
            defaults={
                "entity": entity,
                "fields": fields,
                "cursor_timestamp": start_ts,
                "sticky_timestamp": None,
                "last_id": None,
                "part_seq": 0,
            },
            reset=reset,
        )

        if existing_state:
            if state.get("entity") != entity or state.get("fields") != fields:
                raise RuntimeError(
                    "State entity/fields mismatch. Use reset=True or a new output dir."
                )

        cursor_ts = int(state.get("cursor_timestamp") or start_ts)
        sticky_ts = state.get("sticky_timestamp")
        last_id = state.get("last_id")
        part_seq = int(state.get("part_seq") or 0)

        session = self.session
        batches = 0
        total_rows = 0

        def _build_query(where_clause: str) -> str:
            fields_str = "\n            ".join(fields)
            return f"""
                query BackfillQuery {{
                    {entity}(
                        orderBy: timestamp,
                        orderDirection: desc,
                        first: {batch_size},
                        where: {{{where_clause}}}
                    ) {{
                        {fields_str}
                    }}
                }}
            """.strip()

        while True:
            if end_ts is not None and cursor_ts <= end_ts:
                break

            if sticky_ts is not None and last_id:
                where_clause = f'timestamp: "{sticky_ts}", id_lt: "{last_id}"'
            else:
                where_clause = f'timestamp_lt: "{cursor_ts}"'

            query = _build_query(where_clause)
            payload = {"query": query}

            rows: List[Dict[str, Any]] = []
            for _ in range(5):
                try:
                    resp = session.post(endpoint, json=payload, timeout=60)
                    if resp.status_code == 429:
                        time.sleep(max(sleep, 0.5))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if "errors" in data:
                        raise RuntimeError(f"GraphQL error: {data['errors']}")
                    rows = data.get("data", {}).get(entity, []) or []
                    break
                except requests.RequestException:
                    time.sleep(max(sleep, 0.5))
                    continue

            if not rows:
                if sticky_ts is not None:
                    cursor_ts = int(sticky_ts)
                    sticky_ts = None
                    last_id = None
                    continue
                break

            # Deduplicate by id within batch
            seen_ids: Set[str] = set()
            deduped = []
            for row in rows:
                row_id = str(row.get("id"))
                if not row_id or row_id in seen_ids:
                    continue
                seen_ids.add(row_id)
                deduped.append(row)
            rows = deduped

            # Sort descending by timestamp, id
            def _row_key(r: Dict[str, Any]) -> Tuple[int, str]:
                try:
                    ts_val = int(r.get("timestamp", 0))
                except (TypeError, ValueError):
                    ts_val = 0
                return (ts_val, str(r.get("id") or ""))

            rows.sort(key=_row_key, reverse=True)

            batch_first_ts = int(rows[0]["timestamp"])
            batch_last_ts = int(rows[-1]["timestamp"])
            batch_last_id = str(rows[-1].get("id"))

            # Bucket rows by month and write parquet
            buckets: Dict[str, Dict[str, List[Any]]] = {}
            for row in rows:
                try:
                    ts_val = int(row.get("timestamp"))
                except (TypeError, ValueError):
                    continue
                if end_ts is not None and ts_val < end_ts:
                    continue
                month = datetime.utcfromtimestamp(ts_val).strftime("%Y-%m")
                if month not in buckets:
                    buckets[month] = {f: [] for f in fields}
                for f in fields:
                    val = row.get(f)
                    if f == "timestamp":
                        val = ts_val
                    buckets[month][f].append(val)

            for month, cols in buckets.items():
                if not cols or not cols[fields[0]]:
                    continue
                table = pa.table(cols)
                filepath = out_dir / f"order_filled_{month}_part{part_seq:05d}.parquet"
                pq.write_table(
                    table,
                    filepath,
                    compression="zstd",
                    compression_level=3,
                    row_group_size=500_000,  # Optimize row groups for predicate pushdown
                )
                part_seq += 1
                total_rows += table.num_rows

            # Update cursor
            if len(rows) >= batch_size:
                sticky_ts = batch_last_ts
                last_id = batch_last_id
            else:
                cursor_ts = batch_last_ts
                sticky_ts = None
                last_id = None

            state.update(
                {
                    "cursor_timestamp": cursor_ts,
                    "sticky_timestamp": sticky_ts,
                    "last_id": last_id,
                    "part_seq": part_seq,
                }
            )
            self._save_state(state_file, state)

            batches += 1
            if max_batches and batches >= max_batches:
                break
            if sleep > 0:
                time.sleep(sleep)

        return {
            "rows_written": total_rows,
            "batches": batches,
            "output_dir": str(out_dir),
            "state_file": str(state_file),
        }

    def process_polymarket_trades_from_order_filled(
        self,
        order_filled_dir: str = "data/polymarket/order_filled_events",
        output_dir: str = "data/polymarket/trades",
        markets_dir: str = "data/polymarket",
        state_path: Optional[str] = None,
        reset: bool = False,
        max_files: int = 0,
        fetch_missing_tokens: bool = False,
    ) -> Dict[str, Any]:
        """
        Convert order-filled events parquet -> processed trades parquet (resume-safe).
        """
        self._require_pyarrow()
        import pyarrow as pa
        import pyarrow.parquet as pq
        import pandas as pd
        import numpy as np

        in_dir = Path(order_filled_dir)
        out_dir = Path(output_dir)
        markets_path = Path(markets_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        state_file = Path(state_path) if state_path else out_dir / "_order_filled_state.json"

        state = self._load_state(
            state_file,
            defaults={
                "last_input_file": None,
                "part_seq": 0,
            },
            reset=reset,
        )

        token_map = self._load_polymarket_token_map(markets_path)
        if not token_map:
            raise RuntimeError(
                f"No token map found. Ensure markets parquet exists in {markets_path}."
            )

        files = sorted(in_dir.glob("*.parquet"))
        if not files:
            return {"rows_written": 0, "files": 0}

        last_file = state.get("last_input_file")
        part_seq = int(state.get("part_seq") or 0)
        processed_files = 0
        total_rows = 0
        skipped_missing = 0

        def _apply_mapping(df: "pd.DataFrame") -> "pd.DataFrame":
            nonusdc = df["makerAssetId"].where(df["makerAssetId"] != "0", df["takerAssetId"])
            df["nonusdc_asset_id"] = nonusdc
            df["market_id"] = df["nonusdc_asset_id"].map(lambda x: token_map.get(x, (None, None))[0])
            df["nonusdc_side"] = df["nonusdc_asset_id"].map(lambda x: token_map.get(x, (None, None))[1])
            return df

        for f in files:
            if last_file and f.name <= last_file:
                continue
            df = pd.read_parquet(f)
            if df.empty:
                state["last_input_file"] = f.name
                self._save_state(state_file, state)
                continue

            required = [
                "timestamp",
                "maker",
                "makerAssetId",
                "makerAmountFilled",
                "taker",
                "takerAssetId",
                "takerAmountFilled",
                "transactionHash",
            ]
            missing_cols = [c for c in required if c not in df.columns]
            if missing_cols:
                raise RuntimeError(f"Missing columns in order-filled data: {missing_cols}")

            df["makerAssetId"] = df["makerAssetId"].astype(str)
            df["takerAssetId"] = df["takerAssetId"].astype(str)
            df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
            df = df.dropna(subset=["timestamp"])

            if "id" in df.columns:
                df = df.drop_duplicates(subset=["id"])

            df = _apply_mapping(df)
            missing_tokens = df[df["market_id"].isna()]["nonusdc_asset_id"].dropna().unique().tolist()
            if missing_tokens and fetch_missing_tokens:
                fetched = []
                for token_id in missing_tokens:
                    market = self._fetch_market_by_token(str(token_id))
                    if market:
                        fetched.append(market)
                        tokens = self._parse_token_list(market.get("clobTokenIds"))
                        if len(tokens) >= 2:
                            token_map[tokens[0]] = (str(market.get("id")), "token1")
                            token_map[tokens[1]] = (str(market.get("id")), "token2")
                if fetched:
                    self._append_markets_parquet(fetched, markets_path)
                    df = _apply_mapping(df)

            before = len(df)
            df = df.dropna(subset=["market_id", "nonusdc_side"])
            skipped_missing += (before - len(df))
            if df.empty:
                state["last_input_file"] = f.name
                self._save_state(state_file, state)
                continue

            df["makerAmountFilled"] = pd.to_numeric(df["makerAmountFilled"], errors="coerce") / 1e6
            df["takerAmountFilled"] = pd.to_numeric(df["takerAmountFilled"], errors="coerce") / 1e6

            df["makerAsset"] = np.where(df["makerAssetId"] == "0", "USDC", df["nonusdc_side"])
            df["takerAsset"] = np.where(df["takerAssetId"] == "0", "USDC", df["nonusdc_side"])

            df["taker_direction"] = np.where(df["takerAsset"] == "USDC", "BUY", "SELL")
            df["maker_direction"] = np.where(df["takerAsset"] == "USDC", "SELL", "BUY")

            df["usd_amount"] = np.where(
                df["takerAsset"] == "USDC",
                df["takerAmountFilled"],
                df["makerAmountFilled"],
            )
            df["token_amount"] = np.where(
                df["takerAsset"] != "USDC",
                df["takerAmountFilled"],
                df["makerAmountFilled"],
            )

            df = df.dropna(subset=["usd_amount", "token_amount"])
            df = df[df["token_amount"] > 0]
            df["price"] = df["usd_amount"] / df["token_amount"]

            dt = pd.to_datetime(df["timestamp"], unit="s", utc=True)
            df["timestamp"] = dt.dt.strftime("%Y-%m-%d %H:%M:%S")
            df["month"] = dt.dt.strftime("%Y-%m")

            out_cols = [
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
            df = df[out_cols + ["month"]]

            for month, group in df.groupby("month"):
                out_df = group.drop(columns=["month"])
                table = pa.Table.from_pandas(out_df, preserve_index=False)
                filepath = out_dir / f"trades_{month}_part{part_seq:05d}.parquet"
                pq.write_table(
                    table,
                    filepath,
                    compression="zstd",
                    compression_level=3,
                    row_group_size=500_000,  # Optimize row groups for predicate pushdown
                )
                part_seq += 1
                total_rows += table.num_rows

            processed_files += 1
            state.update({"last_input_file": f.name, "part_seq": part_seq})
            self._save_state(state_file, state)

            if max_files and processed_files >= max_files:
                break

        return {
            "rows_written": total_rows,
            "files": processed_files,
            "skipped_missing": skipped_missing,
            "output_dir": str(out_dir),
            "state_file": str(state_file),
        }

    # =========================================================================
    # KALSHI
    # =========================================================================

    def fetch_kalshi_events(
        self,
        with_nested_markets: bool = True,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Fetch events from Kalshi API (paginated, no auth required)."""
        all_events: List[Dict[str, Any]] = []
        cursor = ""
        page = 0

        while True:
            params: Dict[str, Any] = {
                "limit": min(limit, 200),
                "with_nested_markets": str(with_nested_markets).lower(),
            }
            if cursor:
                params["cursor"] = cursor
            if status:
                params["status"] = status

            try:
                response = self.session.get(
                    f"{KALSHI_API}/events", params=params, timeout=60,
                )
                if response.status_code == 429:
                    time.sleep(2)
                    continue
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                print(f"  Error fetching Kalshi events (page {page}): {e}")
                break

            events = data.get("events", [])
            if not events:
                break

            all_events.extend(events)
            page += 1
            if page % 5 == 0:
                print(f"  Fetched {len(all_events):,} events (page {page})...")

            cursor = data.get("cursor", "")
            if not cursor:
                break
            self._rate_limit()

        return all_events

    def fetch_kalshi_markets(
        self,
        status: Optional[str] = None,
        event_ticker: Optional[str] = None,
        limit: int = 1000,
        min_volume: float = 0,
    ) -> List[Dict[str, Any]]:
        """Fetch markets from Kalshi API (paginated, no auth required).

        If min_volume > 0, only markets meeting the threshold are kept in memory.
        All markets are still scanned, but low-volume ones are discarded immediately.
        Uses a fresh session to avoid memory accumulation in long scans.

        NOTE: The /markets endpoint returns 100k+ mostly low-volume sports parlays.
        For better results, use fetch_kalshi_markets_via_events() which finds
        high-volume markets much faster by scanning events first.
        """
        import gc

        all_markets: List[Dict[str, Any]] = []
        cursor = ""
        page = 0
        total_scanned = 0

        # Use a dedicated session for this long scan to avoid memory leaks
        scan_session = requests.Session()
        scan_session.headers.update({"User-Agent": "PredictionMarketResearch/1.0"})

        try:
            while True:
                params: Dict[str, Any] = {"limit": min(limit, 1000)}
                if cursor:
                    params["cursor"] = cursor
                if status:
                    params["status"] = status
                if event_ticker:
                    params["event_ticker"] = event_ticker

                try:
                    response = scan_session.get(
                        f"{KALSHI_API}/markets", params=params, timeout=60,
                    )
                    if response.status_code == 429:
                        response.close()
                        time.sleep(2)
                        continue
                    response.raise_for_status()
                    data = response.json()
                    response.close()
                except requests.RequestException as e:
                    print(f"  Error fetching Kalshi markets (page {page}): {e}")
                    break
                except MemoryError:
                    print(f"  MemoryError at page {page}, scanned {total_scanned:,}.")
                    gc.collect()
                    scan_session.close()
                    scan_session = requests.Session()
                    scan_session.headers.update({"User-Agent": "PredictionMarketResearch/1.0"})
                    time.sleep(1)
                    continue

                markets = data.get("markets", [])
                cursor = data.get("cursor", "")
                del data

                if not markets:
                    break

                if min_volume > 0:
                    for m in markets:
                        vol = m.get("volume", 0) or 0
                        if vol >= min_volume:
                            all_markets.append(m)
                else:
                    all_markets.extend(markets)

                total_scanned += len(markets)
                del markets
                page += 1

                # Print progress every 5 pages (not 20) for better visibility
                if page % 5 == 0:
                    if min_volume > 0:
                        print(f"  Scanned {total_scanned:,} markets (page {page}), "
                              f"kept {len(all_markets):,} with volume >= {min_volume:,.0f}")
                    else:
                        print(f"  Fetched {total_scanned:,} markets (page {page})...")
                    gc.collect()

                if not cursor:
                    break
                self._rate_limit()
        finally:
            scan_session.close()

        if min_volume > 0:
            print(f"  Scan complete: {total_scanned:,} scanned, {len(all_markets):,} kept")
        return all_markets

    def fetch_kalshi_markets_via_events(
        self,
        min_volume: float = 0,
        max_pages: int = 0,
    ) -> List[Dict[str, Any]]:
        """Fetch Kalshi markets via the events endpoint (much faster).

        The /events endpoint with nested markets returns high-volume political,
        economic, and other popular markets immediately, avoiding the need to
        scan through 100k+ low-volume sports parlays via /markets.

        Typically ~15 pages (~3000 events) covers all popular markets.

        Args:
            min_volume: Minimum total volume (contracts) to include market.
            max_pages: Maximum pages to scan (0 = all, ~15 covers popular markets).

        Returns:
            List of market dicts, each enriched with event_ticker, series_ticker, category.
        """
        all_markets: List[Dict[str, Any]] = []
        cursor = ""
        page = 0
        total_events = 0
        scan_start = time.time()

        # Use a fresh session to avoid any stale connection state
        scan_session = requests.Session()
        scan_session.headers.update({"User-Agent": "PredictionMarketResearch/1.0"})

        try:
            while True:
                if max_pages > 0 and page >= max_pages:
                    print(f"  Reached max pages ({max_pages}), stopping scan")
                    break

                params: Dict[str, Any] = {
                    "limit": 200,
                    "with_nested_markets": "true",
                }
                if cursor:
                    params["cursor"] = cursor

                try:
                    t_req = time.time()
                    response = scan_session.get(
                        f"{KALSHI_API}/events", params=params, timeout=30,
                    )
                    req_time = time.time() - t_req
                    if response.status_code == 429:
                        print(f"  Page {page}: rate-limited, sleeping 3s")
                        response.close()
                        time.sleep(3)
                        continue
                    response.raise_for_status()
                    data = response.json()
                    response.close()
                except requests.RequestException as e:
                    print(f"  Page {page}: error: {e}")
                    break

                events = data.get("events", [])
                cursor = data.get("cursor", "")
                del data

                if not events:
                    break

                page_kept = 0
                for event in events:
                    event_ticker = event.get("event_ticker", "")
                    series_ticker = event.get("series_ticker", "")
                    category = event.get("category", "")
                    nested_markets = event.get("markets", [])

                    for m in nested_markets:
                        vol = m.get("volume", 0) or 0
                        if min_volume > 0 and vol < min_volume:
                            continue
                        m["event_ticker"] = event_ticker
                        m["series_ticker"] = series_ticker
                        m["category"] = category
                        all_markets.append(m)
                        page_kept += 1

                total_events += len(events)
                page += 1

                # Print every page with ETA
                elapsed = time.time() - t_req  # use cumulative later
                total_elapsed = time.time() - scan_start
                avg_per_page = total_elapsed / page if page > 0 else 0
                if max_pages > 0:
                    pct = page / max_pages * 100
                    pages_left = max_pages - page
                    eta = pages_left * avg_per_page
                    print(f"  Page {page}/{max_pages} ({pct:.0f}%) "
                          f"{len(events)} events, +{page_kept} mkts "
                          f"(total={len(all_markets):,}), "
                          f"{req_time:.1f}s/req, ETA {eta:.0f}s")
                else:
                    print(f"  Page {page}: {len(events)} events, "
                          f"+{page_kept} mkts (total={len(all_markets):,}), "
                          f"{req_time:.1f}s/req, {total_elapsed:.0f}s elapsed")

                if not cursor:
                    break
                time.sleep(0.1)  # gentle rate limit
        finally:
            scan_session.close()

        print(f"  Events scan complete: {total_events:,} events, "
              f"{len(all_markets):,} markets"
              + (f" with volume >= {min_volume:,.0f}" if min_volume > 0 else ""))
        return all_markets

    def fetch_kalshi_trades(
        self,
        ticker: Optional[str] = None,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None,
        limit: int = 1000,
        max_trades: int = 0,
    ) -> List[Dict[str, Any]]:
        """Fetch trades from Kalshi API (paginated, no auth, thread-safe)."""
        session = self._get_session()
        all_trades: List[Dict[str, Any]] = []
        cursor = ""

        while True:
            params: Dict[str, Any] = {"limit": min(limit, 1000)}
            if cursor:
                params["cursor"] = cursor
            if ticker:
                params["ticker"] = ticker
            if min_ts is not None:
                params["min_ts"] = min_ts
            if max_ts is not None:
                params["max_ts"] = max_ts

            try:
                response = session.get(
                    f"{KALSHI_API}/markets/trades", params=params, timeout=60,
                )
                if response.status_code == 429:
                    time.sleep(2)
                    continue
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                print(f"  Error fetching Kalshi trades ({ticker}): {e}")
                break

            trades = data.get("trades", [])
            if not trades:
                break

            all_trades.extend(trades)
            if max_trades > 0 and len(all_trades) >= max_trades:
                all_trades = all_trades[:max_trades]
                break

            cursor = data.get("cursor", "")
            if not cursor:
                break
            time.sleep(CLOB_REQUEST_DELAY)

        return all_trades

    def fetch_kalshi_candlesticks(
        self,
        series_ticker: str,
        ticker: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        period_interval: int = 60,
    ) -> List[Dict[str, Any]]:
        """Fetch candlestick price data for a Kalshi market (thread-safe).

        Args:
            series_ticker: Series ticker containing the market
            ticker: Market ticker
            start_ts: Start unix timestamp (default: 1 year ago)
            end_ts: End unix timestamp (default: now)
            period_interval: Candle size in minutes: 1, 60, or 1440 (default: 60 = hourly)

        Returns:
            List of candlestick dicts with OHLCV data
        """
        session = self._get_session()
        now = int(time.time())
        if end_ts is None:
            end_ts = now
        if start_ts is None:
            start_ts = now - (365 * 24 * 60 * 60)

        try:
            response = session.get(
                f"{KALSHI_API}/series/{series_ticker}/markets/{ticker}/candlesticks",
                params={
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "period_interval": period_interval,
                },
                timeout=60,
            )
            if response.status_code == 429:
                time.sleep(2)
                return self.fetch_kalshi_candlesticks(
                    series_ticker, ticker, start_ts, end_ts, period_interval
                )
            response.raise_for_status()
            data = response.json()
            return data.get("candlesticks", [])
        except requests.RequestException:
            return []

    def _fetch_single_kalshi_market(
        self,
        market_row: Dict[str, Any],
        progress: Dict[str, int],
        lock: threading.Lock,
        monthly_trades: Dict[str, List[Dict[str, Any]]],
        monthly_prices: Dict[str, List[Dict[str, Any]]],
        max_trades_per_market: int,
        fetch_trades: bool,
        fetch_prices: bool,
    ) -> bool:
        """Fetch trades + candlesticks for a single market (thread-safe worker)."""
        ticker = market_row["ticker"]
        series_ticker = market_row.get("series_ticker", "")
        event_ticker = market_row.get("event_ticker", "")
        market_trades = 0
        market_candles = 0

        try:
            # ---- Trades ----
            if fetch_trades:
                trades = self.fetch_kalshi_trades(
                    ticker=ticker, max_trades=max_trades_per_market,
                )
                for t in trades:
                    created_time = t.get("created_time", "")
                    # Offset-aware parse; month bucketed in UTC.
                    ts_parsed = parse_created_time_to_unix(created_time)
                    if ts_parsed is None:
                        month = "unknown"
                        ts_unix = 0
                    else:
                        ts_unix = ts_parsed
                        month = pd.Timestamp(ts_unix, unit="s", tz="UTC").strftime("%Y-%m")

                    yes_price = (t.get("yes_price", 0) or 0) / 100.0
                    no_price = (t.get("no_price", 0) or 0) / 100.0

                    row = {
                        "trade_id": t.get("trade_id", ""),
                        "ticker": t.get("ticker", ""),
                        "event_ticker": event_ticker,
                        "yes_price": yes_price,
                        "no_price": no_price,
                        "count": t.get("count", 0) or 0,
                        "taker_side": t.get("taker_side", ""),
                        "created_time": created_time,
                        "timestamp_unix": ts_unix,
                    }
                    with lock:
                        monthly_trades[month].append(row)

                market_trades = len(trades)

            # ---- Candlestick prices (hourly OHLCV) ----
            if fetch_prices and series_ticker:
                candles = self.fetch_kalshi_candlesticks(
                    series_ticker=series_ticker,
                    ticker=ticker,
                    period_interval=1440,  # daily (more reliable than hourly)
                )
                n_ok = 0
                for c in candles:
                    # candle_to_row: end_period_ts parsed (int/ISO), stamped at period END,
                    # empty candles -> NaN prices (not 0), unusable timestamps skipped.
                    row = candle_to_row(c, ticker, event_ticker)
                    if row is None:
                        continue
                    month = pd.Timestamp(row["timestamp"], unit="s", tz="UTC").strftime("%Y-%m")
                    with lock:
                        monthly_prices[month].append(row)
                    n_ok += 1

                market_candles = n_ok

        except Exception as e:
            with lock:
                progress["errors"] += 1
            return False

        with lock:
            progress["done"] += 1
            progress["trades"] += market_trades
            progress["candles"] += market_candles
            done = progress["done"]
            total = progress["total"]
            if done % 10 == 0 or done == total:
                elapsed = time.time() - progress["start_time"]
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                pct = done / total * 100 if total > 0 else 0
                print(
                    f"  [{done}/{total}] {pct:.0f}%  "
                    f"trades={progress['trades']:,}  candles={progress['candles']:,}  "
                    f"errors={progress['errors']}  "
                    f"{rate:.1f} mkts/s  ETA {eta:.0f}s"
                )

        return True

    def fetch_kalshi_data(
        self,
        min_volume: float = 0,
        include_settled: bool = True,
        fetch_trades: bool = True,
        fetch_prices: bool = True,
        max_trades_per_market: int = 10000,
        workers: int = MAX_WORKERS,
        flush_interval: int = 10,
        max_event_pages: int = 0,
    ) -> Dict[str, int]:
        """
        Fetch Kalshi events, markets, trades, and price candlesticks.
        Saves to data/kalshi/ as parquet. Parallelized with checkpoints.

        Uses the events endpoint (with nested markets) for fast discovery
        of high-volume markets, rather than scanning 100k+ /markets pages.

        No authentication required — all endpoints are public market data.

        Args:
            min_volume: Minimum total volume (contracts) to include market
            include_settled: Include settled markets
            fetch_trades: Fetch individual trade history per market
            fetch_prices: Fetch hourly candlestick price history per market
            max_trades_per_market: Max trades to fetch per market (default 10000)
            workers: Concurrent worker threads (default: 20)
            flush_interval: Flush to disk every N markets (default: 50)
            max_event_pages: Max event pages to scan (0=all, ~15 covers popular markets)

        Returns:
            Dict with counts of events, markets, trades, candles
        """
        self._require_pyarrow()
        import pyarrow as pa
        import pyarrow.parquet as pq

        t0 = time.time()
        kalshi_dir = self.data_dir / "kalshi"
        kalshi_dir.mkdir(parents=True, exist_ok=True)
        trades_dir = kalshi_dir / "trades"
        trades_dir.mkdir(parents=True, exist_ok=True)
        prices_dir = kalshi_dir / "prices"
        prices_dir.mkdir(parents=True, exist_ok=True)

        # ---- Checkpoint ----
        checkpoint_path = kalshi_dir / "fetch_checkpoint.json"
        processed_tickers: Set[str] = set()
        if checkpoint_path.exists():
            try:
                with open(checkpoint_path, "r") as f:
                    ckpt = json.load(f)
                    processed_tickers = set(ckpt.get("processed_tickers", []))
                    if processed_tickers:
                        print(f"  Resuming: {len(processed_tickers):,} markets already processed")
            except (json.JSONDecodeError, IOError):
                pass

        # ================================================================
        # Step 1: Fetch markets via events (fast: finds high-volume
        # markets immediately instead of scanning 100k+ sports parlays)
        # ================================================================
        print(f"\n[Kalshi 1/2] Fetching markets via events (volume >= {min_volume:,.0f})...")
        raw_markets = self.fetch_kalshi_markets_via_events(
            min_volume=min_volume, max_pages=max_event_pages,
        )
        print(f"  Got {len(raw_markets):,} qualifying markets in {time.time()-t0:.1f}s")

        if not raw_markets:
            print("  No markets meeting volume threshold.")
            print("  Tip: Kalshi volumes are in contracts. Try --kalshi-min-volume 1000")
            return {"events": 0, "markets": 0, "trades": 0, "candles": 0}

        markets_rows = []
        event_meta: Dict[str, Dict[str, str]] = {}
        for m in raw_markets:
            vol = m.get("volume", 0) or 0
            vol_24h = m.get("volume_24h", 0) or 0
            et = m.get("event_ticker", "")
            st = m.get("series_ticker", "")
            cat = m.get("category", "")
            markets_rows.append({
                "ticker": m.get("ticker", ""),
                "event_ticker": et,
                "series_ticker": st,
                "market_type": m.get("market_type", ""),
                "title": str(m.get("title", ""))[:200],
                "subtitle": str(m.get("subtitle", ""))[:200],
                "status": m.get("status", ""),
                "result": m.get("result", ""),
                "yes_bid": m.get("yes_bid"),
                "yes_ask": m.get("yes_ask"),
                "no_bid": m.get("no_bid"),
                "no_ask": m.get("no_ask"),
                "last_price": m.get("last_price"),
                "volume": int(vol),
                "volume_24h": int(vol_24h),
                "open_interest": m.get("open_interest", 0) or 0,
                "liquidity": m.get("liquidity", 0) or 0,
                "created_time": m.get("created_time", ""),
                "open_time": m.get("open_time", ""),
                "close_time": m.get("close_time", ""),
                "expiration_time": m.get("expiration_time", ""),
                "settlement_value": m.get("settlement_value"),
                "category": cat,
            })
            # Track event metadata (already available from events endpoint)
            if et and et not in event_meta:
                event_meta[et] = {"series_ticker": st, "category": cat}
        del raw_markets

        markets_df = pd.DataFrame(markets_rows)
        pq.write_table(
            pa.Table.from_pandas(markets_df, preserve_index=False),
            kalshi_dir / "markets.parquet", compression="zstd", compression_level=3,
        )
        print(f"  Saved {len(markets_df):,} markets with series_tickers already resolved")

        # Stats
        if not markets_df.empty:
            status_counts = markets_df["status"].value_counts()
            print(f"  Saved {len(markets_df):,} markets")
            for s, c in status_counts.items():
                print(f"    {s}: {c:,}")
            vol_markets = markets_df[markets_df["volume"] > 0]
            print(f"  Markets with volume > 0: {len(vol_markets):,}")
            if not vol_markets.empty:
                print(f"  Total volume: {vol_markets['volume'].sum():,.0f} contracts")

        # ================================================================
        # Filter markets for trade/price fetching
        # ================================================================
        target_markets = markets_df[markets_df["volume"] >= min_volume].copy()
        if not include_settled:
            target_markets = target_markets[~target_markets["status"].isin(["settled"])]
        target_markets = target_markets.sort_values("volume", ascending=False)

        # Remove already-processed
        remaining = target_markets[~target_markets["ticker"].isin(processed_tickers)]
        already = len(target_markets) - len(remaining)

        do_detail = (fetch_trades or fetch_prices) and not remaining.empty
        if not do_detail:
            result = {
                "events": len(event_meta), "markets": len(markets_df),
                "trades": 0, "candles": 0,
            }
            print(f"\n[Kalshi] Complete in {time.time()-t0:.1f}s")
            for k, v in result.items():
                print(f"  {k}: {v:,}")
            return result

        print(f"\n[Kalshi 2/2] Fetching trades & prices for {len(remaining):,} markets "
              f"(volume >= {min_volume:,.0f}, {already} already done)")
        print(f"  Workers: {workers}  Flush interval: {flush_interval}")

        # ================================================================
        # Parallel fetch trades + candlesticks
        # ================================================================
        lock = threading.Lock()
        monthly_trades: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        monthly_prices: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        progress = {
            "done": 0, "total": len(remaining), "trades": 0,
            "candles": 0, "errors": 0, "start_time": time.time(),
        }

        market_dicts = remaining.to_dict("records")
        markets_since_flush = 0

        try:
            for batch_start in range(0, len(market_dicts), workers):
                batch = market_dicts[batch_start:batch_start + workers]

                with ThreadPoolExecutor(max_workers=min(workers, len(batch))) as executor:
                    futures = {
                        executor.submit(
                            self._fetch_single_kalshi_market,
                            m, progress, lock,
                            monthly_trades, monthly_prices,
                            max_trades_per_market, fetch_trades, fetch_prices,
                        ): m
                        for m in batch
                    }
                    for future in as_completed(futures):
                        m = futures[future]
                        try:
                            future.result()
                            processed_tickers.add(m["ticker"])
                            markets_since_flush += 1
                        except Exception as e:
                            with lock:
                                progress["errors"] += 1

                # Flush periodically
                with lock:
                    total_rows = (
                        sum(len(v) for v in monthly_trades.values()) +
                        sum(len(v) for v in monthly_prices.values())
                    )
                if markets_since_flush >= flush_interval or total_rows > 100_000:
                    self._flush_kalshi_trades(monthly_trades, trades_dir, lock)
                    self._flush_kalshi_prices(monthly_prices, prices_dir, lock)
                    self._save_kalshi_checkpoint(checkpoint_path, processed_tickers)
                    markets_since_flush = 0

        except KeyboardInterrupt:
            print("\n  Interrupted! Flushing shards...")
            self._flush_kalshi_trades(monthly_trades, trades_dir, lock)
            self._flush_kalshi_prices(monthly_prices, prices_dir, lock)
            self._save_kalshi_checkpoint(checkpoint_path, processed_tickers)
            # Combine shards written so far
            print("  Combining shards written so far...")
            if fetch_trades:
                self._combine_kalshi_shards(
                    trades_dir, "trades", dedup_cols=["trade_id"], sort_col="timestamp_unix",
                )
            if fetch_prices:
                self._combine_kalshi_shards(
                    prices_dir, "prices", dedup_cols=["ticker", "timestamp"], sort_col="timestamp",
                )
            print(f"  Checkpoint saved ({len(processed_tickers):,} markets). Re-run to resume.")
            raise

        # Final flush
        self._flush_kalshi_trades(monthly_trades, trades_dir, lock)
        self._flush_kalshi_prices(monthly_prices, prices_dir, lock)
        self._save_kalshi_checkpoint(checkpoint_path, processed_tickers)

        # Combine shards into final monthly files
        print(f"\n  Combining shards...")
        if fetch_trades:
            trade_rows = self._combine_kalshi_shards(
                trades_dir, "trades", dedup_cols=["trade_id"], sort_col="timestamp_unix",
            )
            print(f"  Trades: {trade_rows:,} rows total")
        if fetch_prices:
            price_rows = self._combine_kalshi_shards(
                prices_dir, "prices", dedup_cols=["ticker", "timestamp"], sort_col="timestamp",
            )
            print(f"  Prices: {price_rows:,} rows total")

        elapsed = time.time() - t0
        result = {
            "events": len(event_meta),
            "markets": len(markets_df),
            "trades": progress["trades"],
            "candles": progress["candles"],
        }

        print(f"\n[Kalshi] Complete in {elapsed:.1f}s")
        print(f"  Events:  {result['events']:,}")
        print(f"  Markets: {result['markets']:,}")
        print(f"  Trades:  {result['trades']:,}")
        print(f"  Candles: {result['candles']:,}")
        print(f"  Errors:  {progress['errors']}")
        print(f"  Data:    {kalshi_dir}")

        return result

    def _flush_kalshi_trades(
        self,
        monthly_trades: Dict[str, List[Dict[str, Any]]],
        trades_dir: Path,
        lock: Optional[threading.Lock] = None,
    ) -> None:
        """Flush accumulated Kalshi trades as shard files (fast, no read-back)."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        if lock:
            with lock:
                data = {k: list(v) for k, v in monthly_trades.items()}
                monthly_trades.clear()
        else:
            data = dict(monthly_trades)
            monthly_trades.clear()

        shard_id = int(time.time() * 1000) % 1_000_000_000
        for month, rows in data.items():
            if not rows:
                continue
            df = pd.DataFrame(rows)
            shard_path = trades_dir / f"trades_{month}_shard{shard_id}.parquet"
            table = pa.Table.from_pandas(df, preserve_index=False)
            pq.write_table(table, shard_path, compression="zstd", compression_level=3)

    def _flush_kalshi_prices(
        self,
        monthly_prices: Dict[str, List[Dict[str, Any]]],
        prices_dir: Path,
        lock: Optional[threading.Lock] = None,
    ) -> None:
        """Flush accumulated Kalshi prices as shard files (fast, no read-back)."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        if lock:
            with lock:
                data = {k: list(v) for k, v in monthly_prices.items()}
                monthly_prices.clear()
        else:
            data = dict(monthly_prices)
            monthly_prices.clear()

        shard_id = int(time.time() * 1000) % 1_000_000_000
        for month, rows in data.items():
            if not rows:
                continue
            df = pd.DataFrame(rows)
            shard_path = prices_dir / f"prices_{month}_shard{shard_id}.parquet"
            table = pa.Table.from_pandas(df, preserve_index=False)
            pq.write_table(table, shard_path, compression="zstd", compression_level=3)

    def _combine_kalshi_shards(
        self,
        target_dir: Path,
        prefix: str,
        dedup_cols: List[str],
        sort_col: str,
    ) -> int:
        """Combine shard parquet files into final monthly files, then delete shards.

        Returns total rows written.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        # Group shards by month: prefix_YYYY-MM_shardNNN.parquet -> YYYY-MM
        shard_files: Dict[str, List[Path]] = defaultdict(list)
        for f in target_dir.glob(f"{prefix}_*_shard*.parquet"):
            stem = f.stem  # e.g. prices_2025-06_shard123456
            month = stem.split("_shard")[0].replace(f"{prefix}_", "")
            shard_files[month].append(f)

        if not shard_files:
            return 0

        total_rows = 0
        for month, shards in sorted(shard_files.items()):
            final_path = target_dir / f"{prefix}_{month}.parquet"

            dfs = []
            # Include existing final file if present
            if final_path.exists():
                try:
                    dfs.append(pd.read_parquet(final_path))
                except Exception:
                    pass
            for shard in shards:
                try:
                    dfs.append(pd.read_parquet(shard))
                except Exception:
                    pass

            if not dfs:
                continue

            combined = pd.concat(dfs, ignore_index=True)
            combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            combined = combined.sort_values(sort_col).reset_index(drop=True)

            pq.write_table(
                pa.Table.from_pandas(combined, preserve_index=False),
                final_path, compression="zstd", compression_level=3,
            )
            total_rows += len(combined)

            # Delete shards
            for shard in shards:
                try:
                    shard.unlink()
                except OSError:
                    pass

            print(f"    {prefix}_{month}.parquet: {len(combined):,} rows "
                  f"(from {len(shards)} shards)")

        return total_rows

    def _save_kalshi_checkpoint(self, checkpoint_path: Path, processed_tickers: Set[str]) -> None:
        """Save Kalshi fetch checkpoint."""
        try:
            ckpt = {
                "processed_tickers": sorted(list(processed_tickers)),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "count": len(processed_tickers),
            }
            tmp = checkpoint_path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(ckpt, f, indent=2)
            tmp.replace(checkpoint_path)
        except IOError as e:
            print(f"  Warning: Could not save checkpoint: {e}")

    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================

    def fetch_all(
        self,
        manifold_bets: int = 5000000,
        manifold_markets: int = 200000,
        polymarket_markets: int = 200000,
        polymarket_clob: bool = False,
        clob_min_volume: float = 10000,
        clob_max_markets: int = 500,
    ) -> dict:
        """
        Fetch all data from all sources.

        Args:
            manifold_bets: Max Manifold bets to fetch
            manifold_markets: Max Manifold markets to fetch
            polymarket_markets: Max Polymarket markets to fetch
            polymarket_clob: Whether to fetch CLOB price history
            clob_min_volume: Min 24hr volume for CLOB markets
            clob_max_markets: Max markets for CLOB history

        Returns:
            Dictionary with counts of fetched data
        """
        print("=" * 60)
        print("FETCHING PREDICTION MARKET DATA")
        print("=" * 60)

        results = {}

        results["manifold_bets"] = self.fetch_manifold_bets(manifold_bets)
        results["manifold_markets"] = self.fetch_manifold_markets(manifold_markets)
        results["polymarket_markets"] = self.fetch_polymarket_markets(polymarket_markets)

        if polymarket_clob:
            results["polymarket_clob"] = self.fetch_polymarket_clob_data(
                min_volume=clob_min_volume,
                max_markets=clob_max_markets,
            )

        print("\n" + "=" * 60)
        print("FETCH COMPLETE")
        print("=" * 60)
        print(f"  Manifold bets: {results['manifold_bets']:,}")
        print(f"  Manifold markets: {results['manifold_markets']:,}")
        print(f"  Polymarket markets: {results['polymarket_markets']:,}")
        if polymarket_clob:
            print(f"  Polymarket CLOB: {results.get('polymarket_clob', 0):,} markets")

        return results

    def data_exists(self) -> dict:
        """Check what data already exists locally."""
        manifold_dir = self.data_dir / "manifold"
        polymarket_dir = self.data_dir / "polymarket"
        polymarket_clob_dir = self.data_dir / "polymarket_clob"
        order_filled_dir = Path("data/polymarket/order_filled_events")
        trades_parquet_dir = Path("data/polymarket/trades")

        return {
            "manifold_bets": list(manifold_dir.glob("bets_*.json")) if manifold_dir.exists() else [],
            "manifold_markets": list(manifold_dir.glob("markets_*.json")) if manifold_dir.exists() else [],
            "polymarket_markets": list(polymarket_dir.glob("markets_*.json")) if polymarket_dir.exists() else [],
            "polymarket_clob": list(polymarket_clob_dir.glob("market_*.json")) if polymarket_clob_dir.exists() else [],
            "polymarket_order_filled": list(order_filled_dir.glob("*.parquet")) if order_filled_dir.exists() else [],
            "polymarket_trades_parquet": list(trades_parquet_dir.glob("trades_*.parquet")) if trades_parquet_dir.exists() else [],
        }


def ensure_data_exists(data_dir: str = "data", min_bets: int = 10000) -> bool:
    """
    Ensure data exists, fetching if necessary.

    Args:
        data_dir: Data directory
        min_bets: Minimum bets required before fetching

    Returns:
        True if data is available
    """
    fetcher = DataFetcher(data_dir)
    existing = fetcher.data_exists()

    if not existing["manifold_bets"]:
        print("No Manifold bets found. Fetching data...")
        fetcher.fetch_all()
        return True

    # Count existing bets
    total_bets = 0
    for f in existing["manifold_bets"]:
        try:
            with open(f, encoding="utf-8") as file:
                total_bets += len(json.load(file))
        except Exception:
            pass

    if total_bets < min_bets:
        print(f"Only {total_bets:,} bets found (need {min_bets:,}). Fetching more...")
        fetcher.fetch_all()

    return True


if __name__ == "__main__":
    import sys

    fetcher = DataFetcher()

    if len(sys.argv) > 1 and sys.argv[1] == "--clob":
        # Fetch CLOB price history
        print("Fetching Polymarket CLOB price history...")
        min_vol = float(sys.argv[2]) if len(sys.argv) > 2 else 10000
        max_mkts_str = sys.argv[3] if len(sys.argv) > 3 else "None"
        max_mkts = None if max_mkts_str.lower() == "none" else int(max_mkts_str)
        max_days = int(sys.argv[4]) if len(sys.argv) > 4 else 1180
        include_closed = True
        if len(sys.argv) > 5:
            include_closed = str(sys.argv[5]).strip().lower() in {"1", "true", "yes", "y", "on"}
        markets_parquet = sys.argv[6] if len(sys.argv) > 6 else None
        
        fetcher.fetch_polymarket_clob_data(
            min_volume=min_vol,
            max_markets=max_mkts if not markets_parquet else None,
            max_days=max_days,
            include_closed=include_closed,
            markets_parquet_path=markets_parquet,
        )
    else:
        # Show existing data
        existing = fetcher.data_exists()
        print("Existing data:")
        for key, files in existing.items():
            print(f"  {key}: {len(files)} files")
        print(
            "\nTo fetch CLOB data: python -m src.trading.data_modules.fetcher "
            "--clob [min_volume] [max_markets] [max_days] [include_closed] [markets_parquet_path]"
        )
        print(
            "  Example: python -m src.trading.data_modules.fetcher --clob 10000 None 1180 true data/polymarket/markets.parquet"
        )
