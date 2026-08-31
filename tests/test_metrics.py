from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import database as db
from src import metrics as M


def _titles(db_path):
    return db.read_sql("SELECT article_id, title FROM articles",
                       db_path=db_path).set_index("title")["article_id"]


def test_densify_has_no_gaps(panel):
    for _, group in panel.groupby("article_id"):
        gaps = group["date"].diff().dropna().dt.days.unique()
        assert set(gaps) <= {1}


def test_gaps_are_filled_with_the_cutoff_not_zero(db_path):
    """A missing day means 'below the top-list cutoff', never 'no views'."""
    pv = db.read_sql(
        """SELECT p.article_id, p.date, p.views, p.daily_rank
             FROM pageviews p JOIN articles a USING (article_id)
            WHERE a.is_mainspace = 1""", db_path=db_path)
    # Drop one mid-window day for one article to create a real hole.
    victim = pv.iloc[0]["article_id"]
    hole = "2026-01-20"
    pruned = pv[~((pv["article_id"] == victim) & (pv["date"] == hole))]

    dense = M.densify(pruned)
    row = dense[(dense["article_id"] == victim) &
                (dense["date"] == pd.Timestamp(hole))].iloc[0]
    cutoff = pruned[pruned["date"] == hole]["views"].min()
    assert row["observed"] is np.False_ or row["observed"] == False  # noqa: E712
    assert row["views"] == pytest.approx(cutoff)
    assert row["views"] > 0


def test_densify_does_not_invent_history_before_first_sighting(db_path, panel):
    """An article that appears halfway through has no rows before that."""
    ids = _titles(db_path)
    late = panel[panel["article_id"] == ids["partial_0"]]
    assert late["date"].min() == pd.Timestamp("2026-01-31")


def test_velocity_baseline_excludes_today(panel):
    """A spike must not damp its own baseline."""
    frame = M.compute_daily_metrics(panel)
    row = frame.dropna(subset=["baseline"]).iloc[50]
    history = frame[(frame["article_id"] == row["article_id"]) &
                    (frame["date"] < row["date"])].tail(7)
    assert row["baseline"] == pytest.approx(history["views"].mean(), rel=1e-6)


def test_velocity_is_ratio_minus_one(panel):
    frame = M.compute_daily_metrics(panel).dropna(subset=["velocity", "baseline_ratio"])
    assert np.allclose(frame["velocity"], frame["baseline_ratio"] - 1)


def test_flash_articles_spike_hard(db_path, panel):
    ids = _titles(db_path)
    frame = M.compute_daily_metrics(panel)
    flash = frame[frame["article_id"] == ids["flash_0"]].dropna(subset=["velocity"])
    assert flash["velocity"].max() > 10          # 8k -> 400k
    assert flash["anomaly_z"].max() > 3


def test_steady_articles_do_not_look_like_news(db_path, panel):
    ids = _titles(db_path)
    frame = M.compute_daily_metrics(panel)
    steady = frame[frame["article_id"] == ids["steady_0"]].dropna(subset=["velocity"])
    assert steady["velocity"].abs().max() < 0.5
    assert steady["volatility"].max() < 0.2


def test_summary_shape_statistics(db_path, panel):
    ids = _titles(db_path)
    summary = M.summarise_articles(panel).set_index("article_id")
    flash = summary.loc[ids["flash_0"]]
    steady = summary.loc[ids["steady_0"]]

    assert flash["peak_ratio"] > 10           # one enormous day
    assert flash["persistence"] < 0.1         # and nothing after it
    assert steady["peak_ratio"] < 1.5         # evergreen
    assert steady["persistence"] > 0.9
    assert steady["volatility"] < flash["volatility"]


def test_summary_counts_only_observed_days(db_path, panel):
    ids = _titles(db_path)
    summary = M.summarise_articles(panel).set_index("article_id")
    # partial_0 exists for 30 of the 60 days; imputation must not inflate it.
    assert summary.loc[ids["partial_0"], "observations"] == 30


def test_summary_totals_match_raw_sums(db_path, panel):
    raw = db.read_sql(
        """SELECT p.article_id, SUM(p.views) AS total
             FROM pageviews p JOIN articles a USING (article_id)
            WHERE a.is_mainspace = 1 GROUP BY p.article_id""", db_path=db_path)
    summary = M.summarise_articles(panel)
    merged = raw.merge(summary, on="article_id")
    assert np.allclose(merged["total"], merged["total_views"])


def test_normalise_around_peak_is_centred_and_scaled(panel):
    shapes = M.normalise_around_peak(panel, window=10)
    at_peak = shapes[shapes["days_from_peak"] == 0]
    assert np.allclose(at_peak["relative_views"], 1.0)
    assert shapes["relative_views"].max() <= 1.0 + 1e-9
    assert shapes["days_from_peak"].abs().max() <= 10


def test_lifecycle_labels_separate_flash_from_evergreen(db_path, panel):
    ids = _titles(db_path)
    summary = M.summarise_articles(panel)
    shapes = M.normalise_around_peak(panel, window=14)
    labels = M.classify_lifecycle(summary, shapes)
    assert labels[ids["flash_0"]] == "flash spike"
    assert labels[ids["steady_0"]] in {"long-lived trend", "steady interest"}


def test_empty_input_is_handled(empty_db):
    empty = pd.DataFrame(columns=["article_id", "date", "views", "daily_rank"])
    dense = M.densify(empty)
    assert dense.empty
    assert M.compute_daily_metrics(dense).empty
    assert M.summarise_articles(dense).empty


def test_flat_series_still_scores_its_first_spike(db_path, panel):
    """A perfectly steady article has zero rolling spread.

    Without a floor on that spread the anomaly score is NaN precisely on the day
    the article explodes, which is the one day it needs to be finite.
    """
    ids = _titles(db_path)
    frame = M.compute_daily_metrics(panel)
    flash = frame[frame["article_id"] == ids["flash_0"]]
    spike = flash.loc[flash["views"].idxmax()]
    assert np.isfinite(spike["anomaly_z"])
    assert spike["anomaly_z"] > 3
