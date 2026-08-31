"""Cached data access for the dashboard.

Derived metrics are computed on demand from the raw `pageviews` table rather
than read from the materialised tables, so the dashboard can never show numbers
that are stale relative to the data. `scripts/rebuild_metrics.py` writes the
same values into SQLite for external SQL use, calling these very functions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import streamlit as st

from . import communities as C
from . import database as db
from . import metrics as M
from . import relationships as R
from .config import DB_PATH, MIN_OBSERVATIONS, pretty_title

CACHE_TTL = 900  # seconds; a daily collector makes anything shorter pointless


@dataclass(frozen=True)
class Filters:
    """Sidebar state, frozen so Streamlit can use it as a cache key."""
    db_path: str
    start: str
    end: str
    mainspace_only: bool = True
    min_views: int = 0
    min_observations: int = MIN_OBSERVATIONS


# --- raw loads ---------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_titles(db_path: str) -> pd.DataFrame:
    return db.read_sql(
        "SELECT article_id, title, first_seen, last_seen, is_mainspace FROM articles",
        db_path=db_path)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def title_map(db_path: str) -> pd.Series:
    frame = load_titles(db_path)
    return frame.set_index("article_id")["title"]


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_pageviews(db_path: str, start: str, end: str, mainspace_only: bool) -> pd.DataFrame:
    clause = "AND a.is_mainspace = 1" if mainspace_only else ""
    return db.read_sql(
        f"""SELECT p.article_id, p.date, p.views, p.daily_rank
              FROM pageviews p JOIN articles a USING (article_id)
             WHERE p.date BETWEEN ? AND ? {clause}""",
        (start, end), db_path=db_path)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def dataset_stats(db_path: str) -> dict:
    stats = db.read_sql(
        """SELECT COUNT(*) AS observations,
                  COUNT(DISTINCT article_id) AS articles,
                  COUNT(DISTINCT date) AS days,
                  MIN(date) AS first_date, MAX(date) AS last_date,
                  SUM(views) AS total_views
             FROM pageviews""", db_path=db_path)
    out = stats.iloc[0].to_dict() if not stats.empty else {}
    runs = db.read_sql(
        "SELECT run_at, target_date, status, rows_written FROM collection_runs"
        " ORDER BY run_id DESC LIMIT 12", db_path=db_path)
    out["recent_runs"] = runs
    gaps = db.read_sql(
        """SELECT COUNT(*) AS n FROM (
               SELECT DISTINCT date FROM pageviews) t""", db_path=db_path)
    out["distinct_days"] = int(gaps.loc[0, "n"]) if not gaps.empty else 0
    return out


# --- derived -----------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL, show_spinner="Building the attention panel…")
def load_panel(f: Filters) -> pd.DataFrame:
    """Gap-free daily panel over the filter window."""
    pv = load_pageviews(f.db_path, f.start, f.end, f.mainspace_only)
    if pv.empty:
        return pv
    return M.densify(pv, start=f.start, end=f.end)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Computing attention metrics…")
def load_metrics(f: Filters) -> pd.DataFrame:
    panel = load_panel(f)
    if panel.empty:
        return panel
    frame = M.compute_daily_metrics(panel)
    return frame


@st.cache_data(ttl=CACHE_TTL, show_spinner="Summarising articles…")
def load_summary(f: Filters) -> pd.DataFrame:
    panel = load_panel(f)
    if panel.empty:
        return pd.DataFrame()
    summary = M.summarise_articles(panel)
    if summary.empty:
        return summary
    summary = summary.merge(load_titles(f.db_path)[["article_id", "title"]],
                            on="article_id", how="left")
    summary["name"] = summary["title"].map(pretty_title)
    if f.min_views:
        summary = summary[summary["peak_views"] >= f.min_views]
    return summary.reset_index(drop=True)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def article_series(f: Filters, article_id: int) -> pd.DataFrame:
    """One article's daily series with its rolling metrics attached."""
    frame = load_metrics(f)
    if frame.empty:
        return frame
    return frame[frame["article_id"] == article_id].sort_values("date")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def latest_day(f: Filters) -> str | None:
    pv = load_pageviews(f.db_path, f.start, f.end, f.mainspace_only)
    return pv["date"].max() if not pv.empty else None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def day_movers(f: Filters, day: str, top: int = 15) -> dict:
    """Gainers, losers and new entrants for a single day."""
    frame = load_metrics(f)
    if frame.empty:
        return {"gainers": pd.DataFrame(), "losers": pd.DataFrame(),
                "new": pd.DataFrame(), "top": pd.DataFrame()}

    titles = title_map(f.db_path)
    day_ts = pd.Timestamp(day)
    today = frame[(frame["date"] == day_ts) & frame["observed"]].copy()
    if today.empty:
        return {"gainers": pd.DataFrame(), "losers": pd.DataFrame(),
                "new": pd.DataFrame(), "top": pd.DataFrame()}

    today["title"] = today["article_id"].map(titles)
    today["name"] = today["title"].map(pretty_title)
    if f.min_views:
        today = today[today["views"] >= f.min_views]

    ranked = today.sort_values("views", ascending=False)
    movers = today[today["baseline"].notna() & (today["baseline"] > 0)]
    firsts = load_titles(f.db_path).set_index("article_id")["first_seen"]
    today["first_seen"] = today["article_id"].map(firsts)
    newcomers = today[today["first_seen"] == day].sort_values("views", ascending=False)

    return {
        "top": ranked.head(top),
        "gainers": movers.nlargest(top, "velocity"),
        "losers": movers.nsmallest(top, "velocity"),
        "new": newcomers.head(top),
    }


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def daily_totals(f: Filters) -> pd.DataFrame:
    """Total observed attention per day — the dataset's pulse."""
    pv = load_pageviews(f.db_path, f.start, f.end, f.mainspace_only)
    if pv.empty:
        return pv
    out = (pv.groupby("date")
             .agg(total_views=("views", "sum"), articles=("article_id", "nunique"),
                  cutoff=("views", "min"))
             .reset_index())
    out["date"] = pd.to_datetime(out["date"])
    return out


