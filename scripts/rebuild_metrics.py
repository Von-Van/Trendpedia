#!/usr/bin/env python3
"""Materialise derived metrics into SQLite.

The dashboard computes these on demand (so it can never show stale numbers), but
persisting them makes the dataset useful from plain SQL and gives the app a warm
start. Both paths call the same functions in src/metrics.py, so they cannot drift.

    python scripts/rebuild_metrics.py
    python scripts/rebuild_metrics.py --window 90 --no-graph
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd                                              # noqa: E402

from src import communities as C                                 # noqa: E402
from src import database as db                                   # noqa: E402
from src import metrics as M                                     # noqa: E402
from src import relationships as R                               # noqa: E402
from src.config import (DB_PATH, DEFAULT_CORR_WINDOW,             # noqa: E402
                        DEFAULT_EDGE_THRESHOLD, DEFAULT_TOP_K_EDGES,
                        MIN_OBSERVATIONS)

METRIC_COLUMNS = ["article_id", "date", "ma7", "ma28", "velocity", "acceleration",
                  "volatility", "anomaly_z", "baseline_ratio"]
SUMMARY_COLUMNS = ["article_id", "observations", "total_views", "mean_views",
                   "median_views", "peak_views", "peak_date", "latest_views",
                   "best_rank", "latest_rank", "peak_ratio", "persistence", "volatility"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--window", type=int, default=DEFAULT_CORR_WINDOW,
                        help="days of history used for the relationship graph")
    parser.add_argument("--max-articles", type=int, default=600)
    parser.add_argument("--threshold", type=float, default=DEFAULT_EDGE_THRESHOLD)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K_EDGES)
    parser.add_argument("--no-graph", action="store_true",
                        help="skip correlation, edges and communities")
    args = parser.parse_args(argv)

    db.init_db(args.db)
    if not db.has_data(args.db):
        print("No pageview data yet. Run: python scripts/collect_daily.py --backfill 90")
        return 1

    started = time.time()
    print("Loading pageviews…")
    pv = db.read_sql(
        """SELECT p.article_id, p.date, p.views, p.daily_rank
             FROM pageviews p JOIN articles a USING (article_id)
            WHERE a.is_mainspace = 1""", db_path=args.db)
    print(f"  {len(pv):,} observations, {pv['article_id'].nunique():,} articles")

    print("Densifying panel…")
    panel = M.densify(pv)

    print("Computing daily metrics…")
    daily = M.compute_daily_metrics(panel)
    daily_out = daily[METRIC_COLUMNS].copy()
    daily_out["date"] = daily_out["date"].dt.strftime("%Y-%m-%d")
    daily_out = daily_out.astype(object).where(pd.notna(daily_out), None)

    print("Summarising articles…")
    summary = M.summarise_articles(panel)
    summary_out = summary.reindex(columns=SUMMARY_COLUMNS)
    summary_out = summary_out.astype(object).where(pd.notna(summary_out), None)

    with db.session(args.db) as conn:
        db.replace_table(conn, "metrics", daily_out)
        db.replace_table(conn, "article_summary", summary_out)
        db.set_meta(conn, "metrics_rebuilt_at",
                    datetime.now(timezone.utc).isoformat(timespec="seconds"))
    print(f"  wrote {len(daily_out):,} metric rows, {len(summary_out):,} summaries")

    if args.no_graph:
        print(f"Done in {time.time() - started:.1f}s (graph skipped)")
        return 0

    print(f"Building relationship graph over the last {args.window} days…")
    end = pd.to_datetime(panel["date"].max())
    start = end - timedelta(days=args.window - 1)
    recent = panel[panel["date"] >= start]
    wide = R.build_matrix(recent, min_observations=MIN_OBSERVATIONS,
                          max_articles=args.max_articles)
    if wide.empty or wide.shape[1] < 2:
        print("  not enough overlapping history yet — skipping graph")
        return 0

    corr = R.correlation_matrix(wide, mode="levels", remove_market_factor=True,
                                min_overlap=MIN_OBSERVATIONS)
    edges = R.build_edges(corr, threshold=args.threshold, top_k=args.top_k)
    print(f"  {wide.shape[1]} articles, {len(edges):,} edges above r={args.threshold}")

    if edges.empty:
        with db.session(args.db) as conn:
            db.replace_table(conn, "edges", pd.DataFrame(
                columns=["source_id", "target_id", "weight"]))
            db.replace_table(conn, "communities", pd.DataFrame(
                columns=["article_id", "community"]))
        print(f"Done in {time.time() - started:.1f}s")
        return 0

    sizes = recent[recent["observed"]].groupby("article_id")["views"].sum()
    graph = C.build_graph(edges, sizes.reindex(corr.index).fillna(0.0))
    groups = C.detect_communities(graph)
    community_frame = pd.DataFrame(
        {"article_id": list(groups), "community": list(groups.values())})
    print(f"  {community_frame['community'].nunique()} communities detected")

    with db.session(args.db) as conn:
        db.replace_table(conn, "edges", edges[["source_id", "target_id", "weight"]])
        db.replace_table(conn, "communities", community_frame)
        db.set_meta(conn, "graph_window", f"{start.date()}..{end.date()}")

    print(f"Done in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
