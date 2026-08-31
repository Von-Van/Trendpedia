"""Collector tests. No network: a stub client stands in for the API."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src import collector
from src import database as db
from src.wiki_api import DataNotAvailable, PageviewsError


class StubClient:
    """Serves canned top-article payloads and records what was asked for."""

    def __init__(self, missing: set[date] | None = None,
                 broken: set[date] | None = None):
        self.missing = missing or set()
        self.broken = broken or set()
        self.requested: list[date] = []

    def top_articles(self, day: date, limit: int = 1000) -> list[dict]:
        self.requested.append(day)
        if day in self.missing:
            raise DataNotAvailable(f"no data for {day}")
        if day in self.broken:
            raise PageviewsError("upstream exploded")
        return [
            {"article": "Main_Page", "views": 5_000_000, "rank": 1},
            {"article": "Cleopatra", "views": 30_000, "rank": 2},
            {"article": f"Topic_of_{day.isoformat()}", "views": 20_000, "rank": 3},
        ][:limit]


def test_collect_day_writes_articles_and_views(empty_db):
    written = collector.collect_day(date(2026, 6, 1), client=StubClient(), db_path=empty_db)
    assert written == 3
    views = db.read_sql("SELECT * FROM pageviews", db_path=empty_db)
    assert len(views) == 3
    assert set(db.read_sql("SELECT title FROM articles", db_path=empty_db)["title"]) == {
        "Main_Page", "Cleopatra", "Topic_of_2026-06-01"}


def test_collect_day_records_the_run(empty_db):
    collector.collect_day(date(2026, 6, 1), client=StubClient(), db_path=empty_db)
    runs = db.read_sql("SELECT * FROM collection_runs", db_path=empty_db)
    assert runs.iloc[0]["status"] == "ok"
    assert runs.iloc[0]["rows_written"] == 3


def test_collect_range_is_idempotent(empty_db):
    start, end = date(2026, 6, 1), date(2026, 6, 5)
    client = StubClient()
    first = collector.collect_range(start, end, db_path=empty_db, client=client)
    assert first.collected == 5 and first.skipped == 0

    again = collector.collect_range(start, end, db_path=empty_db, client=StubClient())
    assert again.collected == 0 and again.skipped == 5
    assert len(db.read_sql("SELECT * FROM pageviews", db_path=empty_db)) == 15


def test_force_recollect_refetches(empty_db):
    start, end = date(2026, 6, 1), date(2026, 6, 2)
    collector.collect_range(start, end, db_path=empty_db, client=StubClient())
    client = StubClient()
    result = collector.collect_range(start, end, db_path=empty_db, client=client,
                                     skip_existing=False)
    assert result.collected == 2
    assert len(client.requested) == 2


def test_missing_day_is_recorded_not_fatal(empty_db):
    gap = date(2026, 6, 3)
    result = collector.collect_range(date(2026, 6, 1), date(2026, 6, 5),
                                     db_path=empty_db,
                                     client=StubClient(missing={gap}))
    assert result.collected == 4
    assert result.failed == 1
    runs = db.read_sql("SELECT target_date, status FROM collection_runs",
                       db_path=empty_db)
    assert runs[runs["target_date"] == gap.isoformat()].iloc[0]["status"] == "no_data"


def test_upstream_error_does_not_abort_the_run(empty_db):
    result = collector.collect_range(date(2026, 6, 1), date(2026, 6, 4),
                                     db_path=empty_db,
                                     client=StubClient(broken={date(2026, 6, 2)}))
    assert result.collected == 3 and result.failed == 1
    assert db.collected_dates(empty_db) == ["2026-06-01", "2026-06-03", "2026-06-04"]


def test_top_n_limit_is_respected(empty_db):
    collector.collect_day(date(2026, 6, 1), client=StubClient(), db_path=empty_db, limit=2)
    assert len(db.read_sql("SELECT * FROM pageviews", db_path=empty_db)) == 2


def test_backfill_covers_the_requested_span(empty_db):
    client = StubClient()
    end = date(2026, 6, 10)
    collector.backfill_range(7, end=end, db_path=empty_db, client=client)
    assert min(client.requested) == date(2026, 6, 4)
    assert max(client.requested) == end
    assert len(client.requested) == 7


def test_latest_available_date_lags_today():
    today = date(2026, 6, 10)
    assert collector.latest_available_date(today) < today


def test_backfill_article_fills_gaps(empty_db):
    class SeriesClient:
        def article_series(self, title, start, end):
            return [{"date": (start + timedelta(days=i)).isoformat(),
                     "views": 100 + i} for i in range((end - start).days + 1)]

    written = collector.backfill_article("Cleopatra", date(2026, 6, 1), date(2026, 6, 5),
                                         db_path=empty_db, client=SeriesClient())
    assert written == 5
    frame = db.read_sql("SELECT date, views, daily_rank FROM pageviews ORDER BY date",
                        db_path=empty_db)
    assert frame["views"].tolist() == [100, 101, 102, 103, 104]
    # Rank stays NULL: the article was not necessarily in the top list those days.
    assert frame["daily_rank"].isna().all()
    article = db.read_sql("SELECT first_seen, last_seen FROM articles",
                          db_path=empty_db).iloc[0]
    assert article["first_seen"] == "2026-06-01"
    assert article["last_seen"] == "2026-06-05"
