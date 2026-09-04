#!/usr/bin/env python3
"""
Backfill TV episodes from a DoodStream account into Supabase.

Scans every video in the DoodStream account, detects TV episodes
(S##E## / E### / Arabic الحلقة), resolves the correct TMDB ID +
season/episode, and upserts them into the Supabase 'movies' table
with media_type='tv' (the live schema).

Usage:
    python3 backfill_tv_episodes.py                          # needs DOODSTREAM_API_KEY
    python3 backfill_tv_episodes.py --api-key YOUR_KEY
    python3 backfill_tv_episodes.py --only-summary           # don't write to DB
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
    search_tmdb_api,
    save_episode_to_supabase,
)

load_dotenv()

supabase: Client | None = None
try:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if supabase_url and supabase_key:
        supabase = create_client(supabase_url, supabase_key)
        print("[backfill] Connected to Supabase")
except Exception as e:
    print(f"[backfill] Failed to connect: {e}")


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
                print(f"[doodstream] API status {data.get('status')}: {data.get('msg')}")
                break
            batch = data.get("result", {}).get("files", [])
            if not batch:
                break
            files.extend(batch)
            total_pages = data.get("result", {}).get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            print(f"[doodstream] Error page {page}: {e}")
            break
    print(f"[doodstream] Total videos: {len(files)}")
    return files


def resolve_episode_tmdb(info: dict) -> tuple[int | None, int | None, int | None]:
    """Resolve (tmdb_id, season, episode) for an episode info dict."""
    episode = info.get("episode")
    season = info.get("season")
    tmdb_id = info.get("tmdb_id_hint")

    if not tmdb_id and info.get("cleaned_title"):
        tmdb_id = search_tmdb_api(info["cleaned_title"], "", "tv", season, episode)

    if tmdb_id and season is None and episode is not None:
        found = find_season_for_episode(tmdb_id, episode)
        if found:
            season = found
            print(f"[backfill]  resolved season for global episode {episode}: S{season}")

    return tmdb_id, season, episode


def fetch_doodstream_file_info(api_key: str, filecode: str) -> dict | None:
    """Fetch info for a single DoodStream filecode."""
    try:
        resp = requests.get(
            f"https://doodapi.com/api/file/info?key={api_key}&file_code={filecode}",
            timeout=20,
        )
        data = resp.json()
        if data.get("status") == 200 and data.get("result"):
            return data["result"]
        print(f"[doodstream] info failed: {data.get('msg')}")
    except Exception as e:
        print(f"[doodstream] info error: {e}")
    return None


def check_single_filecode(api_key: str, filecode: str, write: bool) -> None:
    """Inspect one filecode: identify show/episode and optional save."""
    f = fetch_doodstream_file_info(api_key, filecode)
    if not f:
        print(f"[check] filecode {filecode}: not found in account")
        return
    title = f.get("title", "")
    print(f"[check] filecode={filecode}")
    print(f"[check] title={title!r}")
    print(f"[check] size={f.get('size')} length={f.get('length')} upload={f.get('upload_date')}")

    info = extract_episode_info(title)
    if info.get("media_type") != "tv" or info.get("episode") is None:
        print(f"[check] -> NOT a TV episode (media_type={info.get('media_type')}); nothing to save.")
        return

    tmdb_id, season, episode = resolve_episode_tmdb(info)
    print(f"[check] -> resolved tmdb={tmdb_id} S{season}E{episode}")

    if not (tmdb_id and season is not None and episode is not None):
        print("[check] -> could not resolve a valid (tmdb, S, E); skipped saving")
        return

    if not write:
        print("[check] (dry run) would save to Supabase as tv episode")
        return

    ok = save_episode_to_supabase(
        tmdb_id=tmdb_id,
        series_title=info.get("cleaned_title") or title,
        season=season,
        episode=episode,
        doodstream_url=f"https://doodstream.com/e/{filecode}",
        doodstream_download_url=f"https://playmogo.com/d/{filecode}",
        title=title,
    )
    print(f"[check] saved: {ok}")


def main():
    parser = argparse.ArgumentParser(description="Backfill TV episodes from DoodStream to Supabase")
    parser.add_argument("--api-key", default=os.environ.get("DOODSTREAM_API_KEY", ""),
                        help="DoodStream API key")
    parser.add_argument("--filecode", default="",
                        help="Check a single filecode (identify + optionally save) instead of scanning all")
    parser.add_argument("--only-summary", action="store_true",
                        help="Only detect episodes, don't write to the database")
    args = parser.parse_args()

    if not args.api_key:
        print("Error: DOODSTREAM_API_KEY not found (set it in .env or pass --api-key)")
        sys.exit(1)

    if args.filecode:
        if not args.only_summary and not supabase:
            print("Error: Supabase not connected (check SUPABASE_URL / SUPABASE_KEY)")
            sys.exit(1)
        check_single_filecode(args.api_key, args.filecode, write=not args.only_summary)
        return

    if not args.only_summary and not supabase:
        print("Error: Supabase not connected (check SUPABASE_URL / SUPABASE_KEY)")
        sys.exit(1)

    files = fetch_all_doodstream_uploads(args.api_key)

    stats = {"files": 0, "tv": 0, "already_tv": 0, "saved": 0, "skipped": 0,
             "no_tmdb": [], "no_episode": []}

    for f in files:
        title = f.get("title", "")
        filecode = f.get("file_code") or f.get("filecode")
        if not filecode:
            continue
        stats["files"] += 1

        info = extract_episode_info(title)
        if info.get("media_type") != "tv" or info.get("episode") is None:
            continue
        stats["tv"] += 1

        tmdb_id, season, episode = resolve_episode_tmdb(info)

        print(f"[backfill] {title[:70]!r} -> tmdb={tmdb_id} S{season}E{episode} ({filecode})")

        if not tmdb_id:
            stats["no_tmdb"].append(title)
            continue
        if season is None or episode is None:
            stats["no_episode"].append(title)
            continue

        if args.only_summary:
            stats["saved"] += 1
            continue

        ok = save_episode_to_supabase(
            tmdb_id=tmdb_id,
            series_title=info.get("cleaned_title") or title,
            season=season,
            episode=episode,
            doodstream_url=f"https://doodstream.com/e/{filecode}",
            doodstream_download_url=f"https://playmogo.com/d/{filecode}",
            title=title,
        )
        if ok:
            stats["saved"] += 1
        else:
            stats["skipped"] += 1

    print("\n" + "=" * 60)
    print("BACKFILL SUMMARY")
    print(f"Files scanned        : {stats['files']}")
    print(f"TV episodes detected : {stats['tv']}")
    print(f"Saved / would-save   : {stats['saved']}")
    print(f"DB errors skipped    : {stats['skipped']}")
    print(f"No TMDB match        : {len(stats['no_tmdb'])}")
    print(f"Missing S/E          : {len(stats['no_episode'])}")
    if stats["no_tmdb"]:
        print("\nNo TMDB match:")
        for t in stats["no_tmdb"][:20]:
            print(f"  - {t}")
    if stats["no_episode"]:
        print("\nMissing season/episode:")
        for t in stats["no_episode"][:20]:
            print(f"  - {t}")
    print("=" * 60)


if __name__ == "__main__":
    main()