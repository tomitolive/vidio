#!/usr/bin/env python3
"""
check_missing.py
----------------
Compare what's stored in Supabase (tv_episodes + movies tables) against
the official TMDB episode/movie data and report what is missing.

Usage:
    python3 check_missing.py                # check everything
    python3 check_missing.py --tv           # only check TV series
    python3 check_missing.py --movies       # only check movies
    python3 check_missing.py --tmdb-id 576580  # check a specific TV series
    python3 check_missing.py --save report_missing.json  # save report to file
"""

import os
import sys
import json
import argparse
import requests
from collections import defaultdict
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

TMDB_BASE = "https://api.themoviedb.org/3"

# ─── Clients ──────────────────────────────────────────────────────────────────

supabase: Client | None = None
try:
    _url = os.environ.get("SUPABASE_URL")
    _key = os.environ.get("SUPABASE_KEY")
    if _url and _key:
        supabase = create_client(_url, _key)
        print("[supabase] ✓ Connected")
    else:
        print("[supabase] ✗ Missing SUPABASE_URL / SUPABASE_KEY")
except Exception as e:
    print(f"[supabase] ✗ Failed to connect: {e}")

TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
if not TMDB_API_KEY:
    print("[tmdb] ✗ Missing TMDB_API_KEY in .env")


# ─── TMDB helpers ─────────────────────────────────────────────────────────────