# --- relationships -----------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL, show_spinner="Correlating attention patterns…")
def load_correlation(f: Filters, mode: str, remove_factor: bool,
                     max_articles: int) -> pd.DataFrame:
    panel = load_panel(f)
    if panel.empty:
        return pd.DataFrame()
    wide = R.build_matrix(panel, min_observations=f.min_observations,
                          max_articles=max_articles)
    if wide.empty:
        return pd.DataFrame()
    return R.correlation_matrix(wide, mode=mode, remove_market_factor=remove_factor,
                               min_overlap=max(3, int(f.min_observations * 0.8)))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def node_sizes(f: Filters) -> pd.Series:
    panel = load_panel(f)
    if panel.empty:
        return pd.Series(dtype=float)
    return panel[panel["observed"]].groupby("article_id")["views"].sum()


@st.cache_data(ttl=CACHE_TTL, show_spinner="Building the attention map…")
def load_graph(f: Filters, mode: str, remove_factor: bool, max_articles: int,
               threshold: float, top_k: int, resolution: float,
               max_groups: int = 8) -> dict:
    """Nodes, edges and community summaries for the attention map.

    Communities past `max_groups` fold into a single grey "Other" so the
    categorical palette is never cycled into invented hues.
    """
    corr = load_correlation(f, mode, remove_factor, max_articles)
    empty = {"nodes": pd.DataFrame(), "edges": pd.DataFrame(),
             "summary": pd.DataFrame(), "corr": corr}
    if corr.empty:
        return empty

    edges = R.build_edges(corr, threshold=threshold, top_k=top_k)
    if edges.empty:
        return empty

    sizes = node_sizes(f).reindex(corr.index).fillna(0.0)
    graph = C.build_graph(edges, sizes)
    groups = C.detect_communities(graph, resolution=resolution)
    positions = C.layout_positions(graph)
    nodes = C.graph_frame(graph, groups, positions, title_map(f.db_path), sizes)
    if nodes.empty:
        return empty

    summary = C.summarise_communities(nodes)
    keep = summary.head(max_groups)["community"].tolist()
    rank = {community: i for i, community in enumerate(keep)}
    nodes["group_rank"] = nodes["community"].map(rank).fillna(-1).astype(int)
    label_by_community = summary.set_index("community")["label"]
    nodes["group_label"] = np.where(
        nodes["group_rank"] >= 0,
        nodes["community"].map(label_by_community),
        "Other groups")
    nodes["name"] = nodes["title"].map(pretty_title)
    nodes = nodes.sort_values("group_rank").reset_index(drop=True)
    summary["shown"] = summary["community"].isin(keep)
    return {"nodes": nodes, "edges": edges, "summary": summary, "corr": corr}


