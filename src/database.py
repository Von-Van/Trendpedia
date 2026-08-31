"""SQLite storage for the attention dataset.

Design note: the `pageviews` table only ever holds what the API actually
reported. Gap-filling, smoothing and other modelling choices live in the
analysis layer so the raw record stays trustworthy and re-analysable.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import pandas as pd

from .config import DB_PATH, is_mainspace

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS articles (
    article_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL UNIQUE,
    first_seen   TEXT    NOT NULL,
    last_seen    TEXT    NOT NULL,
    is_mainspace INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pageviews (
    article_id INTEGER NOT NULL REFERENCES articles(article_id) ON DELETE CASCADE,
    date       TEXT    NOT NULL,
    views      INTEGER NOT NULL,
    daily_rank INTEGER,
    PRIMARY KEY (article_id, date)
);
CREATE INDEX IF NOT EXISTS idx_pageviews_date ON pageviews(date);
CREATE INDEX IF NOT EXISTS idx_pageviews_rank ON pageviews(date, daily_rank);

-- Derived per-article/per-day metrics, rebuilt by scripts/rebuild_metrics.py
CREATE TABLE IF NOT EXISTS metrics (
    article_id   INTEGER NOT NULL REFERENCES articles(article_id) ON DELETE CASCADE,
    date         TEXT    NOT NULL,
    ma7          REAL,
    ma28         REAL,
    velocity     REAL,
    acceleration REAL,
    volatility   REAL,
    anomaly_z    REAL,
    baseline_ratio REAL,
    PRIMARY KEY (article_id, date)
);

-- Per-article aggregates, rebuilt alongside metrics
CREATE TABLE IF NOT EXISTS article_summary (
    article_id   INTEGER PRIMARY KEY REFERENCES articles(article_id) ON DELETE CASCADE,
    observations INTEGER,
    total_views  INTEGER,
    mean_views   REAL,
    median_views REAL,
    peak_views   INTEGER,
    peak_date    TEXT,
    latest_views INTEGER,
    best_rank    INTEGER,
    latest_rank  INTEGER,
    peak_ratio   REAL,
    persistence  REAL,
    volatility   REAL
);

-- Cached relationship graph
CREATE TABLE IF NOT EXISTS edges (
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    weight    REAL    NOT NULL,
    PRIMARY KEY (source_id, target_id)
);

CREATE TABLE IF NOT EXISTS communities (
    article_id INTEGER PRIMARY KEY,
    community  INTEGER NOT NULL
);

-- Pipeline observability: every collection attempt is recorded, success or not.
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT NOT NULL,
    target_date TEXT NOT NULL,
    status      TEXT NOT NULL,
    rows_written INTEGER DEFAULT 0,
    message     TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_date ON collection_runs(target_date);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    """Open a connection, creating the parent directory if needed."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(db_path: str | Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Connection context manager that commits on success, rolls back on error."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path = DB_PATH) -> None:
    """Create tables if they do not exist. Safe to call repeatedly."""
    with session(db_path) as conn:
        conn.executescript(SCHEMA)


# --- writes ------------------------------------------------------------------

def upsert_articles(conn: sqlite3.Connection, titles: Iterable[str], date: str) -> dict[str, int]:
    """Ensure every title exists; widen its first_seen/last_seen window.

    Returns a {title: article_id} map for the supplied titles.
    """
    titles = list(dict.fromkeys(titles))  # de-dupe, keep order
    if not titles:
        return {}
    rows = [(t, date, date, int(is_mainspace(t))) for t in titles]
    conn.executemany(
        """
        INSERT INTO articles (title, first_seen, last_seen, is_mainspace)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(title) DO UPDATE SET
            first_seen = MIN(articles.first_seen, excluded.first_seen),
            last_seen  = MAX(articles.last_seen,  excluded.last_seen)
        """,
        rows,
    )
    return _title_id_map(conn, titles)


def _title_id_map(conn: sqlite3.Connection, titles: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    chunk = 900  # stay under SQLite's variable limit
    for i in range(0, len(titles), chunk):
        part = titles[i : i + chunk]
        q = f"SELECT article_id, title FROM articles WHERE title IN ({','.join('?' * len(part))})"
        for row in conn.execute(q, part):
            out[row["title"]] = row["article_id"]
    return out


def insert_pageviews(conn: sqlite3.Connection, rows: Sequence[tuple[int, str, int, int | None]]) -> int:
    """Insert (article_id, date, views, daily_rank) rows.

    Re-collecting a day overwrites it — the API is the source of truth and
    revisions do happen — but history for other days is never touched.
    """
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO pageviews (article_id, date, views, daily_rank)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(article_id, date) DO UPDATE SET
            views = excluded.views,
            daily_rank = COALESCE(excluded.daily_rank, pageviews.daily_rank)
        """,
        rows,
    )
    return len(rows)


def log_run(conn: sqlite3.Connection, run_at: str, target_date: str, status: str,
            rows_written: int = 0, message: str | None = None) -> None:
    conn.execute(
        "INSERT INTO collection_runs (run_at, target_date, status, rows_written, message)"
        " VALUES (?, ?, ?, ?, ?)",
        (run_at, target_date, status, rows_written, message),
    )


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


# --- reads -------------------------------------------------------------------

def read_sql(query: str, params: Sequence = (), db_path: str | Path = DB_PATH) -> pd.DataFrame:
    conn = connect(db_path)
    try:
        return pd.read_sql_query(query, conn, params=list(params))
    finally:
        conn.close()


def collected_dates(db_path: str | Path = DB_PATH) -> list[str]:
    df = read_sql("SELECT DISTINCT date FROM pageviews ORDER BY date", db_path=db_path)
    return df["date"].tolist() if not df.empty else []


def date_bounds(db_path: str | Path = DB_PATH) -> tuple[str | None, str | None]:
    df = read_sql("SELECT MIN(date) AS lo, MAX(date) AS hi FROM pageviews", db_path=db_path)
    if df.empty or pd.isna(df.loc[0, "lo"]):
        return None, None
    return df.loc[0, "lo"], df.loc[0, "hi"]


def has_data(db_path: str | Path = DB_PATH) -> bool:
    if not Path(db_path).exists():
        return False
    try:
        df = read_sql("SELECT 1 FROM pageviews LIMIT 1", db_path=db_path)
        return not df.empty
    except Exception:
        return False


def replace_table(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> None:
    """Swap the full contents of a derived table."""
    conn.execute(f"DELETE FROM {table}")
    if df.empty:
        return
    cols = ",".join(df.columns)
    placeholders = ",".join("?" * len(df.columns))
    conn.executemany(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
        df.itertuples(index=False, name=None),
    )
