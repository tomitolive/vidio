#!/usr/bin/env python3
"""
fast_check.py
-------------
A quick script to check what DoodStream files are missing from Supabase
without doing TMDB lookups (which are very slow). It determines TV vs Movie
purely from the presence of SxxExx in the filename.
"""

import os
import sys
import re
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

supabase: Client | None = None
try:
    _url = os.environ.get("SUPABASE_URL")
    _key = os.environ.get("SUPABASE_KEY")
    if _url and _key:
        supabase = create_client(_url, _key)
except Exception as e:
    print(f"Error connecting to Supabase: {e}")
    sys.exit(1)


def is_tv_episode(title: str) -> bool:
    """True if title looks like a TV episode (e.g. S01E05)."""
    return bool(re.search(r"[Ss]\d+[Ee]\d+", title))

def extract_filecode(url: str) -> str | None:
    if not url: return None
    patterns = [r'/[ed]/([a-zA-Z0-9]+)$', r'/d/([a-zA-Z0-9]+)$', r'/([a-zA-Z0-9]{8,})$']
    for p in patterns:
        m = re.search(p, url)
        if m: return m.group(1)
    return None

def fetch_db_filecodes() -> tuple[set, set]:
    """Returns (movie_filecodes, tv_filecodes) from DB"""
    movie_fcs = set()
    tv_fcs = set()

    # Movies
    limit = 1000
    offset = 0
    while True:
        res = supabase.table("movies").select("doodstream_url, doodstream_download_url").range(offset, offset+limit-1).execute()
        if not res.data: break
        for row in res.data:
            fc = extract_filecode(row.get("doodstream_url")) or extract_filecode(row.get("doodstream_download_url"))
            if fc: movie_fcs.add(fc)
        if len(res.data) < limit: break
        offset += limit

    # TV
    offset = 0
    while True:
        res = supabase.table("tv_episodes").select("filecode, doodstream_url, doodstream_download_url").range(offset, offset+limit-1).execute()
        if not res.data: break
        for row in res.data:
            fc = row.get("filecode") or extract_filecode(row.get("doodstream_url")) or extract_filecode(row.get("doodstream_download_url"))
            if fc: tv_fcs.add(fc)
        if len(res.data) < limit: break
        offset += limit

    return movie_fcs, tv_fcs


def main():
    api_key = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DOODSTREAM_API_KEY")
    if not api_key:
        print("Missing DOODSTREAM_API_KEY")
        sys.exit(1)

    print(f"Fetching DoodStream files for key {api_key[:6]}...")
    files = []
    page = 1
    while True:
        resp = requests.get(f"https://doodapi.com/api/file/list?key={api_key}&page={page}&per_page=100")
        data = resp.json()
        if data.get("status") != 200: break
        batch = data.get("result", {}).get("files", [])
        if not batch: break
        files.extend(batch)
        if page >= data.get("result", {}).get("total_pages", 1): break
        page += 1

    print(f"Found {len(files)} total files in DoodStream.")
    movie_db, tv_db = fetch_db_filecodes()
    all_db = movie_db | tv_db

    missing_tv = []
    missing_movie = []

    for f in files:
        fc = f.get("file_code") or f.get("filecode")
        title = f.get("title", "")
        if not fc or fc in all_db:
            continue
        
        # Missing from DB
        is_tv = is_tv_episode(title)
        
        if is_tv:
            missing_tv.append((fc, title))
        else:
            missing_movie.append((fc, title))

    # Sort
    missing_tv.sort(key=lambda x: x[1])
    missing_movie.sort(key=lambda x: x[1])

    print("\n" + "="*60)
    print("FAST MISSING CHECK (Bypassing TMDB)")
    print("="*60)
    print(f"Movies missing from DB: {len(missing_movie)}")
    print(f"TV Eps missing from DB: {len(missing_tv)}")

    if missing_tv:
        print("\n\n>>> MISSING TV EPISODES (First 50) <<<")
        for fc, title in missing_tv[:50]:
            print(f"- {title}  [{fc}]")
    if len(missing_tv) > 50:
        print(f"... and {len(missing_tv) - 50} more TV episodes.")
        
    print("\n" + "="*60)


if __name__ == "__main__":
    main()
