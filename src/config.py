"""Central configuration for the Wikipedia Attention Atlas."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = Path(os.environ.get("ATLAS_DB", DATA_DIR / "wikipedia.db"))

# --- Wikimedia API -----------------------------------------------------------
PROJECT = os.environ.get("ATLAS_WIKI_PROJECT", "en.wikipedia")
ACCESS = "all-access"
AGENT = "user"  # excludes bots/spiders on the per-article endpoint

# Wikimedia asks that clients identify themselves with a contact address.
# https://api.wikimedia.org/wiki/Documentation/Getting_started/Rate_limits
USER_AGENT = os.environ.get(
    "ATLAS_USER_AGENT",
    "WikipediaAttentionAtlas/0.1 (portfolio project; https://github.com/) python-requests",
)
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.12  # polite pause between calls, seconds
MAX_RETRIES = 4

# --- Collection --------------------------------------------------------------
TOP_N = int(os.environ.get("ATLAS_TOP_N", 1000))  # API returns 1000; keep 500-1000
DEFAULT_BACKFILL_DAYS = 90

# Pageview data for a given day lands a few hours after UTC midnight. Asking for
# anything more recent than this many days ago is likely to 404.
COLLECTION_LAG_DAYS = 1

# --- Analysis defaults -------------------------------------------------------
ROLLING_SHORT = 7
ROLLING_LONG = 28

# Floor on the rolling standard deviation of log-pageviews used by the anomaly
# score. Without it a perfectly flat article divides by ~zero: the steadier the
# article, the more its first real spike blows up (or vanishes as NaN). 0.05 in
# log space says "no article is believed steadier than about 5% day to day".
MIN_LOG_SD = 0.05
MIN_OBSERVATIONS = 10      # days an article needs before it enters correlation work
DEFAULT_CORR_WINDOW = 60   # days used to build the relationship graph
DEFAULT_EDGE_THRESHOLD = 0.60
DEFAULT_TOP_K_EDGES = 6    # keep K strongest neighbours per article

# Titles outside the main namespace are traffic, but they are not "topics".
# Real article titles can contain colons (e.g. "Star Trek: Voyager"), so match
# against known namespace prefixes rather than any colon.
NON_ARTICLE_PREFIXES = (
    "Special:", "Wikipedia:", "Portal:", "Talk:", "User:", "User_talk:",
    "File:", "File_talk:", "Help:", "Help_talk:", "Category:", "Category_talk:",
    "Template:", "Template_talk:", "Draft:", "MediaWiki:", "Module:",
    "Wikipedia_talk:", "Book:", "TimedText:",
)
NON_ARTICLE_EXACT = {"Main_Page", "-", "Undefined"}


def is_mainspace(title: str) -> bool:
    """True for ordinary encyclopedia articles (the interesting kind)."""
    if title in NON_ARTICLE_EXACT:
        return False
    return not title.startswith(NON_ARTICLE_PREFIXES)


def pretty_title(title: str) -> str:
    """Wikipedia stores titles with underscores; humans read spaces."""
    return title.replace("_", " ")


def article_url(title: str) -> str:
    lang = PROJECT.split(".")[0]
    return f"https://{lang}.wikipedia.org/wiki/{title}"
