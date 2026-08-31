"""Trending — articles running unusually hot against their own baseline."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src import charts, queries as Q, ui

MEASURES = {
    "Surge ratio (views ÷ 7-day baseline)": ("baseline_ratio", "%.2fx"),
    "Anomaly score (σ above 28-day normal)": ("anomaly_z", "%.1f"),
    "Velocity (% above baseline)": ("velocity", "percent"),
    "Acceleration (velocity change)": ("acceleration", "%.2f"),
}


def render() -> None:
    f: Q.Filters = st.session_state["filters"]
    ui.page_header(
        "Trending",
        "Articles whose attention today is unusual *for them* — not merely large.")

    frame = Q.load_metrics(f)
    if frame.empty:
        ui.empty_state("No data in this window.")
        return

    controls = st.columns([3, 2, 2])
    with controls[0]:
        measure_label = st.selectbox("Rank by", list(MEASURES), index=0)
    measure, fmt = MEASURES[measure_label]
    with controls[1]:
        days = st.slider("Look back", 1, 14, 1,
                         help="Consider the best day for each article within this "
                              "many days of the window's end.")
    with controls[2]:
        limit = st.slider("Show", 5, 50, 15, step=5)

    last = frame["date"].max()
    recent = frame[frame["date"] > last - pd.Timedelta(days=days)]
    recent = recent[recent["observed"] & recent[measure].notna()]
    if f.min_views:
        recent = recent[recent["views"] >= f.min_views]

    counts = frame.groupby("article_id")["observed"].sum()
    eligible = counts[counts >= max(3, f.min_observations // 2)].index
    recent = recent[recent["article_id"].isin(eligible)]

    if recent.empty:
        ui.empty_state(
            "Nothing qualifies under these filters.",
            "Try lowering *Minimum days observed* or *Minimum pageviews* in the sidebar.")
        return

    best = recent.loc[recent.groupby("article_id")[measure].idxmax()]
    titles = Q.title_map(f.db_path)
    best = best.assign(title=best["article_id"].map(titles))
    top = best.nlargest(limit, measure).copy()

    tabs = st.tabs(["Ranked", "One-day spikes", "New entrants"])

    with tabs[0]:
        spark = Q.sparklines(f, tuple(top["article_id"]), days=14)
        display = ui.article_frame(top)
        display["Trend"] = display["article_id"].map(lambda a: spark.get(int(a), []))
        display["Score"] = display[measure]
        display["Views"] = display["views"]
        display["Baseline"] = display["baseline"]
        display["When"] = display["date"].dt.strftime("%b %d")
        ui.articles_table(display, {
            "Name": st.column_config.TextColumn("Article", width="medium"),
            "Trend": st.column_config.LineChartColumn("Last 14 days", width="small"),
            "Score": st.column_config.NumberColumn(measure_label.split(" (")[0], format=fmt),
            "Views": ui.count("Views"),
            "Baseline": ui.count("Baseline"),
            "When": st.column_config.TextColumn("Peak day"),
        }, height=min(560, 60 + 35 * len(top)))
        ui.caveat(
            "Every measure compares an article against its own recent history, so a "
            "steady 4M-view page never appears here but a 20k page tripling does.")

        chosen = st.selectbox("Inspect a series", top["title"].map(
            lambda t: t.replace("_", " ")).tolist(), key="tr_pick")
        aid = int(top[top["title"].str.replace("_", " ") == chosen].iloc[0]["article_id"])
        series = Q.article_series(f, aid)
        obs = series[series["observed"]]
        fig = charts.timeseries([
            {"x": obs["date"], "y": obs["views"], "name": "Daily views",
             "color": charts.palette()[0]},
            {"x": series["date"], "y": series["baseline"], "name": "7-day baseline",
             "color": charts.palette()[1], "dash": "dot"},
        ], height=280)
        st.plotly_chart(fig, use_container_width=True, key="tr_series")

    with tabs[1]:
        jumps = recent.copy()
        jumps["jump"] = jumps["views"] - jumps["baseline"]
        jumps = jumps.dropna(subset=["jump"]).nlargest(limit, "jump")
        jumps["title"] = jumps["article_id"].map(titles)
        if jumps.empty:
            ui.empty_state("No spikes recorded.")
        else:
            fig = charts.hbar(
                [t.replace("_", " ")[:34] for t in jumps["title"]], jumps["jump"],
                text=[ui.human(v) for v in jumps["jump"]],
                color=charts.palette()[1], height=max(260, 30 * len(jumps)),
                x_title="extra views above baseline")
            st.plotly_chart(fig, use_container_width=True, key="tr_jumps")
            ui.caveat("Absolute surge, not ratio — this favours already-large articles.")

    with tabs[2]:
        articles = Q.load_titles(f.db_path)
        entered = articles[(articles["first_seen"] >= f.start) &
                           (articles["first_seen"] <= f.end)]
        if f.mainspace_only:
            entered = entered[entered["is_mainspace"] == 1]
        summary = Q.load_summary(f)
        entered = entered.merge(summary[["article_id", "total_views", "peak_views",
                                         "observations"]], on="article_id", how="inner")
        entered = entered.nlargest(limit, "peak_views")
        if entered.empty:
            ui.empty_state("No new articles entered the dataset in this window.")
        else:
            display = ui.article_frame(entered)
            display["Peak"] = display["peak_views"]
            display["Days"] = display["observations"]
            display["Since"] = display["first_seen"]
            ui.articles_table(display, {
                "Name": st.column_config.TextColumn("Article", width="medium"),
                "Peak": ui.count("Peak views"),
                "Days": st.column_config.NumberColumn("Days observed", format="%d"),
                "Since": st.column_config.TextColumn("First seen"),
            }, height=min(560, 60 + 35 * len(entered)))
            ui.caveat("Articles reaching the top-1000 for the first time since collection began.")

    ui.metric_help()
