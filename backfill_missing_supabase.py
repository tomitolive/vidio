#!/usr/bin/env python3
"""
Backfill missing items from comparison_report.json into Supabase.
Reads the report, deduplicates by title, resolves TMDB IDs, and saves to Supabase.
"""

import re
import json
import zlib
import time
from catalog import (
    supabase,
    search_tmdb_api,
    _known_override,
    save_to_supabase,
)

REPORT_FILE = "comparison_report.json"


def clean_title(title: str) -> str:
    """Remove quality tags, source tags, and extra spaces."""
    # Remove EgyDead CoM, 1080p, WEB-DL, BluRay, SCREENER, WEB DL, etc.
    cleaned = re.sub(r'\s+EgyDead\s+CoM', '', title, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+(1080p|720p|480p|2160p|4K|CAM|TS|TC|DVDScr|BRRip|HDRip|WEB-DL|WEB DL|BluRay|SCREENER|AMZN|NF)\s*', ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
    return cleaned


def is_episode(title: str) -> bool:
    """Check if title indicates a TV episode."""
    t = title.lower()
    if re.search(r'\bs\d+e\d+\b', t):  # S01E01 format
        return True
    if re.search(r'\be\d+\b', t):  # E07 format
        return True
    return False


def parse_episode_info(title: str) -> dict:
    """Extract series name, season, and episode from title."""
    cleaned = clean_title(title)
    result = {"series_name": cleaned, "season": None, "episode": None}

    # SxxExx format (e.g., S03E10)
    m = re.search(r'[Ss](\d+)[Ee](\d+)', cleaned)
    if m:
        result["season"] = int(m.group(1))
        result["episode"] = int(m.group(2))
        result["series_name"] = re.sub(r'\s*[Ss]\d+[Ee]\d+.*', '', cleaned).strip()
        return result

    # Exx format (e.g., E07, E1176)
    m = re.search(r'\b[Ee](\d+)\b', cleaned)
    if m:
        result["episode"] = int(m.group(1))
        result["series_name"] = re.sub(r'\s*[Ee]\d+.*', '', cleaned).strip()
        return result

    return result


def resolve_tmdb(series_name: str, media_type: str = "tv", season=None, episode=None):
    """Resolve TMDB ID for a series or movie."""
    # Check known overrides first
    hint_tmdb, hint_season = _known_override(series_name)
    if hint_tmdb:
        return hint_tmdb

    # Search TMDB API
    tmdb_id = search_tmdb_api(series_name, "", media_type, season, episode)
    return tmdb_id


def main():
    with open(REPORT_FILE, encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("in_doodstream_not_in_db", [])

    # Deduplicate by title, keep first filecode
    seen = {}
    for item in items:
        t = item["title"]
        if t not in seen:
            seen[t] = item

    print(f"Found {len(seen)} unique titles to backfill\n")

    saved = 0
    skipped = 0
    errors = []

    for title, item in seen.items():
        filecode = item["filecode"]
        doodstream_url = item.get("download_url") or f"https://playmogo.com/d/{filecode}"
        download_url = item.get("download_url") or f"https://playmogo.com/d/{filecode}"

        # Skip generic "video" titles - no useful info
        if title.lower().strip() == "video":
            print(f"  SKIP (generic title): {title}")
            skipped += 1
            continue

        ep = is_episode(title)

        if ep:
            info = parse_episode_info(title)
            series_name = info["series_name"]
            season = info["season"]
            episode = info["episode"]

            tmdb_id = resolve_tmdb(series_name, "tv", season, episode)
            if not tmdb_id:
                # Fallback: generate synthetic ID
                tmdb_id = 9000000 + (zlib.crc32(series_name.encode()) % 1000000)
                print(f"  WARN: No TMDB ID for '{series_name}', using synthetic {tmdb_id}")

            print(f"  TV: {series_name} S{season}E{episode} | tmdb={tmdb_id} | {filecode}")

            try:
                # Check if already in DB
                existing = supabase.table("tv_episodes").select("id").eq(
                    "tmdb_id", tmdb_id
                ).eq("season_number", season or 1).eq("episode_number", episode or 1).execute()

                if existing.data:
                    # Update with filecode
                    supabase.table("tv_episodes").update({
                        "filecode": filecode,
                        "doodstream_url": doodstream_url,
                        "doodstream_download_url": download_url,
                    }).eq("tmdb_id", tmdb_id).eq("season_number", season or 1).eq("episode_number", episode or 1).execute()
                    print(f"    UPDATED in DB")
                else:
                    # Insert new
                    data_row = {
                        "tmdb_id": tmdb_id,
                        "series_title": series_name,
                        "season_number": season or 1,
                        "episode_number": episode or 1,
                        "title": title,
                        "filecode": filecode,
                        "doodstream_url": doodstream_url,
                        "doodstream_download_url": download_url,
                    }
                    supabase.table("tv_episodes").insert(data_row).execute()
                    print(f"    INSERTED into DB")
                saved += 1
            except Exception as e:
                print(f"    ERROR: {e}")
                errors.append(f"{title}: {e}")

        else:
            # Movie
            cleaned = clean_title(title)
            tmdb_id = resolve_tmdb(cleaned, "movie")
            if not tmdb_id:
                tmdb_id = 8000000 + (zlib.crc32(cleaned.encode()) % 1000000)
                print(f"  WARN: No TMDB ID for '{cleaned}', using synthetic {tmdb_id}")

            print(f"  MOVIE: {cleaned} | tmdb={tmdb_id} | {filecode}")

            try:
                existing = supabase.table("movies").select("id").eq("tmdb_id", tmdb_id).execute()

                if existing.data:
                    supabase.table("movies").update({
                        "doodstream_url": doodstream_url,
                        "doodstream_download_url": download_url,
                    }).eq("tmdb_id", tmdb_id).execute()
                    print(f"    UPDATED in DB")
                else:
                    data_row = {
                        "tmdb_id": tmdb_id,
                        "title": cleaned,
                        "doodstream_url": doodstream_url,
                        "doodstream_download_url": download_url,
                        "media_type": "movie",
                    }
                    supabase.table("movies").insert(data_row).execute()
                    print(f"    INSERTED into DB")
                saved += 1
            except Exception as e:
                print(f"    ERROR: {e}")
                errors.append(f"{title}: {e}")

        time.sleep(0.5)

    print(f"\n{'='*50}")
    print(f"Saved: {saved}")
    print(f"Skipped: {skipped}")
    print(f"Errors: {len(errors)}")
    if errors:
        print("\nErrors detail:")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
