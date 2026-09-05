#!/usr/bin/env python3
"""
Full sync: import EVERYTHING from a DoodStream account into Supabase.

Scans every video on the account, classifies it as a movie or a TV episode
(using the same episode detection as jibi_bot/backfill), resolves the correct
TMDB ID (+ season/episode for TV), and upserts it into the Supabase 'movies'
table with media_type='movie' or media_type='tv' + season_number/episode_number.

This closes the gap where the account holds TV episodes that never reach the
database: the old `import_from_doodstream_account` treated everything as a
movie and never wrote to Supabase.

Usage:
    python3 sync_doodstream_to_db.py                          # needs DOODSTREAM_API_KEY
    python3 sync_doodstream_to_db.py --api-key YOUR_KEY
    python3 sync_doodstream_to_db.py --only-summary           # don't write to DB
    python3 sync_doodstream_to_db.py --filecode XXXX          # single file only
"""

import os
import sys
import argparse
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

from catalog import (
    extract_episode_info,
    find_season_for_episode,
    find_tmdb_id_by_title,
    search_tmdb_api,
    save_episode_to_supabase,
    save_movie_to_supabase,
)

load_dotenv()

supabase: Client | None = None
try:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if supabase_url and supabase_key:
        supabase = create_client(supabase_url, supabase_key)
        print("[sync] Connected to Supabase")
except Exception as e:
    print(f"[sync] Failed to connect: {e}")


def fetch_all_doodstream_uploads(api_key: str) -> list[dict]:
    """Fetch every file in the DoodStream account."""
    files: list[dict] = []
    page = 1
    while True:
        try:
            resp = requests.get(
                f"https://doodapi.com/api/file/list?key={api_key}&page={page}&per_page=100",
                timeout=20,
            )
            data = resp.json()
            if data.get("status") != 200:
                print(f"[sync] API status {data.get('status')}: {data.get('msg')}")
                break
            batch = data.get("result", {}).get("files", [])
            if not batch:
                break
            files.extend(batch)
            total_pages = data.get("result", {}).get("total_pages", 1)
            print(f"[sync] Fetched page {page}/{total_pages} ({len(batch)} files)")
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            print(f"[sync] Error page {page}: {e}")
            break
    print(f"[sync] Total videos: {len(files)}")
    return files


def fetch_doodstream_file_info(api_key: str, filecode: str) -> dict | None:
    """Fetch info for a single DoodStream filecode."""
    try:
        resp = requests.get(
            f"https://doodapi.com/api/file/info?key={api_key}&file_code={filecode}",
            timeout=20,
        )
        data = resp.json()
        if data.get("status") == 200 and data.get("result"):
            res = data["result"]
            return res[0] if isinstance(res, list) and len(res) > 0 else res
        print(f"[sync] info failed: {data.get('msg')}")
    except Exception as e:
        print(f"[sync] info error: {e}")
    return None


def resolve_tv_episode(info: dict) -> tuple[int | None, int | None, int | None]:
    """Resolve (tmdb_id, season, episode) for a TV episode info dict."""
    episode = info.get("episode")
    season = info.get("season")
    tmdb_id = info.get("tmdb_id_hint")

    if not tmdb_id and info.get("cleaned_title"):
        tmdb_id = search_tmdb_api(info["cleaned_title"], "", "tv", season, episode)

    if tmdb_id and season is None and episode is not None:
        found = find_season_for_episode(tmdb_id, episode)
        if found:
            season = found
            print(f"[sync] resolved season for global episode {episode}: S{season}")

    return tmdb_id, season, episode


def resolve_movie_tmdb(title: str) -> int | None:
    """Resolve tmdb_id for a movie from the local catalog or TMDB API."""
    tmdb_id = find_tmdb_id_by_title(title)
    if tmdb_id:
        return tmdb_id
    return search_tmdb_api(title, "", "movie")


def extract_year(title: str) -> int | None:
    """Extract a 4-digit release year from a title."""
    import re
    m = re.search(r"\b((?:19|20)\d{2})\b", title or "")
    return int(m.group(1)) if m else None


def sync_file(api_key: str, filecode: str, write: bool) -> dict:
    """Sync one filecode into Supabase. Returns a stat dict."""
    f = fetch_doodstream_file_info(api_key, filecode)
    if not f:
        return {"filecode": filecode, "type": "error", "reason": "not found in account"}

    title = f.get("title", "")
    info = extract_episode_info(title)
    print(f"\n[sync] {title[:80]!r} ({filecode})")

    dood_url = f"https://doodstream.com/e/{filecode}"
    download_url = f"https://playmogo.com/d/{filecode}"

    if info.get("media_type") == "tv" and info.get("episode") is not None:
        tmdb_id, season, episode = resolve_tv_episode(info)
        print(f"[sync]   -> TV tmdb={tmdb_id} S{season}E{episode}")

        if not tmdb_id:
            import zlib
            series_title = info.get("cleaned_title") or title
            tmdb_id = 9000000 + (zlib.crc32(series_title.encode('utf-8')) % 1000000)
            print(f"[sync]   -> ⚠ TMDB missed for {series_title}, generated fake ID {tmdb_id}")
            
        if season is None or episode is None:
            # Maybe default to Season 1 Episode 1 for missing data so it still inserts? 
            # Or just save it... user said "7ote gha name deyalo ou sf"
            season = season if season is not None else 1
            episode = episode if episode is not None else 1
            print(f"[sync]   -> ⚠ Missing season/episode, defaulted to S{season}E{episode}")

        if not write:
            return {"filecode": filecode, "type": "tv", "reason": "dry_run",
                    "tmdb_id": tmdb_id, "season": season, "episode": episode, "title": title}

        ok = save_episode_to_supabase(
            tmdb_id=tmdb_id,
            series_title=info.get("cleaned_title") or title,
            season=season,
            episode=episode,
            doodstream_url=dood_url,
            doodstream_download_url=download_url,
            title=title,
        )
        return {"filecode": filecode, "type": "tv", "ok": ok,
                "tmdb_id": tmdb_id, "season": season, "episode": episode, "title": title}

    # ── Movie path ─────────────────────────────────────────────
    tmdb_id = resolve_movie_tmdb(title)
    print(f"[sync]   -> movie tmdb={tmdb_id}")

    if not tmdb_id:
        import zlib
        series_title = info.get("cleaned_title") or title
        tmdb_id = 8000000 + (zlib.crc32(series_title.encode('utf-8')) % 1000000)
        print(f"[sync]   -> ⚠ TMDB missed for movie {series_title}, generated fake ID {tmdb_id}")

    if not write:
        return {"filecode": filecode, "type": "movie", "reason": "dry_run",
                "tmdb_id": tmdb_id, "title": title}

    ok = save_movie_to_supabase(
        tmdb_id=tmdb_id,
        title=info.get("cleaned_title") or title,
        doodstream_url=dood_url,
        doodstream_download_url=download_url,
        year=extract_year(title),
    )
    return {"filecode": filecode, "type": "movie", "ok": ok,
            "tmdb_id": tmdb_id, "title": title}


