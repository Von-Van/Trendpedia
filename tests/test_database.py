from __future__ import annotations

import pandas as pd
import pytest

from src import database as db


def test_init_is_idempotent(empty_db):
    db.init_db(empty_db)
    db.init_db(empty_db)
    tables = db.read_sql(
        "SELECT name FROM sqlite_master WHERE type='table'", db_path=empty_db)
    assert {"articles", "pageviews", "metrics", "collection_runs"} <= set(tables["name"])


def test_empty_database_reports_no_data(empty_db):
    assert db.has_data(empty_db) is False
    assert db.date_bounds(empty_db) == (None, None)


def test_upsert_widens_seen_window(empty_db):
    with db.session(empty_db) as conn:
        db.upsert_articles(conn, ["Cleopatra"], "2026-05-10")
        db.upsert_articles(conn, ["Cleopatra"], "2026-05-20")
        db.upsert_articles(conn, ["Cleopatra"], "2026-04-01")
    row = db.read_sql("SELECT * FROM articles", db_path=empty_db).iloc[0]
    assert row["first_seen"] == "2026-04-01"
    assert row["last_seen"] == "2026-05-20"


def test_upsert_marks_namespace(empty_db):
    with db.session(empty_db) as conn:
        db.upsert_articles(conn, ["Cleopatra", "Special:Search", "Star_Trek:_Voyager"],
                           "2026-05-10")
    frame = db.read_sql("SELECT title, is_mainspace FROM articles", db_path=empty_db)
    flags = dict(zip(frame["title"], frame["is_mainspace"]))
    assert flags["Cleopatra"] == 1
    assert flags["Star_Trek:_Voyager"] == 1   # colons are not always namespaces
    assert flags["Special:Search"] == 0


def test_recollecting_a_day_updates_rather_than_duplicates(empty_db):
    with db.session(empty_db) as conn:
        ids = db.upsert_articles(conn, ["A"], "2026-05-10")
        db.insert_pageviews(conn, [(ids["A"], "2026-05-10", 100, 5)])
        db.insert_pageviews(conn, [(ids["A"], "2026-05-10", 250, 3)])
    frame = db.read_sql("SELECT * FROM pageviews", db_path=empty_db)
    assert len(frame) == 1
    assert frame.iloc[0]["views"] == 250
    assert frame.iloc[0]["daily_rank"] == 3


def test_history_for_other_days_is_preserved(empty_db):
    with db.session(empty_db) as conn:
        ids = db.upsert_articles(conn, ["A"], "2026-05-10")
        db.insert_pageviews(conn, [(ids["A"], "2026-05-10", 100, 5),
                                   (ids["A"], "2026-05-11", 200, 4)])
        db.insert_pageviews(conn, [(ids["A"], "2026-05-11", 999, 1)])
    frame = db.read_sql("SELECT date, views FROM pageviews ORDER BY date",
                        db_path=empty_db)
    assert frame["views"].tolist() == [100, 999]


def test_session_rolls_back_on_error(empty_db):
    with pytest.raises(RuntimeError):
        with db.session(empty_db) as conn:
            db.upsert_articles(conn, ["Doomed"], "2026-05-10")
            raise RuntimeError("boom")
    assert db.read_sql("SELECT * FROM articles", db_path=empty_db).empty


def test_fixture_dataset_loads(db_path):
    assert db.has_data(db_path)
    lo, hi = db.date_bounds(db_path)
    assert lo == "2026-01-01" and hi == "2026-03-01"
