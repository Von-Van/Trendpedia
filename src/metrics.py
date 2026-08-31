"""Derived attention metrics.

Two ideas drive the maths here:

1. **Censoring.** The `top` endpoint only reports an article on days it reached
   the top ~1000. A missing day is *not* zero views — it is "fewer than that
   day's cutoff". Filling gaps with 0 manufactures enormous fake spikes, so we
   fill with the cutoff instead and flag the row as unobserved.

2. **Trailing baselines.** Velocity compares today against the week *before*
   today. Including today in its own baseline damps exactly the spikes we are
   trying to detect.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import MIN_LOG_SD, ROLLING_LONG, ROLLING_SHORT


def censoring_threshold(pv: pd.DataFrame) -> pd.Series:
    """Per-day top-list cutoff: the smallest view count that still made the list."""
    return pv.groupby("date")["views"].min()


def densify(pv: pd.DataFrame, start: str | None = None, end: str | None = None,
            within_span: bool = True) -> pd.DataFrame:
    """Return a gap-free daily panel with an `observed` flag.

    Missing days are imputed with that day's censoring threshold, which is a
    principled upper bound on what the article actually received.

    within_span=True limits each article to its own first/last observation, so
    we never invent history for an article before it entered the dataset.
    """
    if pv.empty:
        return pv.assign(observed=pd.Series(dtype=bool))

    pv = pv.copy()
    pv["date"] = pd.to_datetime(pv["date"])
    lo = pd.to_datetime(start) if start else pv["date"].min()
    hi = pd.to_datetime(end) if end else pv["date"].max()
    pv = pv[(pv["date"] >= lo) & (pv["date"] <= hi)]
    if pv.empty:
        return pv.assign(observed=pd.Series(dtype=bool))

    calendar = pd.date_range(lo, hi, freq="D")
    cutoff = pv.groupby("date")["views"].min().reindex(calendar)
    # Days with no data at all (collection gaps) fall back to the global median cutoff.
    cutoff = cutoff.ffill().bfill()

    wide = pv.pivot_table(index="date", columns="article_id", values="views", aggfunc="max")
    wide = wide.reindex(calendar)
    observed = wide.notna()

    if within_span:
        # Mask out days outside each article's observed span.
        first = observed.idxmax()
        rev = observed[::-1]
        last = rev.idxmax()
        span = pd.DataFrame(
            {col: (calendar >= first[col]) & (calendar <= last[col]) for col in wide.columns},
            index=calendar,
        )
        span = span & observed.any()  # articles with no observations stay empty
    else:
        span = pd.DataFrame(True, index=calendar, columns=wide.columns)

    filled = wide.apply(lambda col: col.fillna(cutoff))
    filled = filled.where(span)

    ranks = pv.pivot_table(index="date", columns="article_id", values="daily_rank",
                           aggfunc="min").reindex(calendar).reindex(columns=wide.columns)

    out = (filled.stack().rename("views").to_frame()
           .join(observed.stack().rename("observed"))
           .join(ranks.stack().rename("daily_rank"), how="left")
           .reset_index()
           .rename(columns={"level_0": "date", "level_1": "article_id"}))
    out["observed"] = out["observed"].fillna(False).astype(bool)
    out["views"] = out["views"].astype(float)
    return out.sort_values(["article_id", "date"], ignore_index=True)


def _grouped_roll(df: pd.DataFrame, column: str, window: int, min_periods: int,
                  how: str) -> pd.Series:
    """Rolling aggregate within each article, using pandas' Cython path.

    `groupby(...).transform(lambda s: s.rolling(...))` is ~10x slower across
    the ~18k article groups this dataset produces.
    """
    rolled = getattr(df.groupby("article_id", sort=False)[column]
                     .rolling(window, min_periods=min_periods), how)()
    return rolled.reset_index(level=0, drop=True)


def compute_daily_metrics(panel: pd.DataFrame, short: int = ROLLING_SHORT,
                          long: int = ROLLING_LONG) -> pd.DataFrame:
    """Per-article/per-day rolling metrics on a dense panel from `densify`."""
    cols = ["article_id", "date", "ma7", "ma28", "velocity", "acceleration",
            "volatility", "anomaly_z", "baseline_ratio"]
    if panel.empty:
        return pd.DataFrame(columns=cols)

    df = panel.sort_values(["article_id", "date"]).reset_index(drop=True)
    grp = df.groupby("article_id", sort=False)["views"]

    df["ma7"] = _grouped_roll(df, "views", short, 2, "mean")
    df["ma28"] = _grouped_roll(df, "views", long, 4, "mean")

    # Trailing baseline: the `short`-day mean ending yesterday. Including today
    # in its own baseline damps exactly the spikes we want to detect.
    df["_prev"] = grp.shift(1)
    df["baseline"] = _grouped_roll(df, "_prev", short, 2, "mean")
    df["baseline_ratio"] = df["views"] / df["baseline"].replace(0, np.nan)
    df["velocity"] = df["baseline_ratio"] - 1.0
    df["acceleration"] = df.groupby("article_id", sort=False)["velocity"].diff()

    # Volatility: coefficient of variation over the long window (scale-free).
    roll_mean = _grouped_roll(df, "views", long, 5, "mean")
    roll_std = _grouped_roll(df, "views", long, 5, "std")
    df["volatility"] = roll_std / roll_mean.replace(0, np.nan)

    # Anomaly score in log space — pageviews are heavy-tailed and multiplicative.
    df["_logv"] = np.log1p(df["views"])
    df["_logv_prev"] = df.groupby("article_id", sort=False)["_logv"].shift(1)
    mu = _grouped_roll(df, "_logv_prev", long, 5, "mean")
    sd = _grouped_roll(df, "_logv_prev", long, 5, "std")
    # Floor the spread. A flat series has sd == 0, which would send the score to
    # NaN on the very day the article explodes — the one day it matters most.
    df["anomaly_z"] = (df["_logv"] - mu) / sd.clip(lower=MIN_LOG_SD)

    df = df.drop(columns=["_prev", "_logv", "_logv_prev"])
    return df.replace([np.inf, -np.inf], np.nan)


def summarise_articles(panel: pd.DataFrame, metrics: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per article: lifetime shape statistics.

    Only *observed* days count toward totals — imputed placeholders would
    otherwise inflate an article's apparent footprint. Written without
    `groupby.apply` so it stays fast over tens of thousands of articles.
    """
    if panel.empty:
        return pd.DataFrame()

    obs = panel[panel["observed"]].sort_values(["article_id", "date"])
    if obs.empty:
        return pd.DataFrame()

    grouped = obs.groupby("article_id", sort=True)
    summary = pd.DataFrame({
        "observations": grouped["views"].size(),
        "total_views": grouped["views"].sum().round().astype("int64"),
        "mean_views": grouped["views"].mean(),
        "median_views": grouped["views"].median(),
        "peak_views": grouped["views"].max().round().astype("int64"),
        "best_rank": grouped["daily_rank"].min(),
        "_std": grouped["views"].std(),
        "_first_day": grouped["date"].min(),
        "_last_day": grouped["date"].max(),
    })
    summary["volatility"] = summary["_std"] / summary["mean_views"].replace(0, np.nan)
    summary["peak_ratio"] = summary["peak_views"] / summary["median_views"].replace(0, np.nan)

    # Peak date: index of each article's max-views row.
    peak_rows = obs.loc[grouped["views"].idxmax(), ["article_id", "date"]]
    summary["peak_date"] = peak_rows.set_index("article_id")["date"]

    # Latest observed day (obs is sorted, so the last duplicate is the newest).
    latest = obs.drop_duplicates("article_id", keep="last").set_index("article_id")
    summary["latest_views"] = latest["views"].round().astype("int64")
    summary["latest_rank"] = latest["daily_rank"]

    # Persistence: share of days in the article's span where it held at least a
    # quarter of its own peak. High = sustained interest, low = one-day flash.
    peak_map = summary["peak_views"]
    strong = obs["views"].values >= 0.25 * peak_map.reindex(obs["article_id"]).values
    strong_days = pd.Series(strong, index=obs["article_id"].values).groupby(level=0).sum()
    span_days = (summary["_last_day"] - summary["_first_day"]).dt.days + 1
    summary["persistence"] = strong_days / span_days.replace(0, np.nan)

    summary["first_day"] = summary["_first_day"].dt.strftime("%Y-%m-%d")
    summary["last_day"] = summary["_last_day"].dt.strftime("%Y-%m-%d")
    summary["peak_date"] = pd.to_datetime(summary["peak_date"]).dt.strftime("%Y-%m-%d")
    summary = summary.drop(columns=["_std", "_first_day", "_last_day"])
    return summary.reset_index().replace([np.inf, -np.inf], np.nan)


