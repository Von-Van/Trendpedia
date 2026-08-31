"""Wikipedia Attention Atlas — an interactive map of what the world is reading.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import database as db          # noqa: E402
from src import queries as Q            # noqa: E402
from src.config import DB_PATH, MIN_OBSERVATIONS  # noqa: E402

st.set_page_config(
    page_title="Wikipedia Attention Atlas",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB = str(DB_PATH)


# --- first run ---------------------------------------------------------------

def onboarding() -> None:
    """Shown when the dataset is empty: explain the one command that fixes it."""
    st.title("🌐 Wikipedia Attention Atlas")
    st.markdown(
        "This dashboard explores how public attention moves across Wikipedia over "
        "time — what is surging, what is fading, and which topics rise together.\n\n"
        "**There is no data yet.** The pageviews API serves historical days, so you "
        "can build a useful dataset in a couple of minutes rather than waiting weeks."
    )
    st.code("python scripts/collect_daily.py --backfill 90", language="bash")
    st.caption("Then keep it current with a daily `python scripts/collect_daily.py`.")

    st.divider()
    st.markdown("#### …or collect it right here")
    days = st.slider("Days of history to fetch", 14, 180, 90, step=7)
    if st.button("Start collecting", type="primary"):
        from src.collector import backfill_range
        bar = st.progress(0.0, text="Contacting Wikimedia…")

        def tick(done: int, total: int, day: str) -> None:
            bar.progress(done / total, text=f"Collected {day}  ({done}/{total} days)")

        result = backfill_range(days, db_path=DB, progress=tick)
        bar.empty()
        if result.collected:
            st.success(f"{result}. Loading the dashboard…")
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(f"Collection failed. {result}")
            for err in result.errors[:5]:
                st.caption(err)


# --- sidebar -----------------------------------------------------------------

def sidebar_filters() -> Q.Filters:
    lo, hi = db.date_bounds(DB)
    lo_d, hi_d = date.fromisoformat(lo), date.fromisoformat(hi)

    with st.sidebar:
        st.markdown("#### Filters")
        preset = st.radio(
            "Window", ["Last 30 days", "Last 60 days", "Last 90 days", "All", "Custom"],
            index=1, horizontal=False, label_visibility="collapsed",
        )
        if preset == "Custom":
            picked = st.date_input("Date range", value=(max(lo_d, hi_d - timedelta(days=59)), hi_d),
                                   min_value=lo_d, max_value=hi_d)
            if isinstance(picked, tuple) and len(picked) == 2:
                start, end = picked
            else:
                start, end = lo_d, hi_d
        elif preset == "All":
            start, end = lo_d, hi_d
        else:
            span = int(preset.split()[1])
            end = hi_d
            start = max(lo_d, end - timedelta(days=span - 1))

        st.caption(f"{start} → {end}  ·  {(end - start).days + 1} days")

        min_views = st.select_slider(
            "Minimum pageviews", options=[0, 1_000, 5_000, 10_000, 50_000, 100_000],
            value=0, format_func=lambda v: "any" if v == 0 else f"{v:,}",
            help="Filters out articles that never reached this many views in a day.")
        min_obs = st.slider(
            "Minimum days observed", 3, 60, MIN_OBSERVATIONS,
            help="Articles need this many days in the top-1000 before they are "
                 "eligible for correlation and community analysis.")
        mainspace = st.toggle(
            "Encyclopedia articles only", value=True,
            help="Hides Main Page, Special:Search and other non-article traffic, "
                 "which otherwise dominates every ranking.")

        st.divider()
        stats = Q.dataset_stats(DB)
        st.caption(
            f"**Dataset** · {int(stats.get('days', 0))} days · "
            f"{int(stats.get('articles', 0)):,} articles · "
            f"{int(stats.get('observations', 0)):,} observations")
        st.caption(f"{stats.get('first_date', '—')} → {stats.get('last_date', '—')}")
        if st.button("Refresh cache", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    return Q.Filters(
        db_path=DB, start=start.isoformat(), end=end.isoformat(),
        mainspace_only=mainspace, min_views=min_views, min_observations=min_obs,
    )


# --- entry point -------------------------------------------------------------

def main() -> None:
    db.init_db(DB)
    if not db.has_data(DB):
        onboarding()
        return

    # Page modules live in `views/`, not `pages/`: Streamlit treats a `pages/`
    # folder beside the main script as its legacy multipage convention and would
    # build its own navigation from the filenames before app.py ever runs.
    from views import (article_explorer, attention_map, historical, lifecycles,
                       overview, relationships, trending)

    nav_pages = [
        st.Page(overview.render, title="Overview", url_path="overview",
                icon=":material/dashboard:", default=True),
        st.Page(article_explorer.render, title="Article Explorer", url_path="article",
                icon=":material/search:"),
        st.Page(trending.render, title="Trending", url_path="trending",
                icon=":material/trending_up:"),
        st.Page(lifecycles.render, title="Lifecycles", url_path="lifecycles",
                icon=":material/timeline:"),
        st.Page(relationships.render, title="Relationships", url_path="relationships",
                icon=":material/hub:"),
        st.Page(attention_map.render, title="Attention Map", url_path="map",
                icon=":material/scatter_plot:"),
        st.Page(historical.render, title="Historical Explorer", url_path="historical",
                icon=":material/history:"),
    ]
    nav = st.navigation(nav_pages)

    # Expose the Page objects so a page can hand off to another one:
    # st.switch_page needs the object, not a title.
    st.session_state["pages"] = {page.url_path: page for page in nav_pages}

    st.session_state["filters"] = sidebar_filters()
    nav.run()


if __name__ == "__main__":
    main()