# --- lookups -----------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def searchable_articles(f: Filters) -> pd.DataFrame:
    """Articles present in the window, most-viewed first, for the search box."""
    summary = load_summary(f)
    if summary.empty:
        return pd.DataFrame(columns=["article_id", "title", "name", "total_views"])
    return (summary[["article_id", "title", "name", "total_views", "observations"]]
            .sort_values("total_views", ascending=False, ignore_index=True))


def resolve_article(f: Filters, name: str) -> int | None:
    frame = searchable_articles(f)
    hit = frame[frame["name"] == name]
    return int(hit.iloc[0]["article_id"]) if not hit.empty else None


@st.cache_data(ttl=CACHE_TTL, show_spinner="Shaping attention lifecycles…")
def load_lifecycles(f: Filters, window: int = 21) -> dict:
    """Peak-normalised curves plus heuristic shape labels."""
    panel = load_panel(f)
    if panel.empty:
        return {"shapes": pd.DataFrame(), "summary": pd.DataFrame()}

    shapes = M.normalise_around_peak(panel, window=window)
    summary = load_summary(f)
    if summary.empty or shapes.empty:
        return {"shapes": shapes, "summary": summary}

    labels = M.classify_lifecycle(summary, shapes)
    summary = summary.merge(labels.reset_index(), on="article_id", how="left")
    summary["lifecycle"] = summary["lifecycle"].fillna("steady interest")
    shapes = shapes.merge(summary[["article_id", "name", "lifecycle"]],
                          on="article_id", how="left")
    return {"shapes": shapes, "summary": summary}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def sparklines(f: Filters, article_ids: tuple[int, ...], days: int = 14) -> dict:
    """Recent daily views per article, for inline sparkline columns."""
    frame = load_metrics(f)
    if frame.empty:
        return {}
    cutoff = frame["date"].max() - pd.Timedelta(days=days - 1)
    recent = frame[(frame["date"] >= cutoff) & frame["article_id"].isin(article_ids)]
    return {int(k): v.tolist() for k, v in
            recent.sort_values("date").groupby("article_id")["views"]}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def period_snapshot(f: Filters, start: str, end: str, top: int = 20) -> pd.DataFrame:
    """Leaderboard for an arbitrary sub-window — used to compare two periods."""
    pv = load_pageviews(f.db_path, start, end, f.mainspace_only)
    if pv.empty:
        return pd.DataFrame(columns=["article_id", "title", "name", "total_views",
                                     "days", "mean_views"])
    agg = (pv.groupby("article_id")
             .agg(total_views=("views", "sum"), days=("date", "nunique"),
                  mean_views=("views", "mean"), best_rank=("daily_rank", "min"))
             .reset_index())
    titles = title_map(f.db_path)
    agg["title"] = agg["article_id"].map(titles)
    agg["name"] = agg["title"].map(pretty_title)
    return agg.nlargest(top, "total_views").reset_index(drop=True)
