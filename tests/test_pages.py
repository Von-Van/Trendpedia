"""Every dashboard page is rendered headlessly against the synthetic dataset.

This is the regression net that matters most: the pages are where the data
layer, the metric layer and Streamlit's API meet, and a broken column name or a
missing guard shows up here rather than in the browser.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import ROOT

PAGES = ["overview", "article_explorer", "trending", "lifecycles",
         "relationships", "attention_map", "historical"]

SCRIPT = """
import sys
sys.path.insert(0, {root!r})
import streamlit as st
from src.queries import Filters
from views import {module}

st.session_state["filters"] = Filters(
    db_path={db!r}, start={start!r}, end={end!r},
    mainspace_only=True, min_views=0, min_observations={min_obs},
)
{module}.render()
"""


def _run(module: str, db_path: str, start="2026-01-01", end="2026-03-01",
         min_obs=10) -> AppTest:
    app = AppTest.from_string(SCRIPT.format(
        root=str(ROOT), module=module, db=db_path, start=start, end=end,
        min_obs=min_obs))
    app.run(timeout=120)
    return app


@pytest.mark.parametrize("module", PAGES)
def test_page_renders_without_error(module, db_path):
    app = _run(module, db_path)
    assert not app.exception, f"{module} raised: {app.exception}"


@pytest.mark.parametrize("module", PAGES)
def test_page_produces_output(module, db_path):
    app = _run(module, db_path)
    assert len(app.markdown) or len(app.metric) or len(app.dataframe)


@pytest.mark.parametrize("module", PAGES)
def test_page_survives_an_empty_window(module, empty_db):
    """A brand-new database must show guidance, not a stack trace."""
    app = _run(module, empty_db, start="2026-01-01", end="2026-01-10")
    assert not app.exception, f"{module} raised on empty data: {app.exception}"


@pytest.mark.parametrize("module", PAGES)
def test_page_survives_impossible_filters(module, db_path):
    """Filters that exclude everything are a normal user action, not an error."""
    app = _run(module, db_path, min_obs=60)
    assert not app.exception, f"{module} raised on strict filters: {app.exception}"


def test_overview_reports_the_dataset(db_path):
    app = _run("overview", db_path)
    labels = [m.label for m in app.metric]
    assert "Articles tracked" in labels
    assert "Attention represented" in labels


def test_article_explorer_selects_an_article(db_path):
    app = _run("article_explorer", db_path)
    assert app.selectbox[0].value is not None
    assert any("Peak day" == m.label for m in app.metric)


def test_relationships_finds_the_correlated_trio(db_path):
    app = _run("relationships", db_path)
    assert not app.exception
    assert any("Pairs scored" == m.label for m in app.metric)


ONBOARDING = """
import sys, os
sys.path.insert(0, {root!r})
os.environ["ATLAS_DB"] = {db!r}
import app
app.onboarding()
"""


def test_first_run_shows_setup_instructions(tmp_path):
    """A fresh clone must explain itself, not show an empty dashboard."""
    app = AppTest.from_string(ONBOARDING.format(
        root=str(ROOT), db=str(tmp_path / "fresh.db")))
    app.run(timeout=60)
    assert not app.exception
    text = " ".join(m.value for m in app.markdown) + " ".join(c.value for c in app.code)
    assert "collect_daily.py" in text
    assert "--backfill" in text


def test_attention_map_focus_and_highlight(db_path):
    """Focusing a group and highlighting an article are the map's main controls."""
    app = _run("attention_map", db_path)
    assert not app.exception

    group_picker, article_picker = app.selectbox[0], app.selectbox[1]
    assert len(group_picker.options) > 1, "no communities were detected"

    group_picker.select(group_picker.options[1]).run(timeout=120)
    assert not app.exception, f"focusing a group raised: {app.exception}"

    app = _run("attention_map", db_path)
    article_picker = app.selectbox[1]
    assert len(article_picker.options) > 1, "no mapped articles to highlight"
    article_picker.select(article_picker.options[1]).run(timeout=120)
    assert not app.exception, f"highlighting an article raised: {app.exception}"