def tmdb_get(path: str, params: dict | None = None) -> dict:
    """GET from TMDB API, returns parsed JSON or {}."""
    p = {"api_key": TMDB_API_KEY, **(params or {})}
    try:
        resp = requests.get(f"{TMDB_BASE}{path}", params=p, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[tmdb] Error {path}: {e}")
        return {}


def get_tv_seasons(tmdb_id: int) -> list[dict]:
    """Return list of seasons (each with season_number and episode_count)."""
    data = tmdb_get(f"/tv/{tmdb_id}")
    seasons = []
    for s in data.get("seasons", []):
        if s.get("season_number", 0) == 0:   # skip specials
            continue
        seasons.append({
            "season_number": s["season_number"],
            "episode_count": s.get("episode_count", 0),
            "name": s.get("name", ""),
        })
    return seasons


def get_season_episodes(tmdb_id: int, season_number: int) -> list[dict]:
    """Return list of episodes for a season."""
    data = tmdb_get(f"/tv/{tmdb_id}/season/{season_number}")
    return data.get("episodes", [])


def get_all_tmdb_episodes(tmdb_id: int) -> dict[tuple[int, int], str]:
    """
    Returns a dict mapping (season, episode) -> episode_name
    for all episodes of a TV series.
    """
    seasons = get_tv_seasons(tmdb_id)
    all_eps: dict[tuple[int, int], str] = {}
    for s in seasons:
        eps = get_season_episodes(tmdb_id, s["season_number"])
        for ep in eps:
            key = (s["season_number"], ep["episode_number"])
            all_eps[key] = ep.get("name", "")
    return all_eps


# ─── Supabase helpers ─────────────────────────────────────────────────────────

def fetch_db_tv_episodes() -> dict[int, set[tuple[int, int]]]:
    """
    Returns { tmdb_id: {(season, episode), ...} }
    """
    if not supabase:
        print("[supabase] Not connected, cannot fetch TV episodes")
        return {}

    print("[supabase] Fetching tv_episodes table...")
    result = defaultdict(set)
    page_size = 1000
    offset = 0

    while True:
        try:
            resp = (
                supabase.table("tv_episodes")
                .select("tmdb_id, season_number, episode_number, series_title")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            rows = resp.data
            if not rows:
                break
            for row in rows:
                tid = row["tmdb_id"]
                s = row.get("season_number") or 0
                e = row["episode_number"]
                result[tid].add((s, e))
            print(f"[supabase] Fetched {offset + len(rows)} rows...")
            if len(rows) < page_size:
                break
            offset += page_size
        except Exception as ex:
            print(f"[supabase] Error fetching tv_episodes: {ex}")
            break

    total_eps = sum(len(v) for v in result.values())
    print(f"[supabase] TV: {len(result)} series, {total_eps} episodes in DB")
    return dict(result)


def fetch_db_tv_series_info() -> dict[int, str]:
    """Returns {tmdb_id: series_title} for every series in tv_episodes."""
    if not supabase:
        return {}
    try:
        resp = (
            supabase.table("tv_episodes")
            .select("tmdb_id, series_title")
            .execute()
        )
        info: dict[int, str] = {}
        for row in resp.data:
            info[row["tmdb_id"]] = row.get("series_title") or str(row["tmdb_id"])
        return info
    except Exception as ex:
        print(f"[supabase] Error fetching series info: {ex}")
        return {}


def fetch_db_movies() -> set[int]:
    """Returns set of tmdb_ids in the movies table (media_type='movie')."""
    if not supabase:
        print("[supabase] Not connected, cannot fetch movies")
        return set()

    print("[supabase] Fetching movies table...")
    ids: set[int] = set()
    page_size = 1000
    offset = 0

    while True:
        try:
            resp = (
                supabase.table("movies")
                .select("tmdb_id")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            rows = resp.data
            if not rows:
                break
            for row in rows:
                if row.get("tmdb_id"):
                    ids.add(row["tmdb_id"])
            if len(rows) < page_size:
                break
            offset += page_size
        except Exception as ex:
            print(f"[supabase] Error fetching movies: {ex}")
            break

    print(f"[supabase] Movies: {len(ids)} in DB")
    return ids


# ─── Main check functions ──────────────────────────────────────────────────────

def check_tv_series(
    tmdb_id: int,
    series_title: str,
    db_episodes: set[tuple[int, int]],
    verbose: bool = True,
) -> dict:
    """
    Check a single TV series for missing episodes.
    Returns a report dict.
    """
    if verbose:
        print(f"\n[check] 📺 {series_title} (tmdb_id={tmdb_id})")
        print(f"        DB has {len(db_episodes)} episode(s)")

    tmdb_episodes = get_all_tmdb_episodes(tmdb_id)
    if not tmdb_episodes:
        if verbose:
            print(f"        ⚠ No TMDB data found")
        return {
            "tmdb_id": tmdb_id,
            "series_title": series_title,
            "status": "no_tmdb_data",
            "db_count": len(db_episodes),
            "tmdb_count": 0,
            "missing": [],
            "extra": [],
        }

    # Normalize DB episodes (some may have season=0 if not resolved)
    missing = []
    for (s, e), name in sorted(tmdb_episodes.items()):
        if (s, e) not in db_episodes:
            missing.append({"season": s, "episode": e, "title": name})

    # Episodes in DB not found in TMDB (maybe wrongly numbered)
    tmdb_set = set(tmdb_episodes.keys())
    extra = [
        {"season": s, "episode": e}
        for (s, e) in sorted(db_episodes)
        if (s, e) not in tmdb_set and s != 0  # skip unresolved (s=0)
    ]

    if verbose:
        print(f"        TMDB total: {len(tmdb_episodes)} episode(s)")
        if missing:
            print(f"        ❌ MISSING: {len(missing)} episode(s)")
            for m in missing[:10]:
                print(f"           S{m['season']:02d}E{m['episode']:02d} - {m['title']}")
            if len(missing) > 10:
                print(f"           ... and {len(missing) - 10} more")
        else:
            print(f"        ✅ Complete! No missing episodes.")
        if extra:
            print(f"        ⚠  In DB but not in TMDB: {len(extra)} (possibly wrong numbering)")

    return {
        "tmdb_id": tmdb_id,
        "series_title": series_title,
        "status": "missing" if missing else "complete",
        "db_count": len(db_episodes),
        "tmdb_count": len(tmdb_episodes),
        "missing_count": len(missing),
        "missing": missing,
        "extra_in_db": extra,
    }


def check_all_tv(tmdb_id_filter: int | None = None) -> list[dict]:
    """Check all TV series (or a specific one) for missing episodes."""
    db_all = fetch_db_tv_episodes()
    series_info = fetch_db_tv_series_info()

    if tmdb_id_filter:
        if tmdb_id_filter not in db_all:
            print(f"[check] tmdb_id={tmdb_id_filter} not found in DB — checking against TMDB anyway")
            db_all[tmdb_id_filter] = set()
        db_all = {tmdb_id_filter: db_all[tmdb_id_filter]}

    results = []
    total = len(db_all)
    for i, (tid, eps) in enumerate(db_all.items(), 1):
        title = series_info.get(tid, f"Series #{tid}")
        verbose_header = (total <= 10) or bool(tmdb_id_filter)
        report = check_tv_series(tid, title, eps, verbose=True)
        results.append(report)
        if not verbose_header:
            status_icon = "✅" if report["status"] == "complete" else "❌"
            print(
                f"[{i:3d}/{total}] {status_icon} {title[:50]:<50} "
                f"DB={report['db_count']:4d} TMDB={report['tmdb_count']:4d} "
                f"Missing={report['missing_count']:4d}"
            )

    return results


def print_tv_summary(results: list[dict]) -> None:
    """Print a clean summary table of TV check results."""
    total_series = len(results)
    complete = [r for r in results if r["status"] == "complete"]
    missing = [r for r in results if r["missing_count"] > 0]
    no_data = [r for r in results if r["status"] == "no_tmdb_data"]

    print("\n" + "=" * 70)
    print("📺  TV SERIES MISSING EPISODES REPORT")
    print("=" * 70)
    print(f"Total series in DB : {total_series}")
    print(f"✅ Complete         : {len(complete)}")
    print(f"❌ Has missing eps  : {len(missing)}")
    print(f"⚠  No TMDB data    : {len(no_data)}")

    if missing:
        total_missing_eps = sum(r["missing_count"] for r in missing)
        print(f"\nTotal missing episodes across all series: {total_missing_eps}")
        print("\n" + "-" * 70)
        print(f"{'Series':<45} {'DB':>5} {'TMDB':>5} {'Missing':>8}")
        print("-" * 70)
        for r in sorted(missing, key=lambda x: -x["missing_count"]):
            print(
                f"{r['series_title'][:44]:<45} {r['db_count']:>5} "
                f"{r['tmdb_count']:>5} {r['missing_count']:>8}"
            )
    print("=" * 70)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Check what TV episodes / movies are missing from Supabase vs TMDB"
    )
    parser.add_argument("--tv", action="store_true", help="Check TV episodes (default if no flag)")
    parser.add_argument("--movies", action="store_true", help="Check movies (just shows DB count, no TMDB comparison yet)")
    parser.add_argument("--tmdb-id", type=int, default=None, help="Check a single TV series by TMDB ID")
    parser.add_argument("--save", default="", metavar="FILE", help="Save JSON report to file")
    args = parser.parse_args()

    if not supabase:
        print("\n✗ Cannot proceed: Supabase not connected.")
        sys.exit(1)

    if not TMDB_API_KEY:
        print("\n✗ Cannot proceed: TMDB_API_KEY missing.")
        sys.exit(1)

    run_tv = args.tv or args.tmdb_id or (not args.tv and not args.movies)
    run_movies = args.movies

    report = {}

    # ── TV check ──
    if run_tv:
        tv_results = check_all_tv(tmdb_id_filter=args.tmdb_id)
        print_tv_summary(tv_results)
        report["tv"] = tv_results

    # ── Movies quick count ──
    if run_movies:
        movie_ids = fetch_db_movies()
        print("\n" + "=" * 70)
        print("🎬  MOVIES IN SUPABASE DB")
        print("=" * 70)
        print(f"Total movies stored in DB: {len(movie_ids)}")
        print("(Pass --tv to check TV episodes for missing content)")
        print("=" * 70)
        report["movies_count"] = len(movie_ids)

    # ── Save report ──
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n[report] Saved to: {args.save}")


if __name__ == "__main__":
    main()
