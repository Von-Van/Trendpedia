"""Collection logic: pull days of top-article data into SQLite.

Two modes matter:
  * `collect_day`      — the daily cron job.
  * `backfill_range`   — bootstrap months of history in one run, because the
                         `top` endpoint serves historical dates. This is what
                         makes the dashboard useful on day one instead of in
                         three months.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import database as db
from .config import COLLECTION_LAG_DAYS, DB_PATH, TOP_N
from .wiki_api import DataNotAvailable, PageviewsError, WikiPageviewsClient, daterange

log = logging.getLogger(__name__)


@dataclass
class CollectionResult:
    requested: int = 0
    collected: int = 0
    skipped: int = 0
    failed: int = 0
    rows: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (f"{self.collected} day(s) collected, {self.skipped} already present, "
                f"{self.failed} failed, {self.rows:,} rows written")


def latest_available_date(today: date | None = None) -> date:
    """Most recent day the API is likely to have finished publishing."""
    today = today or datetime.now(timezone.utc).date()
    return today - timedelta(days=COLLECTION_LAG_DAYS)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def collect_day(day: date, client: WikiPageviewsClient | None = None,
                db_path: str | Path = DB_PATH, limit: int = TOP_N) -> int:
    """Fetch and store one day of top articles. Returns rows written."""
    client = client or WikiPageviewsClient()
    day_str = day.isoformat()
    try:
        articles = client.top_articles(day, limit=limit)
    except DataNotAvailable as exc:
        with db.session(db_path) as conn:
            db.log_run(conn, _utcnow(), day_str, "no_data", 0, str(exc)[:300])
        raise
    except PageviewsError as exc:
        with db.session(db_path) as conn:
            db.log_run(conn, _utcnow(), day_str, "error", 0, str(exc)[:300])
        raise

    with db.session(db_path) as conn:
        id_map = db.upsert_articles(conn, [a["article"] for a in articles], day_str)
        rows = [(id_map[a["article"]], day_str, a["views"], a["rank"])
                for a in articles if a["article"] in id_map]
        written = db.insert_pageviews(conn, rows)
        db.log_run(conn, _utcnow(), day_str, "ok", written)
        db.set_meta(conn, "last_collection", _utcnow())
    log.info("Collected %s: %d articles", day_str, written)
    return written


def existing_dates(db_path: str | Path = DB_PATH) -> set[str]:
    return set(db.collected_dates(db_path))


def collect_range(start: date, end: date, db_path: str | Path = DB_PATH,
                  client: WikiPageviewsClient | None = None,
                  skip_existing: bool = True, limit: int = TOP_N,
                  progress=None) -> CollectionResult:
    """Collect every day in [start, end]. Idempotent by default.

    `progress` is an optional callable(done, total, day_str) for CLI/UI feedback.
    """
    db.init_db(db_path)
    client = client or WikiPageviewsClient()
    have = existing_dates(db_path) if skip_existing else set()
    days = list(daterange(start, end))
    result = CollectionResult(requested=len(days))

    for i, day in enumerate(days, start=1):
        day_str = day.isoformat()
        if day_str in have:
            result.skipped += 1
        else:
            try:
                result.rows += collect_day(day, client=client, db_path=db_path, limit=limit)
                result.collected += 1
            except DataNotAvailable:
                result.failed += 1
                result.errors.append(f"{day_str}: no data published yet")
            except PageviewsError as exc:
                result.failed += 1
                result.errors.append(f"{day_str}: {exc}")
        if progress:
            progress(i, len(days), day_str)
    return result


def backfill_range(days: int, end: date | None = None, **kwargs) -> CollectionResult:
    """Bootstrap the dataset with the last `days` days of top articles."""
    end = end or latest_available_date()
    start = end - timedelta(days=days - 1)
    return collect_range(start, end, **kwargs)


def backfill_article(title: str, start: date, end: date,
                     db_path: str | Path = DB_PATH,
                     client: WikiPageviewsClient | None = None) -> int:
    """Fetch one article's *true* daily series and fill in its gaps.

    The `top` endpoint only reports an article on days it cracked the top 1000,
    so stored series have holes. This closes them with real numbers. Ranks stay
    NULL for days the article was outside the top list — that is accurate.
    """
    client = client or WikiPageviewsClient()
    series = client.article_series(title, start, end)
    if not series:
        return 0
    with db.session(db_path) as conn:
        id_map = db.upsert_articles(conn, [title], series[0]["date"])
        article_id = id_map[title]
        rows = [(article_id, point["date"], point["views"], None) for point in series]
        written = db.insert_pageviews(conn, rows)
        conn.execute(
            """UPDATE articles
                  SET first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?)
                WHERE article_id = ?""",
            (series[0]["date"], series[-1]["date"], article_id),
        )
    return written
