#!/usr/bin/env python3
"""Backfill: Re-scrape Cimafu pages for DB entries, extract servers, upload to FileMoon.

Flow:
  1. Load all movies/tv_episodes from Supabase that have doodstream_url but NO filemoon_url
  2. For each entry: search Cimafu by cleaned title + season/episode
  3. Scrape the watch page -> get servers (itemprop="contentUrl")
  4. Resolve a playable direct URL (Streamtape browser resolver)
  5. Upload to FileMoon (remote upload from direct URL)
  6. Update Supabase row with filemoon_url / filemoon_download_url
  7. Update local catalog + filemoon_mirror.json mapping
"""

import os
import sys
import time
import json
import re
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv("/home/tomito/Desktop/vidio/.env")

# Supabase
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# Import our modules
sys.path.insert(0, "/home/tomito/Desktop/vidio")
from cimafu_source import find_cimafu_page, resolve_embed
import filemoon

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

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


def find_source_for_entry(entry):
    """Resolve direct playable URL from the existing DoodStream URL using yt-dlp."""
    dood_url = entry.get("doodstream_url", "")
    if not dood_url:
        return None

    print(f"  Attempting yt-dlp on DoodStream URL: {dood_url}")
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({
            "quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
            "force_generic_extractor": True,
            "extractor_args": {"generic": {"impersonate": ["chrome"]}},
        }) as ydl:
            info = ydl.extract_info(dood_url, download=False)
            
        for k in ("url", "hls_url", "manifest_url"):
            if info.get(k):
                print(f"  Resolved: {info[k][:90]}")
                return info[k]
                
        fmts = info.get("formats") or []
        if fmts:
            best = max((f for f in fmts if f.get("url")), key=lambda f: f.get("height") or 0, default=None)
            if best:
                print(f"  Resolved: {best['url'][:90]}")
                return best["url"]
                
    except Exception as e:
        print(f"  yt-dlp failed for {dood_url}: {str(e)[:80]}")
        
    return None


def upload_to_filemoon(url, title):
    """Upload a direct video URL to FileMoon via remote upload."""
    try:
        job = filemoon.remote_upload([url], title=title)
        jid = job.get("id") or job.get("job_id") or job.get("uuid")
        if not jid:
            fid = job.get("file_id") or job.get("id")
            if fid:
                return filemoon.poll_remote_job(str(fid), title=title, max_wait=300, interval=15)
            return {"status": "failed", "error": "no job id"}
        return filemoon.poll_remote_job(jid, title=title, max_wait=1800, interval=30)
    except Exception as e:
        return {"status": "error", "error": str(e)}


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


def process_entry(entry, mirror_db):
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

    # Find source on Cimafu
    source_url = find_source_for_entry(entry)
    if not source_url:
        return "no_source"

    # Upload to FileMoon
    print(f"  Uploading to FileMoon...")
    result = upload_to_filemoon(source_url, title)
    if result.get("status") != "completed" or not result.get("file_id"):
        print(f"  Upload failed: {result.get('status')} - {result.get('error')}")
        return "upload_failed"

    fid = result["file_id"]
    urls = filemoon.build_urls(fid)
    print(f"  FileMoon: {urls['filemoon_url']}")

    # Update Supabase
    ok = update_entry(entry, urls)
    if not ok:
        return "db_update_failed"

    # Save to mirror DB
    mirror_db["mapping"][filecode] = {
        "doodstream_filecode": filecode,
        "title": title,
        "filemoon_file_id": fid,
        "filemoon_url": urls["filemoon_url"],
        "filemoon_download_url": urls["filemoon_download_url"],
        "status": "mirrored",
        "source_url": source_url,
    }
    save_mirror_db(mirror_db)

    return "success"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Max entries to process (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="Search only, don't upload")
    args = ap.parse_args()

    movies, episodes = get_pending_entries()
    all_entries = movies + episodes

    if args.limit:
        all_entries = all_entries[:args.limit]

    mirror_db = load_mirror_db()
    stats = {"success": 0, "skipped": 0, "no_source": 0, "upload_failed": 0, "db_update_failed": 0, "error": 0}

    for i, entry in enumerate(all_entries, 1):
        print(f"\n[{i}/{len(all_entries)}]")
        try:
            if args.dry_run:
                # Just test search
                source_url = find_source_for_entry(entry)
                if source_url:
                    print(f"  Would upload: {source_url[:90]}")
                    stats["success"] += 1
                else:
                    stats["no_source"] += 1
            else:
                res = process_entry(entry, mirror_db)
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