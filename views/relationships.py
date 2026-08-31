"""Relationships — which articles rise and fall together."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from src import charts, queries as Q, ui
from src.relationships import SERIES_MODES, correlation_pairs, top_related


def render() -> None:
    f: Q.Filters = st.session_state["filters"]
    ui.page_header(
        "Relationships",
        "Correlation between articles' daily attention. If two articles surge on the "
        "same days, something is connecting them.")

    controls = st.columns([3, 3, 2])
    with controls[0]:
        mode_label = st.radio("Compare", list(SERIES_MODES.values()), index=0)
        mode = [k for k, v in SERIES_MODES.items() if v == mode_label][0]
    with controls[1]:
        remove_factor = st.toggle(
            "Remove site-wide trend", value=True,
            help="Subtracts each day's cross-sectional mean, so shared weekly rhythm "
                 "and seasonal drift do not make every pair look related.")
        pool = st.slider("Articles considered", 100, 800, 400, step=50)
    with controls[2]:
        minimum = st.slider("Minimum r", 0.0, 0.95, 0.5, step=0.05)

    corr = Q.load_correlation(f, mode, remove_factor, pool)
    if corr.empty:
        ui.empty_state(
            "Not enough overlapping history to correlate.",
            "Widen the date range or lower *Minimum days observed* in the sidebar.")
        return

    titles = Q.title_map(f.db_path)
    values = corr.to_numpy()
    upper = np.triu_indices_from(values, 1)
    finite = values[upper][~np.isnan(values[upper])]

    ui.kpis([
        ("Articles compared", f"{corr.shape[0]:,}", None),
        ("Pairs scored", f"{len(finite):,}", None),
        ("Median correlation", f"{np.median(finite):.3f}" if len(finite) else "—",
         "near zero is healthy"),
        (f"Pairs above {minimum:.2f}", f"{int((finite >= minimum).sum()):,}", None),
    ])

    tabs = st.tabs(["Strongest pairs", "One article's neighbours", "Correlation matrix"])

    with tabs[0]:
        pairs = correlation_pairs(corr, top=400)
        pairs = pairs[pairs["weight"] >= minimum].head(40)
        if pairs.empty:
            ui.empty_state("No pair clears that threshold.")
        else:
            pairs["A"] = pairs["source_id"].map(titles).str.replace("_", " ")
            pairs["B"] = pairs["target_id"].map(titles).str.replace("_", " ")
            st.dataframe(
                pairs[["A", "B", "weight"]], hide_index=True, use_container_width=True,
                height=480,
                column_config={
                    "A": st.column_config.TextColumn("Article", width="medium"),
                    "B": st.column_config.TextColumn("moves with", width="medium"),
                    "weight": st.column_config.ProgressColumn(
                        "r", min_value=0.0, max_value=1.0, format="%.3f"),
                })
            ui.caveat(
                "Correlations above ~0.99 usually mean mechanical traffic — sets of pages "
                "crawled together produce near-identical daily counts — rather than shared "
                "human interest. The genuinely interesting range is roughly 0.6–0.95.")

    with tabs[1]:
        catalogue = Q.searchable_articles(f)
        available = catalogue[catalogue["article_id"].isin(corr.index)]
        if available.empty:
            ui.empty_state("No eligible articles.")
        else:
            default = st.session_state.get("selected_article")
            options = available["name"].tolist()
            idx = options.index(default) if default in options else 0
            name = st.selectbox("Article", options, index=idx, key="rel_pick")
            st.session_state["selected_article"] = name
            aid = int(available[available["name"] == name].iloc[0]["article_id"])

            related = top_related(corr, aid, k=12, minimum=minimum)
            if related.empty:
                ui.empty_state("Nothing correlates with this article above the threshold.")
            else:
                related["title"] = related["article_id"].map(titles)
                left, right = st.columns([2, 3])
                with left:
                    fig = charts.hbar(
                        [t.replace("_", " ")[:30] for t in related["title"]],
                        related["correlation"],
                        text=[f"{v:.2f}" for v in related["correlation"]],
                        height=max(260, 32 * len(related)), x_title="correlation")
                    fig.update_xaxes(range=[0, 1])
                    st.plotly_chart(fig, use_container_width=True, key="rel_bar")
                with right:
                    st.markdown("**Series comparison** — each scaled to its own maximum")
                    picks = [aid] + related["article_id"].head(3).tolist()
                    frames = []
                    for i, other in enumerate(picks):
                        series = Q.article_series(f, other)
                        obs = series[series["observed"]]
                        if obs.empty:
                            continue
                        frames.append({
                            "x": obs["date"],
                            "y": obs["views"] / obs["views"].max(),
                            "name": titles.get(other, str(other)).replace("_", " ")[:28],
                            "color": charts.palette()[i % 8],
                            "width": 3 if other == aid else 2,
                            "opacity": 1.0 if other == aid else 0.75,
                            "hovertemplate": "%{y:.0%} of own peak<extra>%{fullData.name}</extra>",
                        })
                    fig = charts.timeseries(frames, height=330, y_title="share of own peak")
                    fig.update_yaxes(tickformat=".0%")
                    st.plotly_chart(fig, use_container_width=True, key="rel_series")

    with tabs[2]:
        size = st.slider("Matrix size", 10, 40, 24, step=2,
                         help="The most-viewed articles in the window.")
        sizes = Q.node_sizes(f).reindex(corr.index).fillna(0)
        chosen = sizes.nlargest(size).index
        block = corr.loc[chosen, chosen]
        labels = [titles.get(i, str(i)).replace("_", " ")[:26] for i in chosen]
        fig = charts.heatmap(block, labels=labels, height=max(420, 20 * len(chosen)))
        st.plotly_chart(fig, use_container_width=True, key="rel_matrix")
        ui.caveat(
            "Blue is co-movement, red is opposite movement, grey is unrelated. "
            "Blank cells are pairs without enough shared observed days to score.")

    ui.metric_help()
