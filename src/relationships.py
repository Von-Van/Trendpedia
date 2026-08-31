"""Co-attention: which articles rise and fall together.

The naive approach — correlate raw pageview series — mostly rediscovers that
Wikipedia is busier on Mondays than Saturdays and that traffic drifts with the
seasons. Every pair picks up a shared baseline, so "related" stops meaning
anything.

We remove that shared component first. For each day, the cross-sectional mean of
log-pageviews across all tracked articles is a "market factor"; subtracting it
leaves each article's *own* movement. Correlating those residuals answers the
question we actually care about: when this article moved more than Wikipedia as
a whole, did that one move too?
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import (DEFAULT_EDGE_THRESHOLD, DEFAULT_TOP_K_EDGES,
                     MIN_OBSERVATIONS)

SERIES_MODES = {
    "levels": "Co-attention — sustained popularity at the same time",
    "changes": "Co-movement — day-to-day swings in the same direction",
}


def build_matrix(panel: pd.DataFrame, min_observations: int = MIN_OBSERVATIONS,
                 max_articles: int | None = None, impute: bool = False) -> pd.DataFrame:
    """Dates x articles matrix of log pageviews, ready for correlation.

    Unobserved days stay NaN by default. That matters: filling them with the
    day's top-list cutoff makes every thinly-tracked article trace the same
    cutoff line, and pairs of them correlate at 0.999 for no real reason. With
    NaNs, correlation is computed on the days both articles were genuinely
    observed, which is the honest comparison.
    """
    if panel.empty:
        return pd.DataFrame()

    counts = panel.groupby("article_id")["observed"].sum()
    eligible = counts[counts >= min_observations].index
    if max_articles is not None and len(eligible) > max_articles:
        # Keep the most-viewed, which is what a reader would expect to see.
        totals = (panel[panel["article_id"].isin(eligible)]
                  .groupby("article_id")["views"].sum().nlargest(max_articles))
        eligible = totals.index

    subset = panel[panel["article_id"].isin(eligible)]
    if subset.empty:
        return pd.DataFrame()

    if not impute:
        subset = subset[subset["observed"]]

    wide = subset.pivot_table(index="date", columns="article_id", values="views",
                              aggfunc="max")
    full_index = pd.date_range(panel["date"].min(), panel["date"].max(), freq="D")
    wide = wide.reindex(full_index)
    return np.log1p(wide)


def residualise(wide: pd.DataFrame) -> pd.DataFrame:
    """Strip the shared daily 'how busy was Wikipedia' factor from every series.

    The factor is the cross-sectional *median*, not the mean. A handful of
    articles going viral drags the mean up on exactly the days that matter, and
    subtracting that contaminated factor injects an inverted copy of the event
    into every unrelated article — inventing strong negative correlations. The
    median barely moves. On a large universe the two agree; on a small or
    event-dominated one the median is much better behaved.

    It skips NaNs, so days with sparse coverage still get a sensible factor and
    unobserved cells stay unobserved.
    """
    if wide.empty:
        return wide
    factor = wide.median(axis=1, skipna=True)
    return wide.sub(factor, axis=0)


def to_changes(wide: pd.DataFrame) -> pd.DataFrame:
    """Day-over-day differences of log views (i.e. growth rates)."""
    return wide.diff().iloc[1:]


def correlation_matrix(wide: pd.DataFrame, mode: str = "levels",
                       remove_market_factor: bool = True,
                       min_overlap: int = MIN_OBSERVATIONS) -> pd.DataFrame:
    """Correlation between article series, over pairwise-complete days.

    mode='levels'  — were they popular during the same stretch of days?
    mode='changes' — did they jump and fall on the same days?

    Pairs sharing fewer than `min_overlap` observed days return NaN rather than
    a confident-looking number computed from a handful of points.
    """
    if wide.empty or wide.shape[1] < 2:
        return pd.DataFrame()

    matrix = residualise(wide) if remove_market_factor else wide
    if mode == "changes":
        matrix = to_changes(matrix)
    if matrix.shape[0] < 3:
        return pd.DataFrame()

    matrix = matrix.loc[:, matrix.std() > 1e-9]
    if matrix.shape[1] < 2:
        return pd.DataFrame()

    return matrix.corr(method="pearson", min_periods=max(3, min_overlap))


def overlap_counts(wide: pd.DataFrame) -> pd.DataFrame:
    """How many days each pair of articles was observed together."""
    if wide.empty:
        return pd.DataFrame()
    present = wide.notna().astype("int16")
    counts = present.T.to_numpy() @ present.to_numpy()
    return pd.DataFrame(counts, index=wide.columns, columns=wide.columns)


def top_related(corr: pd.DataFrame, article_id: int, k: int = 10,
                minimum: float = 0.0) -> pd.DataFrame:
    """The k articles whose attention pattern most resembles `article_id`."""
    if corr.empty or article_id not in corr.index:
        return pd.DataFrame(columns=["article_id", "correlation"])
    series = corr.loc[article_id].drop(index=article_id, errors="ignore")
    series = series[series >= minimum].sort_values(ascending=False).head(k)
    return series.rename("correlation").rename_axis("article_id").reset_index()


def build_edges(corr: pd.DataFrame, threshold: float = DEFAULT_EDGE_THRESHOLD,
                top_k: int | None = DEFAULT_TOP_K_EDGES) -> pd.DataFrame:
    """Edge list for the attention graph.

    Two filters, both needed. The threshold keeps only convincing relationships;
    the per-node top-k stops a handful of hub articles from connecting to
    everything and collapsing the layout into one hairball.
    """
    if corr.empty:
        return pd.DataFrame(columns=["source_id", "target_id", "weight"])

    values = corr.to_numpy(copy=True)
    np.fill_diagonal(values, np.nan)
    ids = corr.index.to_numpy()

    if top_k is not None and top_k < len(ids) - 1:
        # Mask everything outside each row's top-k before symmetrising.
        keep = np.zeros_like(values, dtype=bool)
        order = np.argsort(-np.nan_to_num(values, nan=-np.inf), axis=1)[:, :top_k]
        np.put_along_axis(keep, order, True, axis=1)
        keep |= keep.T          # keep the edge if *either* endpoint ranks it highly
    else:
        keep = np.ones_like(values, dtype=bool)

    upper = np.triu(np.ones_like(values, dtype=bool), k=1)
    mask = keep & upper & (values >= threshold) & ~np.isnan(values)
    rows, cols = np.where(mask)
    return pd.DataFrame({
        "source_id": ids[rows],
        "target_id": ids[cols],
        "weight": values[rows, cols],
    }).sort_values("weight", ascending=False, ignore_index=True)


def correlation_pairs(corr: pd.DataFrame, top: int = 50) -> pd.DataFrame:
    """Strongest pairs overall — a quick read on what moves together."""
    edges = build_edges(corr, threshold=-1.0, top_k=None)
    return edges.head(top)
