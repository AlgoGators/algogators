"""Daily check that the market-data pipelines actually ran.

    python -m data_ngin.ops.check_data_freshness

Runs from the box crontab inside the Airflow image (GitHub-hosted runners
cannot reach the private database). For every pipeline config packaged with
data_ngin it reads MAX(time) from that pipeline's target table, in that
pipeline's database, so a new pipeline is monitored without editing this file.

Exit status is 0 when every table is fresh, 1 when any table is stale, empty,
or could not be queried (including an unreachable database). On failure it
files or updates a `data-freshness` GitHub issue (see github_issues).

The threshold defaults to 72 hours, not 24: OHLCV data does not update on
weekends or holidays, so 24h would false-alarm every Saturday and Sunday after
a normal Friday close. Override with STALE_THRESHOLD_HOURS.
"""

import logging
import os
import sys
from datetime import UTC, date, datetime, time, timedelta

from data_ngin.ops.github_issues import file_or_update_issue, github_settings

logger = logging.getLogger(__name__)

ISSUE_LABEL = "data-freshness"
ISSUE_TITLE = "[data-freshness] Market data is stale"
CONFIG_FILES = ("config.yaml", "new_config.yaml", "config_tiingo.yaml")
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")


def monitored_tables(config_dir: str = CONFIG_DIR) -> list[tuple[str | None, str]]:
    """(db_name, "schema.table") for each packaged pipeline config, de-duplicated,
    in config order. db_name None means the DB_NAME env database."""
    from data_ngin.utils.dynamic_loader import load_config

    tables: list[tuple[str | None, str]] = []
    for name in CONFIG_FILES:
        database = load_config(os.path.join(config_dir, name))["database"]
        entry = (database.get("db_name"), f"{database['target_schema']}.{database['table']}")
        if entry not in tables:
            tables.append(entry)
    return tables


def get_latest_timestamps(tables: list[tuple[str | None, str]]) -> dict[str, object]:
    """
    {"db/schema.table": latest timestamp, None if empty, or the Exception raised
    while connecting or querying}. One connection per database.
    """
    import psycopg2
    from platform_db import DatabaseConfig

    latest: dict[str, object] = {}
    by_db: dict[str | None, list[str]] = {}
    for db_name, table in tables:
        by_db.setdefault(db_name, []).append(table)

    for db_name, db_tables in by_db.items():
        env = dict(os.environ)
        if db_name:
            env["DB_NAME"] = db_name
        label = env.get("DB_NAME") or "<DB_NAME unset>"
        try:
            settings = DatabaseConfig.from_env(env)
            conn = psycopg2.connect(connect_timeout=10, **settings.connect_kwargs())
        except Exception as e:
            for table in db_tables:
                latest[f"{label}/{table}"] = e
            continue
        try:
            for table in db_tables:
                key = f"{label}/{table}"
                try:
                    with conn.cursor() as cur:
                        cur.execute(f"SELECT MAX(time) FROM {table}")
                        row = cur.fetchone()
                        latest[key] = row[0] if row else None
                except Exception as e:
                    conn.rollback()
                    latest[key] = e
        finally:
            conn.close()
    return latest


def _as_utc_datetime(ts: datetime | date) -> datetime:
    """Normalize a DB timestamp (naive or aware datetime, or a plain date) to UTC."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    if isinstance(ts, date):
        return datetime.combine(ts, time.min, tzinfo=UTC)
    raise TypeError(f"Unsupported timestamp type: {type(ts)!r}")


def find_stale_tables(
    latest: dict[str, object], now: datetime, threshold_hours: int
) -> list[tuple[str, str]]:
    """Pure: (table, reason) for every table that errored, is empty, or is older
    than threshold_hours."""
    stale = []
    for table, ts in latest.items():
        if isinstance(ts, Exception):
            stale.append((table, f"could not be checked: {type(ts).__name__}: {ts}"))
        elif ts is None:
            stale.append((table, "no rows found"))
        else:
            age = now - _as_utc_datetime(ts)
            if age > timedelta(hours=threshold_hours):
                stale.append((table, f"latest row is {age} old (threshold {threshold_hours}h)"))
    return stale


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    threshold_hours = int(os.environ.get("STALE_THRESHOLD_HOURS", "72"))

    latest = get_latest_timestamps(monitored_tables())
    for table, ts in latest.items():
        logger.info("%s: latest=%s", table, ts)

    stale = find_stale_tables(latest, datetime.now(UTC), threshold_hours)
    if not stale:
        logger.info("OK: all tables within the %sh freshness threshold.", threshold_hours)
        return 0

    for table, reason in stale:
        logger.error("STALE %s: %s", table, reason)

    token, repo = github_settings()
    if token:
        body = "\n".join(f"- `{table}`: {reason}" for table, reason in stale)
        try:
            number = file_or_update_issue(
                repo=repo,
                token=token,
                label=ISSUE_LABEL,
                title=ISSUE_TITLE,
                title_query="Market data is stale",
                body=body,
            )
            logger.info("Recorded on issue #%s", number)
        except Exception:
            logger.exception("Failed to file/update the data-freshness issue")
    else:
        logger.warning("GITHUB_TOKEN not set; skipping issue filing.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
