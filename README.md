# Wikipedia Attention Atlas

An interactive dashboard for exploring how public attention moves across Wikipedia
over time — what is surging, what is fading, which topics rise together, and what
communities of shared attention look like when you draw them as a map.

Everything runs locally. The dataset is a SQLite file that gets more useful every
day the collector runs.

```bash
pip install -r requirements.txt
python scripts/collect_daily.py --backfill 90    # ~2 minutes, builds real history
streamlit run app.py
```

The Wikimedia API serves *historical* days, so you do not have to wait weeks for a
useful dataset — the backfill gives you a working atlas on the first run.

---

## What it answers

| Question | Where |
|---|---|
| What is Wikipedia reading right now, and what changed today? | **Overview** |
| How has attention to *this* article moved, and when did it peak? | **Article Explorer** |
| What is unusually hot relative to its own baseline? | **Trending** |
| Is this a flash spike or a slow burn? | **Lifecycles** |
| Which articles rise and fall together? | **Relationships** |
| What do the communities of shared attention look like? | **Attention Map** |
| What did attention look like in July versus August? | **Historical Explorer** |

---

## The data pipeline

`scripts/collect_daily.py` pulls the day's most-viewed articles from the
[Wikimedia Pageviews API](https://wikimedia.org/api/rest_v1/#/Pageviews%20data)
and appends them to SQLite. It never overwrites history: re-running a day updates
that day only, and every attempt — success, missing data, or upstream error — is
recorded in `collection_runs`.

```
articles          article_id, title, first_seen, last_seen, is_mainspace
pageviews         article_id, date, views, daily_rank        ← raw, append-only
metrics           article_id, date, ma7, ma28, velocity, …   ← derived
article_summary   article_id, peak_ratio, persistence, …     ← derived
edges             source_id, target_id, weight               ← derived
communities       article_id, community                      ← derived
collection_runs   run_at, target_date, status, rows_written
```

`pageviews` holds only what the API actually reported. Gap-filling, smoothing and
every other modelling choice lives in the analysis layer, so the raw record stays
trustworthy and re-analysable when you change your mind about the modelling.

Keep it current with a daily cron entry:

```bash
0 4 * * * cd /path/to/wikipedia-attention-atlas && python scripts/collect_daily.py --quiet
```

`scripts/rebuild_metrics.py` materialises the derived tables for SQL access. The
dashboard computes the same values on demand through the same functions in
`src/metrics.py`, so the two can never drift apart.

---

## Three decisions that shape the analysis

Most of the interesting engineering in this project is in *not* getting these
wrong. Each one is covered by a test.

**1. A missing day is not zero views.**
The `top` endpoint only reports an article on days it reached the top ~1000. A
day with no row means "fewer views than that day's cutoff", not "no views".
Filling gaps with zero manufactures enormous fake spikes, so gaps are filled with
the day's cutoff — a principled upper bound — and flagged as unobserved. Only
genuinely observed days count toward an article's totals.

Article Explorer can also fetch an article's *exact* daily history from the
per-article endpoint, which has no top-1000 censoring, and close the gaps with
real numbers.

**2. A spike must not damp its own baseline.**
Velocity compares today against the seven days *ending yesterday*. The obvious
formulation — today versus a window that includes today — dilutes exactly the
spikes the dashboard exists to find.

The anomaly score has a related trap: it divides by a rolling standard deviation,
and a perfectly steady article has a spread of zero. Left alone, the steadier an
article is, the more its first real spike returns `NaN` — the one day it matters
most. The spread is floored (see `MIN_LOG_SD`) so those articles still score.

**3. "Related" has to mean more than "both are on Wikipedia".**
Two things would otherwise fake a relationship between every pair of articles:

- *A shared rhythm.* Wikipedia is busier midweek and quieter at weekends, and
  traffic drifts seasonally. Correlating raw series mostly rediscovers that. Each
  day's cross-sectional **median** log-pageviews is subtracted first, leaving each
  article's own movement. The median rather than the mean: a handful of articles
  going viral drags the mean up on precisely the days that matter, and subtracting
  that contaminated factor stamps an inverted copy of the event onto every
  unrelated article, inventing strong negative correlations.
- *Shared imputation.* If unobserved days are filled in, every thinly-tracked
  article traces the same cutoff line and pairs of them correlate at 0.999 for no
  real reason. Correlation is therefore computed over pairwise-complete days —
  the days both articles were genuinely observed — and pairs without enough
  overlap return no score rather than a confident-looking number from a handful
  of points.

With both in place the median correlation across ~72,000 pairs sits at about
−0.03, so a strong edge means something.

---

## Metrics

| Metric | Definition | Reading it |
|---|---|---|
| **Velocity** | `(views − trailing 7-day mean) / trailing 7-day mean` | `+2.0` is triple the recent normal |
| **Acceleration** | Day-over-day change in velocity | Positive: the surge is still building |
| **Anomaly score** | σ above the trailing 28-day mean, in log space | Above ~3 is genuinely unusual |
| **Volatility** | Rolling standard deviation ÷ mean | Scale-free, so 4M-view and 40k-view articles compare |
| **Peak ratio** | `peak ÷ median` | High: one dramatic day. Near 1: steady |
| **Persistence** | Share of days holding ≥25% of its own peak | Near 1: sustained. Near 0: a flash |
| **Co-attention** | Pearson *r* between residualised log-pageviews | "When this moved more than Wikipedia as a whole, did that one too?" |

Log space throughout: pageviews are heavy-tailed and move multiplicatively.

---

## Layout

```
app.py                     Streamlit shell: navigation and shared sidebar filters
data/wikipedia.db          the dataset (git-ignored; yours grows locally)
src/
  config.py                paths, API settings, tunable constants
  database.py              schema, connections, upserts
  wiki_api.py              Wikimedia client with retry and backoff
  collector.py             daily collection, backfill, per-article gap filling
  metrics.py               densification, rolling metrics, lifecycle shapes
  relationships.py         correlation, market-factor removal, edge building
  communities.py           graph construction, Louvain, layout
  queries.py               cached data access shared by every page
  charts.py                Plotly styling and the validated palette
  ui.py                    shared Streamlit components
views/
  overview.py  article_explorer.py  trending.py  lifecycles.py
  relationships.py  attention_map.py  historical.py
scripts/
  collect_daily.py         the cron job (and the backfill)
  rebuild_metrics.py       materialise derived tables
tests/
```

> **Why `views/` and not `pages/`?** Streamlit reserves a `pages/` directory beside
> the main script for its legacy multipage convention: if one exists, Streamlit
> builds its own navigation from the filenames *before* `app.py` runs, and
> `st.navigation` never takes effect. That would cost the ordered navigation and
> the sidebar filters shared across pages. The module names are unchanged.

---

## Tests

```bash
python -m pytest
```

87 tests, no network and no dependency on your collected data — a synthetic
dataset with deliberately shaped articles (a correlated trio, flash spikes,
evergreen pages, one article present for only half the window) stands in for the
API. Every dashboard page is also rendered headlessly through Streamlit's
`AppTest` against an empty database, a populated one, and filters that exclude
everything, because that is where the data layer and the UI actually meet.

---

## Known characteristics of the data

- **Mechanical traffic is real traffic.** Sets of pages crawled together produce
  near-identical daily counts and correlate above 0.99. `Microdata (HTML)`, `RDFa`
  and `JSON-LD` are a standing example. These are genuine measurements, not a bug,
  but they are not shared *human* interest — the interesting band is roughly
  0.6–0.95. The `top` endpoint has no bot filter; the per-article endpoint used by
  the gap-filler does.
- **Non-article traffic dominates.** `Main Page` alone is ~6.8M views a day, and
  `Special:Search` and `Wikipedia:` pages crowd the top of every ranking. They are
  collected and stored, but filtered out by default; the sidebar toggle brings
  them back.
- **The top-1000 churns hard.** Over 120 days the median article appears on just
  two days. Correlation and community analysis need articles with real history,
  which is what *Minimum days observed* controls.
- **Removing the shared trend creates some negative correlation.** Articles that
  were both riding the site-wide rhythm end up mildly anticorrelated once it is
  gone. Only positive edges build the map.

## Deliberately out of scope

Single language, no real-time data, no news or social APIs, no LLM classification,
no user accounts, no cloud infrastructure. The lifecycle labels are threshold
rules, not a model, and communities are named after their most-viewed members —
a description of what is inside the group, not an inferred topic.
