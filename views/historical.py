"""Historical Explorer — what attention looked like then, and how two periods differ."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from src import charts, database as db, queries as Q, ui


def _leaderboard(frame: pd.DataFrame, key: str, height: int = 420) -> None:
    if frame.empty:
        ui.empty_state("Nothing recorded for this period.")
        return
    display = ui.article_frame(frame)
    display["Total"] = display["total_views"]
    display["Days"] = display["days"]
    display["Best rank"] = display["best_rank"]
    ui.articles_table(display, {
        "Name": st.column_config.TextColumn("Article", width="medium"),
        "Total": ui.count("Views in period"),
        "Days": st.column_config.NumberColumn("Days present", format="%d"),
        "Best rank": st.column_config.NumberColumn("Best rank", format="#%d"),
    }, height=height, key=key)


def render() -> None:
    f: Q.Filters = st.session_state["filters"]
    ui.page_header("Historical Explorer",
                   "Look at a past period on its own terms, or set two side by side.")

    lo, hi = db.date_bounds(f.db_path)
    if not lo or not hi:
        ui.empty_state("No data collected yet.",
                       "Run `python scripts/collect_daily.py --backfill 90`.")
        return
    lo_d, hi_d = date.fromisoformat(lo), date.fromisoformat(hi)

    tabs = st.tabs(["Single period", "Compare two periods"])

    with tabs[0]:
        picked = st.date_input(
            "Period", value=(max(lo_d, hi_d - timedelta(days=13)), hi_d),
            min_value=lo_d, max_value=hi_d, key="hist_single")
        if not (isinstance(picked, tuple) and len(picked) == 2):
            st.caption("Pick an end date to continue.")
            return
        start, end = picked
        snapshot = Q.period_snapshot(f, start.isoformat(), end.isoformat(), top=25)
        totals = Q.daily_totals(f)
        window = totals[(totals["date"] >= pd.Timestamp(start)) &
                        (totals["date"] <= pd.Timestamp(end))]

        ui.kpis([
            ("Period", f"{(end - start).days + 1} days", f"{start} → {end}"),
            ("Attention", ui.human(window["total_views"].sum()) if not window.empty else "—",
             None),
            ("Distinct articles", f"{len(Q.load_pageviews(f.db_path, start.isoformat(), end.isoformat(), f.mainspace_only)['article_id'].unique()):,}", None),
            ("Leader", snapshot.iloc[0]["name"] if not snapshot.empty else "—",
             ui.human(snapshot.iloc[0]["total_views"]) if not snapshot.empty else None),
        ])

        if not window.empty:
            fig = charts.timeseries([{
                "x": window["date"], "y": window["total_views"], "name": "Total views",
                "fill": "tozeroy", "fillcolor": "rgba(42,120,214,0.10)",
                "hovertemplate": "%{y:,.0f} views<extra></extra>"}], height=250)
            st.plotly_chart(fig, use_container_width=True, key="hist_pulse")

        left, right = st.columns([3, 2])
        with left:
            st.markdown("**Most-viewed articles in the period**")
            _leaderboard(snapshot, key="hist_board")
        with right:
            st.markdown("**Largest spikes in the period**")
            metrics = Q.load_metrics(f)
            spikes = metrics[(metrics["date"] >= pd.Timestamp(start)) &
                             (metrics["date"] <= pd.Timestamp(end)) &
                             metrics["observed"] & metrics["anomaly_z"].notna()]
            if f.min_views:
                spikes = spikes[spikes["views"] >= f.min_views]
            if spikes.empty:
                ui.empty_state("No scored spikes — the window may be too early in the dataset.")
            else:
                best = spikes.loc[spikes.groupby("article_id")["anomaly_z"].idxmax()]
                best = best.nlargest(12, "anomaly_z")
                titles = Q.title_map(f.db_path)
                names = [titles.get(a, str(a)).replace("_", " ")[:30]
                         for a in best["article_id"]]
                fig = charts.hbar(names, best["anomaly_z"],
                                  text=[f"{v:.1f}σ" for v in best["anomaly_z"]],
                                  color=charts.palette()[1], height=420,
                                  x_title="σ above the article's own baseline")
                st.plotly_chart(fig, use_container_width=True, key="hist_spikes")

    with tabs[1]:
        span = st.slider("Length of each period (days)", 3, 45, 14, key="hist_span")
        cols = st.columns(2)
        with cols[0]:
            a_end = st.date_input("Period A ends", value=hi_d, min_value=lo_d,
                                  max_value=hi_d, key="hist_a")
        with cols[1]:
            default_b = max(lo_d + timedelta(days=span), hi_d - timedelta(days=span))
            b_end = st.date_input("Period B ends", value=default_b, min_value=lo_d,
                                  max_value=hi_d, key="hist_b")

        a_start = max(lo_d, a_end - timedelta(days=span - 1))
        b_start = max(lo_d, b_end - timedelta(days=span - 1))
        a = Q.period_snapshot(f, a_start.isoformat(), a_end.isoformat(), top=200)
        b = Q.period_snapshot(f, b_start.isoformat(), b_end.isoformat(), top=200)
        if a.empty or b.empty:
            ui.empty_state("One of the periods has no data.")
            return

        names_a, names_b = set(a["name"]), set(b["name"])
        merged = a.merge(b, on=["article_id", "name"], how="outer",
                         suffixes=("_a", "_b")).fillna({"total_views_a": 0,
                                                        "total_views_b": 0})
        merged["change"] = merged["total_views_a"] - merged["total_views_b"]

        ui.kpis([
            ("Period A", f"{a_start} → {a_end}", ui.human(a['total_views'].sum())),
            ("Period B", f"{b_start} → {b_end}", ui.human(b['total_views'].sum())),
            ("Only in A", f"{len(names_a - names_b):,}", "new attention"),
            ("Only in B", f"{len(names_b - names_a):,}", "faded since"),
        ])

        left, right = st.columns(2)
        with left:
            st.markdown("**Risen in A relative to B**")
            up = merged.nlargest(12, "change")
            fig = charts.hbar([n[:32] for n in up["name"]], up["change"],
                              text=[ui.human(v) for v in up["change"]],
                              color=charts.STATUS["up"], height=380,
                              x_title="extra views in period A")
            st.plotly_chart(fig, use_container_width=True, key="cmp_up")
        with right:
            st.markdown("**Faded in A relative to B**")
            down = merged.nsmallest(12, "change")
            fig = charts.hbar([n[:32] for n in down["name"]], down["change"].abs(),
                              text=[ui.human(abs(v)) for v in down["change"]],
                              color=charts.STATUS["down"], height=380,
                              x_title="views lost since period B")
            st.plotly_chart(fig, use_container_width=True, key="cmp_down")

        st.markdown("**Side by side**")
        board = st.columns(2)
        with board[0]:
            st.caption(f"Period A · {a_start} → {a_end}")
            _leaderboard(a.head(15), key="cmp_a", height=380)
        with board[1]:
            st.caption(f"Period B · {b_start} → {b_end}")
            _leaderboard(b.head(15), key="cmp_b", height=380)
