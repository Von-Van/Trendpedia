"""Reusable Streamlit components shared by every page."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from .config import article_url, pretty_title


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)


def kpis(items: list[tuple]) -> None:
    """Row of KPI cards: (label, value, delta[, delta_color]).

    Most of our deltas are descriptive captions ("over 60 days", "now #14")
    rather than changes. Streamlit paints any delta green-with-an-up-arrow by
    default, which reads as "this went up" and is simply false for those. A
    delta is treated as a real change only when it carries an explicit sign.
    """
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        label, value, delta = item[0], item[1], item[2]
        signed = isinstance(delta, str) and delta.lstrip().startswith(("+", "-", "\u2212"))
        forced = item[3] if len(item) > 3 else None
        with col:
            if delta is not None and (forced == "normal" or (forced is None and signed)):
                st.metric(label, value, delta)
            else:
                # Streamlit still draws a direction arrow for `delta_color="off"`,
                # which implies a rise next to text like a date range. A caption
                # says the same thing without claiming a direction.
                st.metric(label, value)
                if delta:
                    st.caption(delta)


def empty_state(message: str, hint: str = "") -> None:
    st.info(message + (f"\n\n{hint}" if hint else ""))


def human(n: float | int | None, digits: int = 0) -> str:
    """Compact number formatting: 1.2M, 34.5k, 812."""
    if n is None or pd.isna(n):
        return "—"
    n = float(n)
    for cutoff, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= cutoff:
            return f"{n / cutoff:.1f}{suffix}"
    return f"{n:,.{digits}f}"


def pct(value: float | None, digits: int = 0) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value * 100:+.{digits}f}%"


# A compact trailing link. Extracting the title from the URL with a regex would
# render it with underscores next to the readable name — two columns saying the
# same thing worse.
ARTICLE_COLUMN = st.column_config.LinkColumn(
    "Wikipedia", help="Open this article on Wikipedia", display_text="open",
    width="small")


def count(label: str, help: str | None = None) -> dict:
    """Whole-number column with thousands separators.

    Streamlit's number format takes printf strings or a fixed set of presets;
    "%,d" is neither, and silently renders the raw float.
    """
    return st.column_config.NumberColumn(label, format="localized", help=help)


def article_frame(df: pd.DataFrame, title_col: str = "title") -> pd.DataFrame:
    """Add a clickable Wikipedia URL column, keeping the readable name."""
    out = df.copy()
    if title_col in out.columns:
        out["Article"] = out[title_col].map(article_url)
        out["Name"] = out[title_col].map(pretty_title)
    return out


def articles_table(df: pd.DataFrame, columns: dict, height: int | None = None,
                   key: str | None = None, selectable: bool = False):
    """Sortable table with a link-out column. `columns` maps df column -> config."""
    if df.empty:
        empty_state("Nothing matches these filters yet.")
        return None
    config = {**columns, "Article": ARTICLE_COLUMN}
    # The link column is a nicety; a frame without titles still renders.
    order = [c for c in columns if c in df.columns] + \
        (["Article"] if "Article" in df.columns else [])
    if not order:
        empty_state("Nothing to show for these columns.")
        return None

    df = df[order].copy()
    # Counts arrive as floats from rolling means; show them as whole numbers.
    for column, cfg in columns.items():
        if column in df.columns and isinstance(cfg, dict) and \
                cfg.get("type_config", {}).get("format") == "localized":
            df[column] = pd.to_numeric(df[column], errors="coerce").round().astype("Int64")
    return st.dataframe(
        df, column_config=config, hide_index=True, height=height, key=key,
        use_container_width=True,
        on_select="rerun" if selectable else "ignore",
        selection_mode="single-row" if selectable else None,
    )


def metric_help() -> None:
    """Shared glossary — every page links to the same definitions."""
    with st.expander("What do these metrics mean?"):
        st.markdown("""
| Metric | Definition | Reading it |
|---|---|---|
| **Velocity** | `(views today − trailing 7-day mean) / trailing 7-day mean` | `+2.0` means triple the recent normal. The baseline excludes today, so a spike is not damped by itself. |
| **Acceleration** | Day-over-day change in velocity | Positive means the surge is still building; negative means it has crested. |
| **Anomaly score** | Standard deviations above the trailing 28-day mean, in log space | Pageviews are heavy-tailed, so the log matters. Above ~3 is genuinely unusual. |
| **Volatility** | Rolling standard deviation ÷ mean | Scale-free, so a 4M-view article and a 40k-view one are comparable. |
| **Peak ratio** | `peak views ÷ median views` | High means one dramatic day; near 1 means steady attention. |
| **Persistence** | Share of days the article held ≥25% of its own peak | Near 1 = sustained interest. Near 0 = a flash. |
| **Correlation** | Pearson *r* between two articles' daily log-pageviews, after removing the shared site-wide trend | Answers "when this moved more than Wikipedia as a whole, did that one too?" |
""")


def caveat(text: str) -> None:
    st.caption(f":grey[{text}]")
