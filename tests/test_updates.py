"""Tests for on-demand updating: what is missing, and fetching exactly that."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src import collector
from src import database as db
from tests.test_collector import StubClient


def _seed(db_path: str, days: list[date]) -> None:
    collector.collect_days(days, db_path=db_path, client=StubClient())


def test_status_of_an_empty_dataset(empty_db):
    status = collector.data_status(empty_db)
    assert status.is_empty
    assert status.first is None and status.last is None
    assert status.days_behind == 0
    assert status.missing == []


def test_missing_days_on_empty_dataset_is_empty(empty_db):
    """With nothing collected there is no history to infer a range from."""
    assert collector.missing_days(empty_db) == []


def test_missing_days_finds_new_days(empty_db):
    newest = collector.latest_available_date()
    _seed(empty_db, [newest - timedelta(days=n) for n in (4, 3, 2)])
    missing = collector.missing_days(empty_db)
    assert missing == [newest - timedelta(days=1), newest]


def test_missing_days_finds_internal_gaps(empty_db):
    """A run that failed halfway leaves a hole, and Update should fill it."""
    newest = collector.latest_available_date()
    _seed(empty_db, [newest - timedelta(days=n) for n in (5, 4, 2, 1, 0)])
    missing = collector.missing_days(empty_db)
    assert newest - timedelta(days=3) in missing


def test_status_separates_new_days_from_gaps(empty_db):
    newest = collector.latest_available_date()
    _seed(empty_db, [newest - timedelta(days=n) for n in (6, 5, 3, 2)])
    status = collector.data_status(empty_db)
    assert status.days_behind == 2                    # newest-1 and newest
    assert status.internal_gaps == 1                  # newest-4
    assert not status.is_current
    assert status.total_days == 4


def test_status_is_current_when_nothing_is_missing(empty_db):
    newest = collector.latest_available_date()
    _seed(empty_db, [newest - timedelta(days=n) for n in (2, 1, 0)])
    status = collector.data_status(empty_db)
    assert status.is_current
    assert status.days_behind == 0
    assert status.missing == []


def test_status_records_when_it_last_ran(empty_db):
    _seed(empty_db, [collector.latest_available_date()])
    assert collector.data_status(empty_db).last_updated is not None


def test_collect_days_fetches_only_what_was_asked_for(empty_db):
    client = StubClient()
    wanted = [date(2026, 6, 1), date(2026, 6, 5)]
    result = collector.collect_days(wanted, db_path=empty_db, client=client)
    assert result.collected == 2
    assert client.requested == wanted                 # not the range between them
    assert db.collected_dates(empty_db) == ["2026-06-01", "2026-06-05"]


def test_collect_days_explains_unpublished_days(empty_db):
    """The common case: asking for a day Wikimedia has not finished yet."""
    gap = date(2026, 6, 2)
    result = collector.collect_days(
        [date(2026, 6, 1), gap], db_path=empty_db, client=StubClient(missing={gap}))
    assert result.collected == 1 and result.failed == 1
    assert "not published" in result.errors[0]


def test_collect_days_reports_progress(empty_db):
    seen = []
    collector.collect_days([date(2026, 6, 1), date(2026, 6, 2)], db_path=empty_db,
                           client=StubClient(),
                           progress=lambda done, total, day: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2)]


def test_collect_days_accepts_an_empty_list(empty_db):
    result = collector.collect_days([], db_path=empty_db, client=StubClient())
    assert result.collected == 0 and result.failed == 0


def test_updating_twice_changes_nothing_the_second_time(empty_db):
    """The Update button must be safe to press repeatedly."""
    newest = collector.latest_available_date()
    _seed(empty_db, [newest - timedelta(days=3)])
    first = collector.missing_days(empty_db)
    collector.collect_days(first, db_path=empty_db, client=StubClient())
    assert collector.data_status(empty_db).is_current
    assert collector.missing_days(empty_db) == []


def test_older_range_sits_immediately_before_the_dataset():
    """Reaching further back must not re-fetch days already held."""
    from src.data_panel import older_range

    first = date(2026, 5, 3)
    older = older_range(first, 90)
    assert len(older) == 90
    assert older[-1] == first - timedelta(days=1)     # ends the day before
    assert older[0] == date(2026, 2, 2)
    assert first not in older
