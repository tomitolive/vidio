#!/usr/bin/env python3
"""
Identify and optionally delete duplicate videos from DoodStream account
Groups videos by title and shows duplicates
"""

import os
import json
import sys
import requests
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()


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


def group_by_title(videos: list[dict]) -> dict:
    """Group videos by normalized title."""
    groups = defaultdict(list)
    
    for video in videos:
        title = video.get("title", "").strip()
        if not title:
            title = "video"  # Default for unnamed videos
        
        # Normalize title for grouping (case-insensitive, remove extra spaces)
        normalized = " ".join(title.lower().split())
        
        groups[normalized].append({
            "filecode": video.get("file_code") or video.get("filecode"),
            "title": title,
            "upload_date": video.get("upload_date", ""),
            "size": video.get("size", ""),
            "download_url": f"https://playmogo.com/d/{video.get('file_code') or video.get('filecode')}",
        })
    
    return groups


def delete_doodstream_file(api_key: str, filecode: str) -> bool:
    """Delete a file from DoodStream account."""
    try:
        resp = requests.get(
            f"https://doodapi.com/api/file/delete?key={api_key}&file_code={filecode}",
            timeout=20,
        )
        data = resp.json()
        if data.get("status") == 200:
            print(f"[delete] Successfully deleted: {filecode}")
            return True
        else:
            print(f"[delete] Failed to delete {filecode}: {data.get('msg')}")
            return False
    except Exception as e:
        print(f"[delete] Error deleting {filecode}: {e}")
        return False


def main():
    """Main function."""
    api_key = os.environ.get("DOODSTREAM_API_KEY")
    
    if not api_key:
        print("Error: DOODSTREAM_API_KEY not found in environment")
        print("Please set it in .env file or as environment variable")
        sys.exit(1)
    
    print("=" * 60)
    print("DoodStream Duplicate Finder")
    print("=" * 60)
    
    # Fetch data
    videos = fetch_all_doodstream_videos(api_key)
    
    if not videos:
        print("\nNo videos found in DoodStream account")
        sys.exit(0)
    
    # Group by title
    print("\n[analyze] Grouping videos by title...")
    groups = group_by_title(videos)
    
    # Find duplicates
    duplicates = {title: group for title, group in groups.items() if len(group) > 1}
    
    print(f"\n[analyze] Found {len(duplicates)} titles with duplicates")
    print(f"[analyze] Total duplicate files: {sum(len(g) for g in duplicates.values())}")
    
    # Show duplicates
    print("\n" + "=" * 60)
    print("DUPLICATES FOUND")
    print("=" * 60)
    
    for title, group in sorted(duplicates.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"\n{title.upper()} ({len(group)} copies):")
        for i, video in enumerate(group, 1):
            print(f"  [{i}] Filecode: {video['filecode']}")
            print(f"      Title: {video['title']}")
            print(f"      Size: {video['size']}")
            print(f"      Upload Date: {video['upload_date']}")
            print(f"      Download: {video['download_url']}")
    
    # Auto-select best option: keep newest
    choice = "1"  # Keep only the newest upload (by upload_date)
    print("\n" + "=" * 60)
    print("AUTO-SELECTED: Keep only the newest upload (by upload_date)")
    print("=" * 60)
    
    # Confirm deletion
    print("\n" + "=" * 60)
    print("DRY RUN - Showing what will be deleted")
    print("=" * 60)
    
    to_delete = []
    
    for title, group in duplicates.items():
        if choice == "1":  # Keep newest
            # Sort by upload_date (empty dates treated as oldest)
            sorted_group = sorted(group, key=lambda x: x.get("upload_date") or "1970-01-01", reverse=True)
            keep = sorted_group[0]
            to_delete.extend([v for v in group if v != keep])
        
        elif choice == "2":  # Keep oldest
            sorted_group = sorted(group, key=lambda x: x.get("upload_date") or "2099-12-31")
            keep = sorted_group[0]
            to_delete.extend([v for v in group if v != keep])
        
        elif choice == "3":  # Keep largest
            sorted_group = sorted(group, key=lambda x: int(x.get("size") or 0), reverse=True)
            keep = sorted_group[0]
            to_delete.extend([v for v in group if v != keep])
        
        elif choice == "4":  # Keep smallest
            sorted_group = sorted(group, key=lambda x: int(x.get("size") or 0))
            keep = sorted_group[0]
            to_delete.extend([v for v in group if v != keep])
        
        elif choice == "5":  # Manual
            print(f"\n{title} ({len(group)} copies):")
            for i, video in enumerate(group, 1):
                print(f"  [{i}] {video['filecode']} - {video['title']} ({video['size']})")
            
            keep_idx = input(f"  Which one to keep? (1-{len(group)}): ").strip()
            try:
                keep_idx = int(keep_idx) - 1
                if 0 <= keep_idx < len(group):
                    keep = group[keep_idx]
                    to_delete.extend([v for v in group if v != keep])
                else:
                    print(f"  Invalid choice, skipping {title}")
            except ValueError:
                print(f"  Invalid input, skipping {title}")
    
    print(f"\n[summary] Will delete {len(to_delete)} files:")
    for video in to_delete:
        print(f"  - {video['filecode']} ({video['title']})")
    
    confirm = input("\nConfirm deletion? (yes/no): ").strip().lower()
    
    if confirm != "yes":
        print("Deletion cancelled")
        sys.exit(0)
    
    # Perform deletion
    print("\n[delete] Starting deletion...")
    deleted_count = 0
    failed_count = 0
    
    for video in to_delete:
        if delete_doodstream_file(api_key, video['filecode']):
            deleted_count += 1
        else:
            failed_count += 1
    
    print(f"\n[summary] Deletion complete:")
    print(f"  Successfully deleted: {deleted_count}")
    print(f"  Failed: {failed_count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
