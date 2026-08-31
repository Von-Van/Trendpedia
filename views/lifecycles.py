"""Lifecycles — the *shape* of attention, with each article scaled to its own peak."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src import charts, queries as Q, ui

SHAPES = ["flash spike", "gradual burn", "delayed growth",
          "recurring interest", "long-lived trend", "steady interest"]

BLURB = {
    "flash spike": "One dramatic day, then gone. A news event with no follow-through.",
    "gradual burn": "Peaks, then holds a meaningful share of that peak for days.",
    "delayed growth": "Little attention before the peak — the story arrived from nowhere.",
    "recurring interest": "Swings up and down repeatedly rather than peaking once.",
    "long-lived trend": "Sustained elevated attention with no single dominant day.",
    "steady interest": "Evergreen. Attention barely moves.",
}


def render() -> None:
    f: Q.Filters = st.session_state["filters"]
    ui.page_header(
        "Lifecycles",
        "Every article re-centred on its own peak day and scaled to its own maximum, "
        "so a 4M-view story and a 40k-view one can be compared as shapes.")

    window = st.slider("Days either side of the peak", 5, 30, 14)
    data = Q.load_lifecycles(f, window=window)
    shapes, summary = data["shapes"], data["summary"]
    if shapes.empty or summary.empty:
        ui.empty_state("Not enough history in this window to trace lifecycles.")
        return

    eligible = summary[summary["observations"] >= max(5, f.min_observations)]
    if eligible.empty:
        ui.empty_state("No article has enough observed days.",
                       "Lower *Minimum days observed* in the sidebar.")
        return

    controls = st.columns([3, 2])
    with controls[0]:
        picked = st.multiselect(
            "Shapes to show", SHAPES,
            default=[s for s in SHAPES if s in set(eligible["lifecycle"])][:3])
    with controls[1]:
        count = st.slider("Articles per shape", 3, 30, 12)

    subset = eligible[eligible["lifecycle"].isin(picked)] if picked else eligible
    if subset.empty:
        ui.empty_state("No articles with those shapes under the current filters.")
        return

    st.divider()
    st.markdown("**Attention shapes, normalised to each article's peak**")

    colors = charts.palette()
    frames, archetypes = [], []
    for i, shape in enumerate([s for s in SHAPES if s in set(subset["lifecycle"])]):
        members = (subset[subset["lifecycle"] == shape]
                   .nlargest(count, "total_views")["article_id"])
        curves = shapes[shapes["article_id"].isin(members)]
        if curves.empty:
            continue
        color = colors[i % len(colors)]
        for j, (aid, curve) in enumerate(curves.groupby("article_id")):
            curve = curve.sort_values("days_from_peak")
            frames.append({
                "x": curve["days_from_peak"], "y": curve["relative_views"],
                "name": shape, "color": color, "width": 1, "opacity": 0.22,
                "showlegend": False,
                "hovertemplate": f"{curve['name'].iloc[0]}<br>day %{{x}}: %{{y:.0%}} of peak<extra></extra>",
            })
        median = curves.groupby("days_from_peak")["relative_views"].median()
        archetypes.append({"x": median.index, "y": median.values, "name": shape,
                           "color": color, "width": 3})

    fig = charts.timeseries(frames + archetypes, height=430,
                            y_title="share of the article's own peak")
    fig.update_layout(hovermode="closest")
    fig.update_xaxes(title_text="days from peak", zeroline=True,
                     zerolinecolor=charts.INK[charts.active_mode()]["axis"], zerolinewidth=1)
    fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(fig, use_container_width=True, key="lc_curves")
    ui.caveat(
        "Faint lines are individual articles; the heavy line is each shape's median curve. "
        "Shapes are assigned by simple thresholds on peak ratio, persistence and how much "
        "attention survives a week after the peak — a description, not a model.")

    st.divider()
    cols = st.columns(min(3, max(1, len(picked) or 3)))
    for col, shape in zip(cols, (picked or SHAPES)[:3]):
        with col:
            n = int((eligible["lifecycle"] == shape).sum())
            st.metric(shape.title(), f"{n:,} articles")
            st.caption(BLURB[shape])

    st.divider()
    st.markdown("**Articles by shape**")
    display = ui.article_frame(subset.nlargest(60, "total_views"))
    display["Shape"] = display["lifecycle"]
    display["Peak ratio"] = display["peak_ratio"]
    display["Persistence"] = display["persistence"]
    display["Total"] = display["total_views"]
    ui.articles_table(display, {
        "Name": st.column_config.TextColumn("Article", width="medium"),
        "Shape": st.column_config.TextColumn("Lifecycle"),
        "Peak ratio": st.column_config.NumberColumn("Peak ÷ median", format="%.1fx"),
        "Persistence": st.column_config.ProgressColumn(
            "Persistence", min_value=0.0, max_value=1.0, format="%.2f"),
        "Total": ui.count("Total views"),
    }, height=420)

    ui.metric_help()
