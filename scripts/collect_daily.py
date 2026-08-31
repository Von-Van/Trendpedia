#!/usr/bin/env python3
"""Collect Wikipedia top-article pageviews into the local dataset.

Typical use:
    python scripts/collect_daily.py                 # yesterday only (cron job)
    python scripts/collect_daily.py --backfill 90   # bootstrap 90 days of history
    python scripts/collect_daily.py --start 2026-06-01 --end 2026-06-30
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import database as db                                    # noqa: E402
from src.collector import (backfill_range, collect_range,          # noqa: E402
                           latest_available_date)
from src.config import DB_PATH, TOP_N                              # noqa: E402


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backfill", type=int, metavar="DAYS",
                        help="collect the last DAYS days of history")
    parser.add_argument("--start", type=_parse_date, help="first date (YYYY-MM-DD)")
    parser.add_argument("--end", type=_parse_date, help="last date (YYYY-MM-DD)")
    parser.add_argument("--top-n", type=int, default=TOP_N,
                        help=f"articles to keep per day (default {TOP_N})")
    parser.add_argument("--db", default=str(DB_PATH), help="database path")
    parser.add_argument("--force", action="store_true",
                        help="re-fetch days already stored")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
    )

    db.init_db(args.db)
    newest = latest_available_date()

    if args.backfill:
        end = args.end or newest
        start = end - timedelta(days=args.backfill - 1)
    elif args.start:
        start = args.start
        end = args.end or newest
    else:
        start = end = args.end or newest

    if start > end:
        parser.error("--start must not be after --end")
    if end > newest:
        print(f"note: trimming end date to {newest} (newer data is not published yet)")
        end = newest

    total_days = (end - start).days + 1
    print(f"Collecting {total_days} day(s): {start} -> {end}  [top {args.top_n}]")

    def progress(done: int, total: int, day: str) -> None:
        if not args.quiet:
            print(f"  [{done}/{total}] {day}", flush=True)

    result = collect_range(start, end, db_path=args.db, skip_existing=not args.force,
                           limit=args.top_n, progress=progress)
    print(f"\n{result}")
    for err in result.errors[:10]:
        print(f"  ! {err}")

    lo, hi = db.date_bounds(args.db)
    counts = db.read_sql(
        "SELECT COUNT(*) AS rows, COUNT(DISTINCT article_id) AS articles FROM pageviews",
        db_path=args.db)
    print(f"Dataset now spans {lo} -> {hi}: "
          f"{int(counts.loc[0,'rows']):,} observations across "
          f"{int(counts.loc[0,'articles']):,} articles")
    if result.collected:
        print("Next: python scripts/rebuild_metrics.py")
    return 1 if result.failed and not result.collected else 0


if __name__ == "__main__":
    raise SystemExit(main())
