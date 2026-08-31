"""Overview — the state of public attention at a glance."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src import charts, queries as Q, ui


def _movers_table(frame: pd.DataFrame, value_label: str) -> None:
    if frame.empty:
        ui.empty_state("No articles qualify under the current filters.")
        return
    display = ui.article_frame(frame)
    display["Views"] = display["views"]
    display["Baseline"] = display["baseline"]
    display["Change"] = display["velocity"]
    ui.articles_table(display, {
        "Name": st.column_config.TextColumn("Article", width="medium"),
        "Views": ui.count(value_label),
        "Baseline": ui.count("7-day baseline"),
        "Change": st.column_config.NumberColumn("vs baseline", format="percent"),
    }, height=340)


def render() -> None:
    f: Q.Filters = st.session_state["filters"]
    ui.page_header(
        "Overview",
        "What Wikipedia readers are paying attention to, and how that is shifting.")

    summary = Q.load_summary(f)
    totals = Q.daily_totals(f)
    if summary.empty or totals.empty:
        ui.empty_state("No data in this window.", "Widen the date range in the sidebar.")
        return

    latest = Q.latest_day(f)
    movers = Q.day_movers(f, latest, top=15)

    # --- KPIs
    span_views = int(totals["total_views"].sum())
    last_day_views = int(totals.iloc[-1]["total_views"])
    prev_day_views = int(totals.iloc[-2]["total_views"]) if len(totals) > 1 else None
    delta = (f"{(last_day_views / prev_day_views - 1) * 100:+.1f}% vs previous day"
             if prev_day_views else None)
    top_gainer = movers["gainers"].iloc[0] if not movers["gainers"].empty else None

    ui.kpis([
        ("Articles tracked", f"{summary['article_id'].nunique():,}", None),
        ("Attention represented", ui.human(span_views), f"over {len(totals)} days"),
        ("Latest day", str(pd.Timestamp(latest).date()), delta),
        ("Fastest riser",
         top_gainer["name"] if top_gainer is not None else "—",
         ui.pct(top_gainer["velocity"]) if top_gainer is not None else None),
    ])

    st.divider()

    # --- daily pulse
    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Daily attention across tracked articles**")
        fig = charts.timeseries([{
            "x": totals["date"], "y": totals["total_views"], "name": "Total pageviews",
            "fill": "tozeroy", "fillcolor": "rgba(42,120,214,0.10)",
            "hovertemplate": "%{y:,.0f} views<extra></extra>",
        }], height=320)
        st.plotly_chart(fig, use_container_width=True, key="ov_totals")
        ui.caveat(
            "Sum of every article in the daily top-1000. The weekly sawtooth is real: "
            "Wikipedia is quieter at weekends.")
    with right:
        st.markdown("**Top articles on the latest day**")
        top = movers["top"].head(10)
        if top.empty:
            ui.empty_state("Nothing recorded for this day.")
        else:
            fig = charts.hbar(
                [n[:34] for n in top["name"]], top["views"],
                text=[ui.human(v) for v in top["views"]], height=320)
            st.plotly_chart(fig, use_container_width=True, key="ov_top")

    st.divider()

    # --- movers
    st.markdown(f"**Movers on {pd.Timestamp(latest).date()}**")
    tabs = st.tabs(["Biggest gainers", "Biggest losers", "New entrants",
                    "Longest-running"])
    with tabs[0]:
        _movers_table(movers["gainers"], "Views")
        ui.caveat("Ranked by views today against the article's own trailing 7-day mean.")
    with tabs[1]:
        _movers_table(movers["losers"], "Views")
        ui.caveat("Attention falling fastest relative to its own recent normal.")
    with tabs[2]:
        newcomers = movers["new"]
        if newcomers.empty:
            ui.empty_state("No article entered the dataset for the first time on this day.")
        else:
            display = ui.article_frame(newcomers)
            display["Views"] = display["views"]
            ui.articles_table(display, {
                "Name": st.column_config.TextColumn("Article", width="medium"),
                "Views": ui.count("Views"),
            }, height=340)
            ui.caveat("First appearance in the top-1000 since collection began.")
    with tabs[3]:
        longest = summary.nlargest(15, "observations")
        display = ui.article_frame(longest)
        display["Days"] = display["observations"]
        display["Total"] = display["total_views"]
        display["Persistence"] = display["persistence"]
        ui.articles_table(display, {
            "Name": st.column_config.TextColumn("Article", width="medium"),
            "Days": st.column_config.NumberColumn("Days in top-1000", format="%d"),
            "Total": ui.count("Total views"),
            "Persistence": st.column_config.ProgressColumn(
                "Persistence", min_value=0.0, max_value=1.0, format="%.2f"),
        }, height=340)
        ui.caveat("Articles that hold attention rather than spiking — the evergreen tail.")

    ui.metric_help()
