"""Shared fixtures.

Tests run against a synthetic dataset rather than the real database or the live
API: deterministic, offline, and fast.
"""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import database as db  # noqa: E402

DAYS = 60
START = date(2026, 1, 1)


def _synthetic_frame() -> pd.DataFrame:
    """Articles with deliberately different attention shapes.

    - trio_a/b/c   move together (a shared "event")
    - solo_*       independent noise
    - flash_*      one enormous day
    - steady_*     flat evergreen traffic
    - partial_*    only present for part of the window (tests censoring)
    """
    rng = np.random.default_rng(7)
    rows = []
    dates = [START + timedelta(days=i) for i in range(DAYS)]
    event = np.array([math.exp(-((i - 30) ** 2) / 40) for i in range(DAYS)])

    for name, base, shape in [
        ("trio_a", 50_000, event), ("trio_b", 40_000, event), ("trio_c", 30_000, event),
    ]:
        series = base * (1 + 6 * shape) * (1 + rng.normal(0, 0.03, DAYS))
        rows += [(name, d, max(1000, int(v)), None) for d, v in zip(dates, series)]

    for k in range(4):
        series = 25_000 * (1 + rng.normal(0, 0.35, DAYS))
        rows += [(f"solo_{k}", d, max(1000, int(v)), None) for d, v in zip(dates, series)]

    for k in range(2):
        series = np.full(DAYS, 8_000.0)
        series[20 + k] = 400_000
        rows += [(f"flash_{k}", d, int(v), None) for d, v in zip(dates, series)]

    for k in range(2):
        series = 60_000 * (1 + rng.normal(0, 0.02, DAYS))
        rows += [(f"steady_{k}", d, int(v), None) for d, v in zip(dates, series)]

    # Present only in the second half — its early days are genuinely unobserved.
    late = 20_000 * (1 + rng.normal(0, 0.2, DAYS // 2))
    rows += [(f"partial_0", d, max(1000, int(v)), None)
             for d, v in zip(dates[DAYS // 2:], late)]

    # Non-article traffic that the mainspace filter must exclude.
    rows += [("Main_Page", d, 5_000_000, 1) for d in dates]
    rows += [("Special:Search", d, 800_000, 2) for d in dates]

    frame = pd.DataFrame(rows, columns=["title", "date", "views", "daily_rank"])
    frame["date"] = frame["date"].map(lambda d: d.isoformat())
    # Assign a real daily rank by views within each day.
    frame["daily_rank"] = (frame.groupby("date")["views"]
                           .rank(ascending=False, method="first").astype(int))
    return frame


@pytest.fixture(scope="session")
def synthetic_frame() -> pd.DataFrame:
    return _synthetic_frame()


@pytest.fixture(scope="session")
def db_path(tmp_path_factory, synthetic_frame) -> str:
    path = str(tmp_path_factory.mktemp("atlas") / "test.db")
    db.init_db(path)
    with db.session(path) as conn:
        for day, group in synthetic_frame.groupby("date"):
            ids = db.upsert_articles(conn, group["title"].tolist(), day)
            db.insert_pageviews(conn, [
                (ids[r.title], day, int(r.views), int(r.daily_rank))
                for r in group.itertuples(index=False)])
    return path


@pytest.fixture
def empty_db(tmp_path) -> str:
    path = str(tmp_path / "empty.db")
    db.init_db(path)
    return path


@pytest.fixture(scope="session")
def panel(db_path):
    from src import metrics as M
    pv = db.read_sql(
        """SELECT p.article_id, p.date, p.views, p.daily_rank
             FROM pageviews p JOIN articles a USING (article_id)
            WHERE a.is_mainspace = 1""", db_path=db_path)
    return M.densify(pv)
