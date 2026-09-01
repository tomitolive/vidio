#!/usr/bin/env python3
"""
Compare DoodStream account videos with Supabase database
Shows differences between what's in DoodStream vs what's in the database
"""

import os
import json
import sys
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Initialize Supabase client
supabase: Client | None = None
try:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if supabase_url and supabase_key:
        supabase = create_client(supabase_url, supabase_key)
        print("[supabase] Connected to Supabase")
    else:
        print("[supabase] SUPABASE_URL or SUPABASE_KEY not found in environment")
except Exception as e:
    print(f"[supabase] Failed to connect: {e}")


def fetch_all_doodstream_videos(api_key: str) -> list[dict]:
    """Fetch all videos from DoodStream account."""
    if not api_key:
        print("[doodstream] Error: No API key provided")
        return []
    
    print("[doodstream] Fetching videos from DoodStream account...")
    files = []
    page = 1
    
    while True:
        try:
            resp = requests.get(
                f"https://doodapi.com/api/file/list?key={api_key}&page={page}&per_page=100",
                timeout=20,
            )
            data = resp.json()
            if data.get("status") != 200:
                print(f"[doodstream] API returned status {data.get('status')}: {data.get('msg')}")
                break
            batch = data.get("result", {}).get("files", [])
            if not batch:
                break
            files.extend(batch)
            total_pages = data.get("result", {}).get("total_pages", 1)
            print(f"[doodstream] Fetched page {page}/{total_pages} ({len(batch)} files)")
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            print(f"[doodstream] Error fetching page {page}: {e}")
            break
    
    print(f"[doodstream] Total videos found: {len(files)}")
    return files


def fetch_all_supabase_movies() -> list[dict]:
    """Fetch all movies from Supabase database."""
    if not supabase:
        print("[supabase] Not connected, cannot fetch movies")
        return []
    
    try:
        print("[supabase] Fetching movies from database...")
        response = supabase.table("movies").select("*").execute()
        movies = response.data
        print(f"[supabase] Total movies found: {len(movies)}")
        return movies
    except Exception as e:
        print(f"[supabase] Error fetching movies: {e}")
        return []


def extract_filecode_from_url(url: str) -> str | None:
    """Extract filecode from DoodStream URL."""
    import re
    if not url:
        return None
    # Match patterns like:
    # https://doodstream.com/e/abc123
    # https://playmogo.com/d/abc123
    # https://playmogo.com/e/abc123
    patterns = [
        r'/[ed]/([a-zA-Z0-9]+)$',
        r'/d/([a-zA-Z0-9]+)$',
        r'/([a-zA-Z0-9]{8,})$',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def compare_data(doodstream_videos: list[dict], supabase_movies: list[dict]) -> dict:
    """Compare DoodStream videos with Supabase movies."""
    
    # Create sets of filecodes for comparison
    doodstream_filecodes = set()
    doodstream_by_filecode = {}
    
    for video in doodstream_videos:
        filecode = video.get("file_code") or video.get("filecode")
        if filecode:
            doodstream_filecodes.add(filecode)
            doodstream_by_filecode[filecode] = {
                "title": video.get("title", ""),
                "filecode": filecode,
                "upload_date": video.get("upload_date", ""),
                "size": video.get("size", ""),
                "download_url": f"https://playmogo.com/d/{filecode}",
                "embed_url": f"https://playmogo.com/e/{filecode}",
            }
    
    # Extract filecodes from Supabase movies
    supabase_filecodes = set()
    supabase_by_filecode = {}
    
    for movie in supabase_movies:
        # Try to extract filecode from doodstream_url or doodstream_download_url
        dood_url = movie.get("doodstream_url", "")
        download_url = movie.get("doodstream_download_url", "")
        
        filecode = None
        if dood_url:
            filecode = extract_filecode_from_url(dood_url)
        if not filecode and download_url:
            filecode = extract_filecode_from_url(download_url)
        
        if filecode:
            supabase_filecodes.add(filecode)
            supabase_by_filecode[filecode] = {
                "tmdb_id": movie.get("tmdb_id"),
                "title": movie.get("title", ""),
                "doodstream_url": dood_url,
                "doodstream_download_url": download_url,
                "created_at": movie.get("created_at", ""),
            }
    
    # Find differences
    in_doodstream_not_in_db = doodstream_filecodes - supabase_filecodes
    in_db_not_in_doodstream = supabase_filecodes - doodstream_filecodes
    common = doodstream_filecodes & supabase_filecodes
    
    return {
        "in_doodstream_not_in_db": [
            doodstream_by_filecode[fc] for fc in in_doodstream_not_in_db
        ],
        "in_db_not_in_doodstream": [
            supabase_by_filecode[fc] for fc in in_db_not_in_doodstream
        ],
        "common_count": len(common),
        "doodstream_total": len(doodstream_filecodes),
        "supabase_total": len(supabase_filecodes),
    }


def main():
    """Main function."""
    api_key = os.environ.get("DOODSTREAM_API_KEY")
    
    if not api_key:
        print("Error: DOODSTREAM_API_KEY not found in environment")
        print("Please set it in .env file or as environment variable")
        sys.exit(1)
    
    if not supabase:
        print("Error: Supabase not connected. Check SUPABASE_URL and SUPABASE_KEY")
        sys.exit(1)
    
    print("=" * 60)
    print("DoodStream vs Supabase Comparison")
    print("=" * 60)
    
    # Fetch data
    doodstream_videos = fetch_all_doodstream_videos(api_key)
    supabase_movies = fetch_all_supabase_movies()
    
    if not doodstream_videos:
        print("\nNo videos found in DoodStream account")
        sys.exit(0)
    
    if not supabase_movies:
        print("\nNo movies found in Supabase database")
    
    # Compare
    print("\n[compare] Analyzing differences...")
    result = compare_data(doodstream_videos, supabase_movies)
    
    # Print report
    print("\n" + "=" * 60)
    print("COMPARISON REPORT")
    print("=" * 60)
    print(f"Total in DoodStream: {result['doodstream_total']}")
    print(f"Total in Supabase: {result['supabase_total']}")
    print(f"Common (in both): {result['common_count']}")
    print(f"In DoodStream but NOT in Supabase: {len(result['in_doodstream_not_in_db'])}")
    print(f"In Supabase but NOT in DoodStream: {len(result['in_db_not_in_doodstream'])}")
    
    # Show details
    if result['in_doodstream_not_in_db']:
        print("\n" + "-" * 60)
        print(f"Videos in DoodStream but NOT in Database ({len(result['in_doodstream_not_in_db'])}):")
        print("-" * 60)
        for video in result['in_doodstream_not_in_db']:
            print(f"\n  Title: {video['title']}")
            print(f"  Filecode: {video['filecode']}")
            print(f"  Download: {video['download_url']}")
            print(f"  Embed: {video['embed_url']}")
            print(f"  Upload Date: {video['upload_date']}")
    
    if result['in_db_not_in_doodstream']:
        print("\n" + "-" * 60)
        print(f"Videos in Database but NOT in DoodStream ({len(result['in_db_not_in_doodstream'])}):")
        print("-" * 60)
        for movie in result['in_db_not_in_doodstream']:
            print(f"\n  Title: {movie['title']}")
            print(f"  TMDB ID: {movie['tmdb_id']}")
            print(f"  DoodStream URL: {movie['doodstream_url']}")
            print(f"  Download URL: {movie['doodstream_download_url']}")
            print(f"  Created At: {movie['created_at']}")
    
    # Save report to file
    report_file = "comparison_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print(f"\n[report] Detailed report saved to: {report_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
