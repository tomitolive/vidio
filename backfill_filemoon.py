#!/usr/bin/env python3
"""Backfill: Re-scrape Cimafu pages for DB entries using original URLs and upload to FileMoon.

Flow:
  1. Load all movies/tv_episodes from Supabase that have doodstream_url but NO filemoon_url
  2. Load processed_cima4u_movies.json to get original Cimafu watch URLs.
  3. Call cima4u_crawler.mirror_to_filemoon(watch_url, title)
  4. Update Supabase row with filemoon_url / filemoon_download_url
  5. Update local mirror DB
"""

import os
import sys
import time
import json
import re
import urllib.parse
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv("/home/tomito/Desktop/vidio/.env")

# Supabase
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# Import our modules
sys.path.insert(0, "/home/tomito/Desktop/vidio")
import filemoon
from cima4u_crawler import mirror_to_filemoon

MIRROR_DB = "filemoon_mirror.json"


def load_mirror_db():
    if os.path.exists(MIRROR_DB):
        try:
            with open(MIRROR_DB, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"mapping": {}, "last_updated": None}


def save_mirror_db(db):
    db["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(MIRROR_DB, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def get_pending_entries():
    """Get all entries that have doodstream_url but missing filemoon_url."""
    movies = supabase.table("movies").select("*").is_("filemoon_url", "null").neq("doodstream_url", "").execute().data
    episodes = supabase.table("tv_episodes").select("*").is_("filemoon_url", "null").neq("doodstream_url", "").execute().data
    print(f"Pending movies: {len(movies)} | episodes: {len(episodes)}")
    return movies, episodes


def load_original_urls():
    """Load the mapping of filecode -> original URL from processed_cima4u_movies.json"""
    mapping = {}
    try:
        with open("processed_cima4u_movies.json", encoding="utf-8") as f:
            data = json.load(f)
            for url, info in data.get("processed_urls", {}).items():
                if info.get("filecode"):
                    mapping[str(info["filecode"])] = url
    except Exception as e:
        print(f"Error loading processed JSON: {e}")
    return mapping


def update_entry(entry, filemoon_urls):
    """Update Supabase row with FileMoon URLs."""
    table = "movies" if entry.get("media_type", "movie") == "movie" else "tv_episodes"
    try:
        supabase.table(table).update(filemoon_urls).eq("id", entry["id"]).execute()
        print(f"  Updated Supabase {table} id={entry['id']}")
        return True
    except Exception as e:
        print(f"  Supabase update failed: {e}")
        return False


def process_entry(entry, mirror_db, original_urls):
    """Process a single DB entry end-to-end."""
    title = entry.get("title", "video")
    dood_url = entry.get("doodstream_url", "")
    filecode = ""
    m = re.search(r"/([A-Za-z0-9]{8,})(?:[/?#]|$)", dood_url)
    if m:
        filecode = m.group(1)

    print(f"\n{'='*60}")
    print(f"Processing: {title[:70]}")
    print(f"  Dood: {dood_url[:80]}")

    # Skip if already has filemoon
    if entry.get("filemoon_url"):
        print(f"  Already has filemoon_url, skipping")
        return "skipped"

    # Find the original Cima4u URL
    watch_url = original_urls.get(filecode)
    if not watch_url:
        print(f"  Could not find original Cima4u watch URL for filecode {filecode}")
        return "no_source"

    print(f"  Original URL: {watch_url[:80]}...")

    # Mirror to Filemoon (using Playwright + Streamtape)
    print(f"  Uploading to FileMoon via Streamtape source...")
    urls = mirror_to_filemoon(watch_url, title, filecode)
    if not urls:
        print(f"  Mirror to FileMoon failed")
        return "upload_failed"
        
    print(f"  FileMoon: {urls['filemoon_url']}")

    # Update Supabase
    ok = update_entry(entry, urls)
    if not ok:
        return "db_update_failed"

    # Save to mirror DB
    fid = urls["filemoon_url"].split("/")[-1]
    mirror_db["mapping"][filecode] = {
        "doodstream_filecode": filecode,
        "title": title,
        "filemoon_file_id": fid,
        "filemoon_url": urls["filemoon_url"],
        "filemoon_download_url": urls["filemoon_download_url"],
        "status": "mirrored",
        "source_url": watch_url,
    }
    save_mirror_db(mirror_db)

    return "success"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Max entries to process (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="Search only, don't upload")
    args = ap.parse_args()

    # Disable playwright verbose output
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    
    movies, episodes = get_pending_entries()
    all_entries = movies + episodes

    if args.limit:
        all_entries = all_entries[:args.limit]

    mirror_db = load_mirror_db()
    original_urls = load_original_urls()
    print(f"Loaded {len(original_urls)} original URLs from processed JSON.")
    
    stats = {"success": 0, "skipped": 0, "no_source": 0, "upload_failed": 0, "db_update_failed": 0, "error": 0}

    for i, entry in enumerate(all_entries, 1):
        print(f"\n[{i}/{len(all_entries)}]")
        try:
            if args.dry_run:
                # Just test search
                title = entry.get("title", "video")
                m = re.search(r"/([A-Za-z0-9]{8,})(?:[/?#]|$)", entry.get("doodstream_url", ""))
                fc = m.group(1) if m else "unknown"
                url = original_urls.get(fc)
                if url:
                    print(f"  Would mirror: {url[:90]}")
                    stats["success"] += 1
                else:
                    print(f"  No original URL for {fc}")
                    stats["no_source"] += 1
            else:
                res = process_entry(entry, mirror_db, original_urls)
                stats[res] = stats.get(res, 0) + 1
        except Exception as e:
            print(f"  ERROR: {e}")
            stats["error"] += 1
        time.sleep(3)  # be nice to servers

    print(f"\n{'='*60}")
    print("SUMMARY:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()