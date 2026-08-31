#!/usr/bin/env python3
"""
Cima4u Category Crawler
Scrapes movie links from Cima4u category pages and processes them with jibi_bot.
"""

import os
import sys
import json
import time
import re
from typing import List, Dict, Set
from urllib.parse import unquote, urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import requests

# Import jibi_bot functions
from jibi_bot import run_jibi_bot, clean_url

# Configuration
PROCESSED_DB_FILE = "processed_cima4u_movies.json"
CATEGORIES = [
    "https://cimafu.cam/category/افلام-اجنبي/",
    "https://cimafu.cam/category/افلام-اسيوي/",
]


def load_processed_movies() -> Dict:
    """Load processed movies database."""
    try:
        if os.path.exists(PROCESSED_DB_FILE):
            with open(PROCESSED_DB_FILE, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return {"processed_urls": {}, "last_pages": {}, "last_updated": None}


def save_processed_movies(data: Dict):
    """Save processed movies database."""
    data["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(PROCESSED_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_movie_links_from_page(html: str) -> List[str]:
    """Extract movie URLs from category page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    movie_links = []
    
    # Find all MovieBlock elements
    movie_blocks = soup.find_all("li", class_="MovieBlock")
    for block in movie_blocks:
        link = block.find("a")
        if link and link.get("href"):
            url = clean_url(link["href"])
            if url:
                # Add /watch/ to the URL if not present
                if not url.endswith("/watch/"):
                    url = url.rstrip("/") + "/watch/"
                movie_links.append(url)
    
    return movie_links


def get_category_page_with_playwright(category_url: str, page_num: int = 1) -> str:
    """Load category page using Playwright."""
    from playwright.sync_api import sync_playwright
    
    # Add page number to URL if not first page
    if page_num > 1:
        url = f"{category_url.rstrip('/')}/page/{page_num}/"
    else:
        url = category_url
    
    print(f"[crawler] Loading: {url}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(3)
            html = page.content()
            return html
        except Exception as e:
            print(f"[crawler] Error loading page: {e}")
            return ""
        finally:
            browser.close()


def has_next_page(html: str) -> bool:
    """Check if there's a next page."""
    soup = BeautifulSoup(html, "html.parser")
    
    # Check for pagination with "next" or page numbers
    pagination = soup.find("div", class_="pagination")
    if pagination:
        # Look for "next" link or page numbers beyond current
        next_link = pagination.find("a", class_="next")
        if next_link:
            return True
        
        # Check if there are page numbers
        page_nums = pagination.find_all("a", class_="page-numbers")
        if page_nums:
            # If there's a current page and other pages exist
            current = pagination.find("span", class_="current")
            if current and len(page_nums) > 0:
                return True
    
    return False


def process_category(
    category_url: str,
    api_key: str,
    max_pages: int = None,
    max_movies: int = None,
    stop_on_first_success: bool = False
) -> Dict:
    """Process a category page and extract/process movies."""
    category_url = clean_url(category_url)
    processed_db = load_processed_movies()
    
    # Get last processed page for this category
    category_key = category_url.rstrip("/")
    last_page = processed_db["last_pages"].get(category_key, 0)
    
    stats = {
        "category": category_url,
        "pages_processed": 0,
        "movies_found": 0,
        "movies_processed": 0,
        "movies_skipped": 0,
        "errors": []
    }
    
    current_page = last_page + 1
    total_movies_processed = 0
    success_found = False
    
    while True:
        if max_pages and stats["pages_processed"] >= max_pages:
            print(f"[crawler] Reached max pages limit: {max_pages}")
            break
        
        if max_movies and total_movies_processed >= max_movies:
            print(f"[crawler] Reached max movies limit: {max_movies}")
            break
        
        if stop_on_first_success and success_found:
            print(f"[crawler] Stopping after first successful upload")
            break
        
        # Load page
        html = get_category_page_with_playwright(category_url, current_page)
        if not html:
            print(f"[crawler] No HTML loaded for page {current_page}")
            break
        
        # Extract movie links
        movie_links = extract_movie_links_from_page(html)
        stats["movies_found"] += len(movie_links)
        
        if not movie_links:
            print(f"[crawler] No movies found on page {current_page}")
            break
        
        print(f"[crawler] Page {current_page}: Found {len(movie_links)} movies")
        
        # Process each movie (only one successful upload per page)
        page_success = False
        for movie_url in movie_links:
            if max_movies and total_movies_processed >= max_movies:
                break
            
            if stop_on_first_success and success_found:
                print(f"[crawler] Stopping after first successful upload")
                break
            
            # Check if already processed
            if movie_url in processed_db["processed_urls"]:
                print(f"[crawler] Skipping already processed: {movie_url[:60]}...")
                stats["movies_skipped"] += 1
                continue
            
            print(f"[crawler] Processing: {movie_url[:60]}...")
            
            try:
                # Call jibi_bot to process the movie
                result = run_jibi_bot(movie_url, api_key=api_key)
                
                if result.get("success"):
                    # Mark as processed
                    processed_db["processed_urls"][movie_url] = {
                        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "filecode": result.get("filecode"),
                        "tmdb_id": result.get("tmdb_id")
                    }
                    stats["movies_processed"] += 1
                    total_movies_processed += 1
                    success_found = True
                    page_success = True
                    print(f"[crawler] ✓ Success: filecode={result.get('filecode')}, tmdb_id={result.get('tmdb_id')}")
                    
                    if stop_on_first_success:
                        print(f"[crawler] First successful upload completed, stopping...")
                        break
                    else:
                        print(f"[crawler] One video uploaded for this page, moving to next page...")
                        break
                else:
                    stats["errors"].append(f"Failed: {movie_url}")
                    print(f"[crawler] ✗ Failed: {result.get('errors', [])}")
                
                # Save progress after each movie
                save_processed_movies(processed_db)
                
                # Small delay to avoid overwhelming the server
                time.sleep(2)
                
            except Exception as e:
                error_msg = f"Error processing {movie_url}: {e}"
                stats["errors"].append(error_msg)
                print(f"[crawler] Error: {error_msg}")
        
        # Update last processed page
        processed_db["last_pages"][category_key] = current_page
        stats["pages_processed"] += 1
        
        # Check if there's a next page
        if not has_next_page(html):
            print(f"[crawler] No more pages found")
            break
        
        current_page += 1
        time.sleep(1)  # Delay between pages
    
    # Final save
    save_processed_movies(processed_db)
    
    return stats


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Cima4u Category Crawler")
    parser.add_argument("--category", help="Category URL to process")
    parser.add_argument("--api-key", required=True, help="DoodStream API key")
    parser.add_argument("--max-pages", type=int, help="Maximum pages to process")
    parser.add_argument("--max-movies", type=int, help="Maximum movies to process")
    parser.add_argument("--all-categories", action="store_true", help="Process all predefined categories")
    parser.add_argument("--stop-on-first-success", action="store_true", help="Stop after first successful upload")
    
    args = parser.parse_args()
    
    if args.all_categories:
        categories = CATEGORIES
    elif args.category:
        categories = [args.category]
    else:
        print("Error: Please provide --category or --all-categories")
        sys.exit(1)
    
    print(f"[crawler] Starting crawler with {len(categories)} categories")
    print(f"[crawler] Max pages: {args.max_pages or 'unlimited'}")
    print(f"[crawler] Max movies: {args.max_movies or 'unlimited'}")
    print(f"[crawler] Stop on first success: {args.stop_on_first_success}")
    
    all_stats = []
    
    for category in categories:
        print(f"\n{'='*60}")
        print(f"Processing category: {category}")
        print(f"{'='*60}")
        
        stats = process_category(
            category,
            args.api_key,
            max_pages=args.max_pages,
            max_movies=args.max_movies,
            stop_on_first_success=args.stop_on_first_success
        )
        all_stats.append(stats)
        
        print(f"\n[stats] Pages: {stats['pages_processed']}")
        print(f"[stats] Found: {stats['movies_found']}")
        print(f"[stats] Processed: {stats['movies_processed']}")
        print(f"[stats] Skipped: {stats['movies_skipped']}")
        print(f"[stats] Errors: {len(stats['errors'])}")
    
    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    total_processed = sum(s["movies_processed"] for s in all_stats)
    total_found = sum(s["movies_found"] for s in all_stats)
    total_skipped = sum(s["movies_skipped"] for s in all_stats)
    
    print(f"Total movies found: {total_found}")
    print(f"Total movies processed: {total_processed}")
    print(f"Total movies skipped: {total_skipped}")


if __name__ == "__main__":
    main()
