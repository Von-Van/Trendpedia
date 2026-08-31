"""Thin client for the Wikimedia Pageviews REST API.

Docs: https://wikimedia.org/api/rest_v1/#/Pageviews%20data
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import requests

from .config import (ACCESS, AGENT, MAX_RETRIES, PROJECT, REQUEST_DELAY,
                     REQUEST_TIMEOUT, TOP_N, USER_AGENT)

log = logging.getLogger(__name__)
BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews"


class PageviewsError(RuntimeError):
    """API call failed in a way the caller should know about."""


class DataNotAvailable(PageviewsError):
    """The API has no data for that date yet (or ever). Not a bug."""


class WikiPageviewsClient:
    def __init__(self, project: str = PROJECT, session: requests.Session | None = None):
        self.project = project
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    # -- plumbing -------------------------------------------------------------
    def _get(self, url: str) -> dict:
        """GET with retry/backoff. 404 means 'no data', which is not retryable."""
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                time.sleep(REQUEST_DELAY)
                return resp.json()
            if resp.status_code == 404:
                raise DataNotAvailable(f"No pageview data at {url}")
            if resp.status_code == 429 or resp.status_code >= 500:
                # rate limited or transient server issue — back off and retry
                wait = 2 ** attempt
                log.warning("HTTP %s from Wikimedia, retrying in %ss", resp.status_code, wait)
                last_error = PageviewsError(f"HTTP {resp.status_code}")
                time.sleep(wait)
                continue
            raise PageviewsError(f"HTTP {resp.status_code} for {url}: {resp.text[:200]}")

        raise PageviewsError(f"Giving up on {url} after {MAX_RETRIES} attempts: {last_error}")

    # -- endpoints ------------------------------------------------------------
    def top_articles(self, day: date, limit: int = TOP_N) -> list[dict]:
        """Most-viewed articles for a single day.

        Returns [{'article': str, 'views': int, 'rank': int}, ...] — at most
        1000 entries, which is what the endpoint provides.
        """
        url = (f"{BASE}/top/{self.project}/{ACCESS}/"
               f"{day.year}/{day.month:02d}/{day.day:02d}")
        payload = self._get(url)
        try:
            articles = payload["items"][0]["articles"]
        except (KeyError, IndexError) as exc:
            raise PageviewsError(f"Unexpected payload shape from {url}") from exc

        cleaned = []
        for entry in articles[:limit]:
            try:
                cleaned.append({
                    "article": entry["article"],
                    "views": int(entry["views"]),
                    "rank": int(entry["rank"]),
                })
            except (KeyError, TypeError, ValueError):
                log.warning("Skipping malformed entry: %r", entry)
        return cleaned

    def article_series(self, title: str, start: date, end: date) -> list[dict]:
        """True daily pageviews for one article — no top-1000 censoring.

        Used to fill in the days an article was popular but not top-1000.
        """
        safe = requests.utils.quote(title.replace(" ", "_"), safe="")
        url = (f"{BASE}/per-article/{self.project}/{ACCESS}/{AGENT}/{safe}/daily/"
               f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}")
        payload = self._get(url)
        out = []
        for item in payload.get("items", []):
            try:
                stamp = datetime.strptime(item["timestamp"], "%Y%m%d%H").date()
                out.append({"date": stamp.isoformat(), "views": int(item["views"])})
            except (KeyError, ValueError):
                continue
        return out


def daterange(start: date, end: date):
    """Inclusive date iterator."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)
