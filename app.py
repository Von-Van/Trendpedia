"""Wikipedia Attention Atlas — an interactive map of what the world is reading.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import data_panel             # noqa: E402
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
    """First run. One button, no terminal required."""
    st.title("🌐 Wikipedia Attention Atlas")
    st.markdown(
        "This dashboard explores how public attention moves across Wikipedia over "
        "time — what is surging, what is fading, and which topics rise together.\n\n"
        "**Let's get some data.** Wikipedia publishes its history, so there is no "
        "waiting: a couple of minutes from now you will have months of it."
    )

    left, right = st.columns([2, 3])
    with left:
        days = st.slider("How much history?", 14, 180, 90, step=7,
                         format="%d days")
        minutes = max(1, round(days * 0.6 / 60))
        st.caption(f"About {minutes} minute{'s' if minutes != 1 else ''} to fetch.")
    with right:
        st.markdown("&nbsp;")
        if st.button("Collect data now", type="primary", use_container_width=True):
            data_panel.onboarding_fetch(DB, days)
        st.caption("You can add more history later, from the sidebar.")

    with st.expander("Prefer the command line?"):
        st.code("python scripts/collect_daily.py --backfill 90", language="bash")
        st.caption("Does exactly the same thing. Updating is always your call — "
                   "nothing downloads on its own.")


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
        data_panel.render(DB)

        stats = Q.dataset_stats(DB)
        st.caption(
            f"{int(stats.get('articles', 0)):,} articles · "
            f"{int(stats.get('observations', 0)):,} observations")

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
