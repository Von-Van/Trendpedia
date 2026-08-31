from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import database as db
from src import relationships as R


def _ids(db_path):
    return db.read_sql("SELECT article_id, title FROM articles",
                       db_path=db_path).set_index("title")["article_id"]


@pytest.fixture(scope="module")
def wide(panel):
    return R.build_matrix(panel, min_observations=10)


def test_matrix_is_log_scaled_dates_by_articles(panel, wide):
    assert wide.shape[0] == panel["date"].nunique()
    assert (wide.dropna().to_numpy() > 0).all()
    assert wide.max().max() < 25  # log space, not raw views


def test_unobserved_days_stay_missing_by_default(db_path, wide):
    """Imputing gaps makes thinly-tracked articles trace the same cutoff line."""
    ids = _ids(db_path)
    partial = wide[ids["partial_0"]]
    assert partial.isna().sum() == 30


def test_related_articles_are_found(db_path, wide):
    ids = _ids(db_path)
    corr = R.correlation_matrix(wide, min_overlap=10)
    trio = [ids["trio_a"], ids["trio_b"], ids["trio_c"]]
    for a in trio:
        for b in trio:
            if a != b:
                assert corr.loc[a, b] > 0.9


def test_unrelated_articles_are_not_related(db_path, wide):
    ids = _ids(db_path)
    corr = R.correlation_matrix(wide, min_overlap=10)
    assert abs(corr.loc[ids["trio_a"], ids["solo_0"]]) < 0.5
    assert abs(corr.loc[ids["solo_0"], ids["solo_1"]]) < 0.5


def test_top_related_ranks_the_event_cluster_first(db_path, wide):
    ids = _ids(db_path)
    corr = R.correlation_matrix(wide, min_overlap=10)
    related = R.top_related(corr, ids["trio_a"], k=2)
    assert set(related["article_id"]) == {ids["trio_b"], ids["trio_c"]}


def _universe(n_articles=40, days=90, seed=0):
    """Independent articles sharing one site-wide weekly rhythm, plus a true pair.

    This is the situation the market factor exists for: everything moves together
    a little because Wikipedia itself is busier midweek.
    """
    rng = np.random.default_rng(seed)
    index = pd.date_range("2026-01-01", periods=days, freq="D")
    rhythm = 0.45 * np.sin(np.arange(days) * 2 * np.pi / 7) + \
        np.linspace(0, 0.5, days)          # weekly rhythm + slow site-wide drift
    columns = {}
    for i in range(n_articles):
        columns[i] = 10 + rhythm + rng.normal(0, 0.10, days)
    # One genuinely related pair, on top of the shared rhythm.
    story = rng.normal(0, 0.6, days)
    columns[100] = 10 + rhythm + story + rng.normal(0, 0.05, days)
    columns[101] = 10 + rhythm + story + rng.normal(0, 0.05, days)
    return pd.DataFrame(columns, index=index)


def test_market_factor_removal_strips_the_shared_rhythm():
    """Unrelated articles riding the same weekly rhythm should stop looking related."""
    wide = _universe()
    raw = R.correlation_matrix(wide, remove_market_factor=False, min_overlap=10)
    residual = R.correlation_matrix(wide, remove_market_factor=True, min_overlap=10)
    unrelated = [c for c in wide.columns if c < 100]
    upper = np.triu_indices(len(unrelated), 1)

    raw_typical = np.nanmedian(raw.loc[unrelated, unrelated].to_numpy()[upper])
    residual_typical = np.nanmedian(residual.loc[unrelated, unrelated].to_numpy()[upper])
    assert raw_typical > 0.75                      # the rhythm makes everything "related"
    assert abs(residual_typical) < 0.2             # and removing it dissolves that


def test_market_factor_removal_preserves_real_signal():
    """The genuinely related pair must survive the treatment."""
    wide = _universe()
    residual = R.correlation_matrix(wide, remove_market_factor=True, min_overlap=10)
    assert residual.loc[100, 101] > 0.8


def test_factor_is_robust_to_one_dominant_event():
    """A few articles going viral must not invert into everyone else's series.

    With a mean factor, a large shared event contaminates the factor itself, so
    subtracting it stamps a mirrored copy of the event onto every unrelated
    article and fabricates strong negative correlations. The median resists this.
    """
    wide = _universe(n_articles=12, days=60, seed=3)
    spike = np.exp(-((np.arange(60) - 30) ** 2) / 40) * 3.0
    for column in (200, 201, 202):                 # a loud, correlated minority
        wide[column] = 10 + spike + np.random.default_rng(column).normal(0, 0.05, 60)

    residual = R.correlation_matrix(wide, remove_market_factor=True, min_overlap=10)
    quiet = [c for c in wide.columns if c < 100]
    upper = np.triu_indices(len(quiet), 1)
    worst = np.nanmin(residual.loc[quiet, quiet].to_numpy()[upper])
    assert worst > -0.5, "the event leaked into unrelated articles"


def test_residualise_centres_each_day_on_its_median(wide):
    residual = R.residualise(wide)
    assert np.allclose(residual.median(axis=1).dropna(), 0, atol=1e-9)


def test_changes_mode_uses_differences(wide):
    changes = R.to_changes(wide)
    assert len(changes) == len(wide) - 1


def test_min_overlap_blocks_thin_comparisons(db_path, wide):
    """Pairs without enough shared days get NaN, not a confident-looking number."""
    ids = _ids(db_path)
    corr = R.correlation_matrix(wide, min_overlap=45)
    assert pd.isna(corr.loc[ids["partial_0"], ids["trio_a"]])


def test_edges_respect_threshold_and_have_no_self_loops(wide):
    corr = R.correlation_matrix(wide, min_overlap=10)
    edges = R.build_edges(corr, threshold=0.8, top_k=None)
    assert (edges["weight"] >= 0.8).all()
    assert (edges["source_id"] != edges["target_id"]).all()


def test_edges_are_deduplicated(wide):
    corr = R.correlation_matrix(wide, min_overlap=10)
    edges = R.build_edges(corr, threshold=0.5, top_k=None)
    pairs = {frozenset((r.source_id, r.target_id)) for r in edges.itertuples()}
    assert len(pairs) == len(edges)


def test_top_k_limits_hub_degree(wide):
    corr = R.correlation_matrix(wide, min_overlap=10)
    loose = R.build_edges(corr, threshold=-1.0, top_k=None)
    capped = R.build_edges(corr, threshold=-1.0, top_k=2)
    assert len(capped) < len(loose)
    degree = pd.concat([capped["source_id"], capped["target_id"]]).value_counts()
    assert degree.max() <= 2 * corr.shape[0]  # symmetrised, so a node can exceed k


def test_edges_sorted_strongest_first(wide):
    corr = R.correlation_matrix(wide, min_overlap=10)
    edges = R.build_edges(corr, threshold=0.5, top_k=None)
    assert edges["weight"].is_monotonic_decreasing


def test_empty_inputs_do_not_explode():
    empty = pd.DataFrame()
    assert R.build_matrix(empty).empty
    assert R.correlation_matrix(empty).empty
    assert R.build_edges(pd.DataFrame()).empty
