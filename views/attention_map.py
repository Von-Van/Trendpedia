"""Attention Map — the whole correlation network as one picture."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src import charts, communities as C, queries as Q, ui
from src.relationships import SERIES_MODES


def render() -> None:
    f: Q.Filters = st.session_state["filters"]
    ui.page_header(
        "Attention Map",
        "Articles are linked when their attention moves together; the layout pulls "
        "connected articles close, and Louvain groups the clusters that result.")

    with st.expander("Graph settings", expanded=False):
        c = st.columns(3)
        with c[0]:
            mode_label = st.radio("Compare", list(SERIES_MODES.values()), index=0,
                                  key="map_mode")
            mode = [k for k, v in SERIES_MODES.items() if v == mode_label][0]
            remove_factor = st.toggle("Remove site-wide trend", value=True, key="map_factor")
        with c[1]:
            pool = st.slider("Articles considered", 100, 800, 400, step=50, key="map_pool")
            threshold = st.slider("Minimum edge strength", 0.3, 0.95, 0.6, step=0.05,
                                  key="map_thr")
        with c[2]:
            top_k = st.slider("Links kept per article", 2, 15, 6, key="map_k",
                              help="Stops hub articles from connecting to everything "
                                   "and collapsing the layout into one blob.")
            resolution = st.slider("Community granularity", 0.5, 2.0, 1.0, step=0.1,
                                   key="map_res",
                                   help="Higher values split the map into more, smaller groups.")

    graph = Q.load_graph(f, mode, remove_factor, pool, threshold, top_k, resolution)
    nodes, edges, summary = graph["nodes"], graph["edges"], graph["summary"]

    if nodes.empty:
        ui.empty_state(
            "No article pairs clear that edge strength.",
            "Lower *Minimum edge strength*, widen the date range, or reduce "
            "*Minimum days observed* in the sidebar.")
        return

    shown = summary[summary["shown"]]
    labels = ["All groups"] + shown["label"].tolist()
    picks = st.columns([3, 2])
    with picks[0]:
        chosen_group = st.selectbox("Focus a group", labels, index=0)
    with picks[1]:
        focus = st.selectbox("Highlight an article", ["—"] + sorted(nodes["name"]),
                             index=0)

    view = nodes
    if chosen_group != "All groups":
        community = int(shown[shown["label"] == chosen_group].iloc[0]["community"])
        keep = set(nodes[nodes["community"] == community]["article_id"])
        view = nodes[nodes["article_id"].isin(keep)]
        edges_view = edges[edges["source_id"].isin(keep) & edges["target_id"].isin(keep)]
    else:
        edges_view = edges

    highlight = None
    if focus != "—":
        aid = int(nodes[nodes["name"] == focus].iloc[0]["article_id"])
        # The article and whatever it links to directly; everything else fades.
        network = C.build_graph(edges)
        highlight = set(C.ego_graph(network, aid, 1).nodes) or {aid}

    ui.kpis([
        ("Articles mapped", f"{len(view):,}", None),
        ("Connections", f"{len(edges_view):,}", None),
        ("Groups found", f"{summary['community'].nunique():,}",
         f"{len(shown)} coloured" if len(summary) > len(shown) else None),
        ("Median link strength",
         f"{edges_view['weight'].median():.2f}" if not edges_view.empty else "—", None),
    ])

    # One label per coloured group: enough to name every cluster on the map,
    # few enough that the labels do not collide in the dense middle. Focusing a
    # single group thins the map out and reveals the rest.
    fig = charts.network(view, edges_view, height=640, highlight=highlight,
                         label_top=view["group_label"].nunique())
    event = st.plotly_chart(fig, use_container_width=True, key="map_net",
                            on_select="rerun", selection_mode="points")
    ui.caveat(
        "Node size is total attention in the window; position comes from the layout, so "
        "proximity carries group identity as much as colour does. Groups beyond the "
        "eighth are drawn grey rather than given invented colours.")

    selected = None
    points = (event.get("selection", {}) or {}).get("points", []) if event else []
    if points:
        custom = points[0].get("customdata")
        if custom:
            selected = str(custom[0])

    if selected:
        st.divider()
        st.markdown(f"**{selected.replace('_', ' ')}**")
        row = nodes[nodes["title"] == selected]
        if not row.empty:
            aid = int(row.iloc[0]["article_id"])
            corr = graph["corr"]
            from src.relationships import top_related
            related = top_related(corr, aid, k=8, minimum=threshold)
            if not related.empty:
                titles = Q.title_map(f.db_path)
                related["title"] = related["article_id"].map(titles)
                display = ui.article_frame(related)
                display["r"] = related["correlation"]
                ui.articles_table(display, {
                    "Name": st.column_config.TextColumn("Moves with", width="medium"),
                    "r": st.column_config.ProgressColumn("r", min_value=0.0,
                                                         max_value=1.0, format="%.3f"),
                }, height=260)
            target = st.session_state.get("pages", {}).get("article")
            if target is not None and st.button("Open in Article Explorer"):
                st.session_state["selected_article"] = row.iloc[0]["name"]
                st.switch_page(target)

    st.divider()
    st.markdown("**Groups**")
    table = summary.copy()
    table["total_views"] = table["total_views"].round().astype("int64")
    table["Members"] = table["members"].map(
        lambda m: ", ".join(t.replace("_", " ") for t in m[:6]) +
                  (f" … +{len(m) - 6}" if len(m) > 6 else ""))
    st.dataframe(
        table[["label", "size", "total_views", "Members"]],
        hide_index=True, use_container_width=True, height=320,
        column_config={
            "label": st.column_config.TextColumn("Group", width="medium"),
            "size": st.column_config.NumberColumn("Articles", format="%d"),
            "total_views": st.column_config.NumberColumn("Total views", format="localized"),
            "Members": st.column_config.TextColumn("Members", width="large"),
        })
    ui.caveat(
        "Groups are named after their most-viewed members. That is a description of "
        "what is inside, not an inferred topic label.")
