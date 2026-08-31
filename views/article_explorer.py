"""Article Explorer — everything the dataset knows about one article."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from src import charts, queries as Q, ui
from src.config import article_url, pretty_title


def render() -> None:
    f: Q.Filters = st.session_state["filters"]
    ui.page_header("Article Explorer",
                   "Search for an article and inspect its attention history.")

    catalogue = Q.searchable_articles(f)
    if catalogue.empty:
        ui.empty_state("No articles in this window.", "Widen the date range.")
        return

    default = st.session_state.get("selected_article")
    options = catalogue["name"].tolist()
    index = options.index(default) if default in options else 0
    name = st.selectbox(f"Article  ({len(options):,} available)", options, index=index,
                        key="explorer_pick")
    st.session_state["selected_article"] = name

    article_id = Q.resolve_article(f, name)
    if article_id is None:
        ui.empty_state("Could not resolve that article.")
        return

    series = Q.article_series(f, article_id)
    row = catalogue[catalogue["article_id"] == article_id].iloc[0]
    facts = Q.load_summary(f)
    facts = facts[facts["article_id"] == article_id]
    facts = facts.iloc[0] if not facts.empty else None
    observed = series[series["observed"]]

    if facts is None or observed.empty:
        ui.empty_state("No observations for this article in the window.")
        return

    st.markdown(f"#### [{name}]({article_url(row['title'])})")

    latest = observed.iloc[-1]
    ui.kpis([
        ("Peak day", ui.human(facts["peak_views"]), str(facts["peak_date"])),
        ("Latest views", ui.human(latest["views"]),
         ui.pct(latest["velocity"]) + " vs baseline" if pd.notna(latest["velocity"]) else None),
        ("Best rank", f"#{int(facts['best_rank'])}" if pd.notna(facts["best_rank"]) else "—",
         f"now #{int(latest['daily_rank'])}" if pd.notna(latest["daily_rank"]) else "outside top-1000"),
        ("First seen", str(facts["first_day"]), f"{int(facts['observations'])} days observed"),
    ])

    # --- main series
    st.markdown("**Pageviews over time**")
    frames = [
        {"x": observed["date"], "y": observed["views"], "name": "Daily views",
         "color": charts.palette()[0], "width": 2},
        {"x": series["date"], "y": series["ma7"], "name": "7-day average",
         "color": charts.palette()[1], "width": 2, "dash": "dot"},
    ]
    fig = charts.timeseries(frames, height=360)
    peak_row = observed.loc[observed["views"].idxmax()]
    fig.add_annotation(x=peak_row["date"], y=peak_row["views"], text="peak",
                       showarrow=True, arrowhead=0, ay=-28, arrowcolor=charts.INK[charts.active_mode()]["muted"],
                       font=dict(size=11, color=charts.INK[charts.active_mode()]["secondary"]))
    st.plotly_chart(fig, use_container_width=True, key="ae_series")

    gaps = int((~series["observed"]).sum())
    if gaps:
        ui.caveat(
            f"{gaps} day(s) in this window fall outside the top-1000, so the true value "
            "is unknown but below that day's cutoff. Use *Fill true history* below to "
            "fetch the exact numbers for this article.")

    # --- derived metrics
    st.divider()
    st.markdown("**Derived metrics**")
    m1, m2 = st.columns(2)
    with m1:
        vel = series.dropna(subset=["velocity"])
        if vel.empty:
            ui.empty_state("Not enough history yet for velocity.")
        else:
            fig = charts.timeseries([{
                "x": vel["date"], "y": vel["velocity"], "name": "Velocity",
                "color": charts.palette()[2],
                "hovertemplate": "%{y:+.1%}<extra>velocity</extra>"}],
                height=250, y_title="vs 7-day baseline")
            fig.add_hline(y=0, line_width=1, line_color=charts.INK[charts.active_mode()]["axis"])
            st.plotly_chart(fig, use_container_width=True, key="ae_vel")
            ui.caveat("Above zero: attention is running hotter than the article's own recent normal.")
    with m2:
        anomaly = series.dropna(subset=["anomaly_z"])
        if anomaly.empty:
            ui.empty_state("Not enough history yet for anomaly scores.")
        else:
            fig = charts.timeseries([{
                "x": anomaly["date"], "y": anomaly["anomaly_z"], "name": "Anomaly",
                "color": charts.palette()[4],
                "hovertemplate": "%{y:.1f}σ<extra>anomaly</extra>"}],
                height=250, y_title="σ above baseline")
            fig.add_hline(y=3, line_width=1, line_dash="dot", line_color=charts.STATUS["down"])
            st.plotly_chart(fig, use_container_width=True, key="ae_anom")
            ui.caveat("Dotted line marks 3σ — a genuinely unusual day for this article.")

    stats = st.columns(4)
    stats[0].metric("Peak ratio", f"{facts['peak_ratio']:.1f}×",
                    help="Peak views ÷ median views. High means one dramatic day.")
    stats[1].metric("Persistence", f"{facts['persistence']:.2f}",
                    help="Share of days holding ≥25% of its own peak.")
    stats[2].metric("Volatility", f"{facts['volatility']:.2f}",
                    help="Standard deviation ÷ mean. Scale-free.")
    stats[3].metric("Total attention", ui.human(facts["total_views"]),
                    help="Sum of observed daily views in this window.")

    # --- related
    st.divider()
    st.markdown("**Articles with similar attention patterns**")
    corr = Q.load_correlation(f, "levels", True, 500)
    if corr.empty or article_id not in corr.index:
        ui.empty_state(
            "This article has too few observed days for correlation.",
            "Lower *Minimum days observed* in the sidebar, or widen the window.")
    else:
        from src.relationships import top_related
        related = top_related(corr, article_id, k=10, minimum=0.3)
        if related.empty:
            ui.empty_state("No article moves closely with this one under the current filters.")
        else:
            titles = Q.title_map(f.db_path)
            related["title"] = related["article_id"].map(titles)
            display = ui.article_frame(related)
            display["r"] = related["correlation"]
            ui.articles_table(display, {
                "Name": st.column_config.TextColumn("Article", width="medium"),
                "r": st.column_config.ProgressColumn("Correlation", min_value=0.0,
                                                     max_value=1.0, format="%.3f"),
            }, height=300)

    # --- true-history backfill
    st.divider()
    with st.expander("Fill true history for this article"):
        st.markdown(
            "The daily collector only sees articles while they are in the top-1000. "
            "This fetches this article's exact daily pageviews from the per-article "
            "endpoint and fills the gaps — the rest of the dataset is untouched.")
        span = st.slider("Days of history", 30, 365, 180, step=30, key="ae_backfill_days")
        if st.button("Fetch true history", key="ae_backfill"):
            from datetime import timedelta
            from src.collector import backfill_article
            end = date.fromisoformat(f.end)
            with st.spinner("Asking Wikimedia…"):
                try:
                    written = backfill_article(row["title"], end - timedelta(days=span), end,
                                               db_path=f.db_path)
                    st.success(f"Wrote {written} day(s) of exact pageviews.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not fetch: {exc}")

    ui.metric_help()