def main():
    parser = argparse.ArgumentParser(
        description="Sync all DoodStream videos (movies + TV) into Supabase"
    )
    parser.add_argument("--api-key", default=os.environ.get("DOODSTREAM_API_KEY", ""),
                        help="DoodStream API key")
    parser.add_argument("--filecode", default="",
                        help="Sync a single filecode instead of scanning the whole account")
    parser.add_argument("--only-summary", action="store_true",
                        help="Only detect/resolve; don't write to the database")
    args = parser.parse_args()

    if not args.api_key:
        print("Error: DOODSTREAM_API_KEY not found (set it in .env or pass --api-key)")
        sys.exit(1)

    if args.filecode:
        if not args.only_summary and not supabase:
            print("Error: Supabase not connected (check SUPABASE_URL / SUPABASE_KEY)")
            sys.exit(1)
        sync_file(args.api_key, args.filecode, write=not args.only_summary)
        return

    if not args.only_summary and not supabase:
        print("Error: Supabase not connected (check SUPABASE_URL / SUPABASE_KEY)")
        sys.exit(1)

    files = fetch_all_doodstream_uploads(args.api_key)
    if not files:
        print("[sync] No files in the account")
        sys.exit(0)

    stats = {
        "files": 0, "movies": 0, "tv": 0, "saved_movies": 0, "saved_tv": 0,
        "dry_run": 0, "failures": 0,
        "movies_no_tmdb": [], "tv_no_tmdb": [], "tv_no_episode": [], "errors": [],
    }

    for f in files:
        filecode = f.get("file_code") or f.get("filecode")
        if not filecode:
            continue
        stats["files"] += 1
        try:
            res = sync_file(args.api_key, filecode, write=not args.only_summary)
        except Exception as e:
            print(f"[sync] Error processing {filecode}: {e}")
            stats["errors"].append(f"{filecode}: {e}")
            stats["failures"] += 1
            continue

        kind = res.get("type")
        reason = res.get("reason", "")

        if kind == "tv":
            stats["tv"] += 1
            if reason == "no_tmdb":
                stats["tv_no_tmdb"].append(res.get("title"))
            elif reason == "no_episode":
                stats["tv_no_episode"].append(res.get("title"))
            elif res.get("ok"):
                stats["saved_tv"] += 1
            elif reason == "dry_run":
                stats["dry_run"] += 1
            else:
                stats["failures"] += 1
                stats["errors"].append(f"{res.get('title')}: DB error")
        elif kind == "movie":
            stats["movies"] += 1
            if reason == "no_tmdb":
                stats["movies_no_tmdb"].append(res.get("title"))
            elif res.get("ok"):
                stats["saved_movies"] += 1
            elif reason == "dry_run":
                stats["dry_run"] += 1
            else:
                stats["failures"] += 1
                stats["errors"].append(f"{res.get('title')}: DB error")

    print("\n" + "=" * 64)
    print("SYNC SUMMARY")
    print(f"Files scanned       : {stats['files']}")
    print(f"Movies detected     : {stats['movies']}  (saved: {stats['saved_movies']})")
    print(f"TV episodes detected: {stats['tv']}  (saved: {stats['saved_tv']})")
    print(f"Dry-run (would save): {stats['dry_run']}")
    print(f"DB failures         : {stats['failures']}")
    print(f"Movies no TMDB match: {len(stats['movies_no_tmdb'])}")
    print(f"TV no TMDB match    : {len(stats['tv_no_tmdb'])}")
    print(f"TV missing S/E      : {len(stats['tv_no_episode'])}")
    if stats["movies_no_tmdb"]:
        print("\nMovies without TMDB match:")
        for t in stats["movies_no_tmdb"][:20]:
            print(f"  - {t}")
    if stats["tv_no_tmdb"]:
        print("\nTV without TMDB match:")
        for t in stats["tv_no_tmdb"][:20]:
            print(f"  - {t}")
    if stats["tv_no_episode"]:
        print("\nTV missing season/episode:")
        for t in stats["tv_no_episode"][:20]:
            print(f"  - {t}")
    if stats["errors"]:
        print(f"\nErrors ({len(stats['errors'])}):")
        for e in stats["errors"][:20]:
            print(f"  - {e}")
    print("=" * 64)


if __name__ == "__main__":
    main()