"""The Data section of the sidebar: check what is missing, fetch it on demand.

Updating is deliberately a button rather than a background job. The user decides
when to reach out to Wikimedia, watches it happen, and sees the dashboard
refresh with the new days.
"""
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from . import database as db
from .collector import (DataStatus, collect_days, collect_range, data_status,
                        latest_available_date)

MESSAGE_KEY = "data_panel_message"


def older_range(first: date, days: int) -> list[date]:
    """The `days` days immediately before `first` — used to reach further back."""
    end = first - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _friendly(day: date | None) -> str:
    return day.strftime("%b %-d") if day else "—"


def _remember(kind: str, text: str) -> None:
    """Stash a result so it can be shown after the rerun that follows a fetch."""
    st.session_state[MESSAGE_KEY] = (kind, text)


def _flush_message() -> None:
    message = st.session_state.pop(MESSAGE_KEY, None)
    if not message:
        return
    kind, text = message
    if kind == "success":
        st.toast(text, icon="✅")
    elif kind == "warning":
        st.warning(text, icon="⚠️")
    else:
        st.error(text, icon="🚫")


def _fetch(days: list[date], db_path: str, label: str) -> None:
    """Run a collection with a progress bar, then refresh the whole dashboard."""
    if not days:
        _remember("success", "Already up to date.")
        st.rerun()

    bar = st.progress(0.0, text=f"{label}…")

    def tick(done: int, total: int, day: str) -> None:
        bar.progress(done / total, text=f"{day}  ({done}/{total})")

    try:
        result = collect_days(days, db_path=db_path, progress=tick)
    except Exception as exc:                      # network down, disk full, …
        bar.empty()
        _remember("error", f"Could not reach Wikimedia: {exc}")
        st.rerun()
        return
    bar.empty()

    if result.collected and not result.failed:
        _remember("success",
                  f"Added {result.collected} day(s) · {result.rows:,} new records.")
    elif result.collected:
        _remember("warning",
                  f"Added {result.collected} day(s); {result.failed} could not be "
                  f"fetched. {result.errors[0] if result.errors else ''}")
    else:
        detail = result.errors[0] if result.errors else "No new data was available."
        _remember("warning", detail)

    # New rows invalidate every derived figure on every page.
    st.cache_data.clear()
    st.rerun()


def _status_caption(status: DataStatus) -> None:
    if status.is_empty:
        st.caption("No data collected yet.")
        return

    span = f"{_friendly(status.first)} – {_friendly(status.last)} · {status.total_days} days"
    if status.is_current:
        st.caption(f":green[●] Up to date · {span}")
    else:
        behind = status.days_behind
        bits = []
        if behind:
            bits.append(f"{behind} new day{'s' if behind != 1 else ''} available")
        if status.internal_gaps:
            bits.append(f"{status.internal_gaps} missing day"
                        f"{'s' if status.internal_gaps != 1 else ''} to backfill")
        st.caption(f":orange[●] {' · '.join(bits)}")
        st.caption(span)


def render(db_path: str) -> None:
    """Draw the Data section. Call this from the sidebar."""
    _flush_message()
    status = data_status(db_path)

    st.markdown("#### Data")
    _status_caption(status)

    disabled = status.is_current
    if st.button(
            "Up to date" if disabled else "Update data",
            type="secondary" if disabled else "primary",
            use_container_width=True, disabled=disabled,
            help=("Wikimedia publishes each day a few hours after it ends, so the "
                  "most recent day available is usually yesterday.")
            if disabled else
            f"Fetch {len(status.missing)} day(s) from Wikimedia."):
        _fetch(status.missing, db_path, "Fetching new days")

    with st.expander("More data options"):
        if not status.is_empty:
            st.caption(f"**Earliest day held:** {status.first}")
            older_days = st.slider("Add older history (days)", 30, 365, 90, step=30,
                                   key="dp_backfill_days",
                                   help="Wikimedia serves historical days, so the "
                                        "past can be filled in at any time.")
            if st.button(f"Fetch {older_days} earlier days", use_container_width=True,
                         key="dp_backfill"):
                _fetch(older_range(status.first, older_days), db_path,
                       "Fetching older history")

        if status.last_updated:
            st.caption(f"**Last checked:** {status.last_updated[:16].replace('T', ' ')} UTC")

        if st.button("Refresh charts", use_container_width=True, key="dp_refresh",
                     help="Recompute every metric from the stored data. Useful if "
                          "something looks stale."):
            st.cache_data.clear()
            _remember("success", "Charts refreshed.")
            st.rerun()


def onboarding_fetch(db_path: str, days: int) -> None:
    """First-run collection, shown on the welcome screen."""
    bar = st.progress(0.0, text="Contacting Wikimedia…")

    def tick(done: int, total: int, day: str) -> None:
        bar.progress(done / total, text=f"Collected {day}  ({done}/{total} days)")

    end = latest_available_date()
    start = end - timedelta(days=days - 1)
    try:
        result = collect_range(start, end, db_path=db_path, progress=tick)
    except Exception as exc:
        bar.empty()
        st.error(f"Could not reach Wikimedia: {exc}")
        return
    bar.empty()

    if result.collected:
        _remember("success", f"Collected {result.collected} days of history.")
        st.cache_data.clear()
        st.rerun()
    else:
        st.error(f"Collection failed. {result}")
        for err in result.errors[:5]:
            st.caption(err)