def normalise_around_peak(panel: pd.DataFrame, window: int = 21) -> pd.DataFrame:
    """Re-index each article to days-from-peak with views scaled to its own peak.

    This is what makes attention *shapes* comparable: a 4M-view story and a
    40k-view story both run from 0 to 1 on the y axis.
    """
    if panel.empty:
        return panel

    obs = panel[panel["observed"]].copy()
    peak_idx = obs.groupby("article_id")["views"].idxmax()
    peaks = obs.loc[peak_idx, ["article_id", "date", "views"]].rename(
        columns={"date": "peak_date", "views": "peak_views"})

    merged = panel.merge(peaks, on="article_id", how="inner")
    merged["days_from_peak"] = (merged["date"] - merged["peak_date"]).dt.days
    merged["relative_views"] = merged["views"] / merged["peak_views"].replace(0, np.nan)
    return merged[merged["days_from_peak"].abs() <= window]


def classify_lifecycle(summary: pd.DataFrame, shapes: pd.DataFrame) -> pd.Series:
    """Heuristic attention-shape labels.

    Deliberately simple and inspectable rather than learned: the rules are
    thresholds on persistence, peak ratio, and how much attention survives a
    week after the peak.
    """
    if summary.empty:
        return pd.Series(dtype=object)

    after = (shapes[shapes["days_from_peak"].between(4, 10)]
             .groupby("article_id")["relative_views"].mean().rename("tail"))
    before = (shapes[shapes["days_from_peak"].between(-10, -4)]
              .groupby("article_id")["relative_views"].mean().rename("lead"))
    df = summary.set_index("article_id").join([after, before])

    labels = pd.Series("steady interest", index=df.index, dtype=object)
    flash = (df["peak_ratio"] >= 3) & (df["tail"] < 0.35)
    burn = (df["peak_ratio"] >= 2) & (df["tail"] >= 0.35) & (df["persistence"] < 0.7)
    delayed = (df["lead"] < 0.3) & (df["tail"] >= 0.5)
    longlived = (df["persistence"] >= 0.7) & (df["peak_ratio"] < 3)
    recurring = (df["volatility"] >= 0.5) & (df["peak_ratio"] < 3) & (df["persistence"] >= 0.4)

    labels[recurring] = "recurring interest"
    labels[longlived] = "long-lived trend"
    labels[burn] = "gradual burn"
    labels[delayed] = "delayed growth"
    labels[flash] = "flash spike"
    return labels.rename("lifecycle")
