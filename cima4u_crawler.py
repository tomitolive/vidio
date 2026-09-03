#!/usr/bin/env python3
"""
Cima4u Category Crawler
Scrapes movie links from Cima4u category pages and processes them with jibi_bot.
Supports both movies and TV series episodes.
"""

import os
import sys
import json
import time
import re
import sqlite3
from typing import List, Dict, Set, Tuple
from urllib.parse import unquote, urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import requests

# Import jibi_bot functions
from jibi_bot import run_jibi_bot, clean_url

# Configuration
PROCESSED_DB_FILE = "processed_cima4u_movies.json"
TV_DB_FILE = "tv_series.db"
MOVIE_CATEGORIES = [
    "https://cimafu.cam/category/افلام-اجنبي/",
    "https://cimafu.cam/category/افلام-اسيوي/",
]
TV_CATEGORIES = [
    "https://cimafu.cam/category/مسلسلات-اجنبي/",
    "https://cimafu.cam/category/مسلسلات-اسيوي/",
]
CATEGORIES = MOVIE_CATEGORIES + TV_CATEGORIES


# ─── TV Series Database Functions ─────────────────────────────────────────────

def init_tv_database():
    """Initialize SQLite database for TV series and episodes."""
    conn = sqlite3.connect(TV_DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            url TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            season INTEGER,
            episode INTEGER,
            url TEXT UNIQUE NOT NULL,
            filecode TEXT,
            tmdb_id TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (series_id) REFERENCES series(id)
        )
    ''')
    
    conn.commit()
    return conn


def save_series_to_db(conn, series_name: str, url: str = None):
    """Save a series to the database and return its ID."""
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO series (name, url) VALUES (?, ?)",
        (series_name, url)
    )
    conn.commit()
    c.execute("SELECT id FROM series WHERE name = ?", (series_name,))
    return c.fetchone()[0]


def save_episode_to_db(conn, series_id: int, season, episode, url, filecode=None, tmdb_id=None):
    """Save an episode to the database."""
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO episodes (series_id, season, episode, url, filecode, tmdb_id) VALUES (?, ?, ?, ?, ?, ?)",
            (series_id, season, episode, url, filecode, tmdb_id)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_series_stats(conn):
    """Get statistics about series in the database."""
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM series")
    total_series = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM episodes")
    total_episodes = c.fetchone()[0]
    
    c.execute('''
        SELECT s.name, COUNT(e.id) as ep_count 
        FROM series s 
        LEFT JOIN episodes e ON s.id = e.series_id 
        GROUP BY s.id 
        ORDER BY ep_count DESC
    ''')
    series_list = c.fetchall()
    
    return {
        "total_series": total_series,
        "total_episodes": total_episodes,
        "series_list": series_list
    }


# ─── Episode Detection Functions ──────────────────────────────────────────────

def is_episode_url(url: str) -> bool:
    """Check if URL points to a TV episode (contains الحلقة or episodes pattern)."""
    decoded = unquote(url).lower()
    return "الحلقة" in decoded or "حلقة" in decoded


def is_series_url(url: str) -> bool:
    """Check if URL points to a series page (not an episode directly)."""
    decoded = unquote(url).lower()
    # Series pages have مسلسل or انمي but NOT الحلقة
    has_series = "مسلسل" in decoded or "انمي" in decoded
    has_episode = "الحلقة" in decoded or "حلقة" in decoded
    return has_series and not has_episode


def extract_episode_from_url(url: str) -> dict:
    """Extract series name, season, and episode from Cima4u URL.
    
    Examples:
    "مشاهدة-انمي-وتحميل-one-piece-الحلقة-1176-مترجمة"
    -> {"series_name": "one piece", "season": None, "episode": 1176}
    
    "مشاهدة-مسلسل-وتحميل-mushoku-tensei-الموسم-الثالث-10"
    -> {"series_name": "mushoku tensei", "season": 3, "episode": 10}
    """
    decoded = unquote(url)
    
    result = {
        "series_name": None,
        "season": None,
        "episode": None,
        "media_type": "tv"
    }
    
    # Extract episode number
    ep_match = re.search(r'الحلقة-(\d+)', decoded)
    if ep_match:
        result["episode"] = int(ep_match.group(1))
    
    # Extract season number from Arabic words
    season_patterns = {
        "الموسم-الأول": 1, "الموسم-الاول": 1,
        "الموسم-الثاني": 2, "الموسم-التاني": 2,
        "الموسم-الثالث": 3,
        "الموسم-الرابع": 4,
        "الموسم-الخامس": 5,
        "الموسم-السادس": 6,
        "الموسم-السابع": 7,
        "الموسم-الثامن": 8,
        "الموسم-التاسع": 9,
        "الموسم-العاشر": 10,
    }
    
    for pattern, num in season_patterns.items():
        if pattern in decoded:
            result["season"] = num
            break
    
    # Extract season number from digit (e.g., "الموسم-3")
    if result["season"] is None:
        season_digit_match = re.search(r'الموسم-(\d+)', decoded)
        if season_digit_match:
            result["season"] = int(season_digit_match.group(1))
    
    # Extract series name (English text between arrows)
    # Pattern: مشاهدة-انمي-وتحميل-[SERIES-NAME]-الموسم or الحلقة
    name_match = re.search(r'وتحميل-(.+?)-(?:الموسم|الحلقة)', decoded)
    if name_match:
        result["series_name"] = name_match.group(1).replace("-", " ").strip()
    else:
        # Fallback: try to extract from URL slug
        name_match2 = re.search(r'/([^/]+?)-(?:الموسم|الحلقة)', decoded)
        if name_match2:
            result["series_name"] = name_match2.group(1).replace("-", " ").strip()
    
    return result


def extract_series_name_from_url(url: str) -> str:
    """Extract series name from episode URL."""
    info = extract_episode_from_url(url)
    return info.get("series_name", "")


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


def extract_movie_links_from_page(html: str) -> List[Tuple[str, dict]]:
    """Extract movie/episode URLs from category page HTML.
    
    Returns list of tuples: (url, info_dict)
    info_dict contains: {"type": "movie"|"episode", "series_name", "season", "episode"}
    """
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
                
                # Detect if this is an episode or movie
                if is_episode_url(url):
                    ep_info = extract_episode_from_url(url)
                    movie_links.append((url, {
                        "type": "episode",
                        **ep_info
                    }))
                else:
                    movie_links.append((url, {
                        "type": "movie",
                        "series_name": None,
                        "season": None,
                        "episode": None
                    }))
    
    return movie_links


def get_category_page_with_playwright(category_url: str, page_num: int = 1) -> str:
    """Load category page using Playwright (through a live proxy)."""
    from playwright.sync_api import sync_playwright
    from jibi_bot import get_next_proxy, get_all_proxies, parse_playwright_proxy

    # Add page number to URL if not first page
    if page_num > 1:
        url = f"{category_url.rstrip('/')}/page/{page_num}/"
    else:
        url = category_url

    print(f"[crawler] Loading: {url}")

    # Build ordered list of proxies to try, then direct connection as last resort
    proxy_attempts = list(get_all_proxies())
    if proxy_attempts:
        get_next_proxy()  # advance rotation past first
    proxy_attempts = list(proxy_attempts[:5]) + [None]  # None = direct connection

    with sync_playwright() as p:
        for attempt_idx, proxy_url in enumerate(proxy_attempts):
            pw_proxy = parse_playwright_proxy(proxy_url) if proxy_url else None
            if proxy_url:
                masked = proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url
                print(f"[crawler] Using proxy [{attempt_idx+1}]: {masked}")
            else:
                print(f"[crawler] Trying direct connection (no proxy)...")

            launch_kwargs = {
                "headless": True,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1920,1080",
                ],
            }
            if pw_proxy:
                launch_kwargs["proxy"] = pw_proxy

            try:
                browser = p.chromium.launch(**launch_kwargs)
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
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)
                    html = page.content()
                    return html
                except Exception as e:
                    print(f"[crawler] Proxy [{attempt_idx+1}] failed: {str(e)[:120]}")
                finally:
                    browser.close()
            except Exception as e:
                print(f"[crawler] Browser launch failed on attempt {attempt_idx+1}: {str(e)[:120]}")

    return ""


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
    stop_on_first_success: bool = False,
    mode: str = "all",
    tv_conn=None
) -> Dict:
    """Process a category page and extract/process movies and episodes.
    
    mode: "movies" = only movies, "tv" = only TV episodes, "all" = both
    """
    category_url = clean_url(category_url)
    processed_db = load_processed_movies()
    
    # Always start from page 1 to get latest updates
    category_key = category_url.rstrip("/")
    
    stats = {
        "category": category_url,
        "pages_processed": 0,
        "movies_found": 0,
        "episodes_found": 0,
        "movies_processed": 0,
        "episodes_processed": 0,
        "movies_skipped": 0,
        "episodes_skipped": 0,
        "errors": []
    }
    
    current_page = 1  # Always start from page 1
    total_processed = 0
    success_found = False
    
    while True:
        if max_pages and stats["pages_processed"] >= max_pages:
            print(f"[crawler] Reached max pages limit: {max_pages}")
            break
        
        if max_movies and total_processed >= max_movies:
            print(f"[crawler] Reached max items limit: {max_movies}")
            break
        
        # Load page
        html = get_category_page_with_playwright(category_url, current_page)
        if not html:
            print(f"[crawler] No HTML loaded for page {current_page}")
            break
        
        # Extract movie/episode links with info
        links_with_info = extract_movie_links_from_page(html)
        
        # Filter based on mode
        if mode == "movies":
            links_with_info = [(url, info) for url, info in links_with_info if info["type"] == "movie"]
        elif mode == "tv":
            links_with_info = [(url, info) for url, info in links_with_info if info["type"] == "episode"]
        
        # Count separately
        movie_count = sum(1 for _, info in links_with_info if info["type"] == "movie")
        episode_count = sum(1 for _, info in links_with_info if info["type"] == "episode")
        stats["movies_found"] += movie_count
        stats["episodes_found"] += episode_count
        
        if not links_with_info:
            print(f"[crawler] No content found on page {current_page}")
            break
        
        print(f"[crawler] Page {current_page}: Found {movie_count} movies, {episode_count} episodes")
        
        # Process each item on the page
        page_success = False
        page_has_dup = False
        page_has_fail = False
        
        for url, info in links_with_info:
            if max_movies and total_processed >= max_movies:
                break
            
            if stop_on_first_success and success_found:
                print(f"[crawler] Stopping after first successful upload")
                break
            
            # Check if already processed (by URL)
            if url in processed_db["processed_urls"]:
                item_type = "Episode" if info["type"] == "episode" else "Movie"
                print(f"[crawler] Skipping already processed {item_type}: {url[:60]}...")
                if info["type"] == "episode":
                    stats["episodes_skipped"] += 1
                else:
                    stats["movies_skipped"] += 1
                continue
            
            item_type = "Episode" if info["type"] == "episode" else "Movie"
            if info["type"] == "episode":
                print(f"[crawler] Processing {item_type}: {info.get('series_name', '?')} S{info.get('season', '?')}E{info.get('episode', '?')}")
            else:
                print(f"[crawler] Processing {item_type}: {url[:60]}...")
            
            try:
                # Call jibi_bot to process the item
                result = run_jibi_bot(url, api_key=api_key)
                
                # Duplicate: already uploaded
                if result.get("skipped_duplicate"):
                    if info["type"] == "episode":
                        stats["episodes_skipped"] += 1
                    else:
                        stats["movies_skipped"] += 1
                    page_has_dup = True
                    print(f"[crawler] ↻ Duplicate (already uploaded): filecode={result.get('filecode')}")
                    
                    # Save to processed_urls
                    processed_db["processed_urls"][url] = {
                        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "filecode": result.get("filecode"),
                        "tmdb_id": result.get("tmdb_id"),
                        "type": info["type"],
                        "series_name": info.get("series_name"),
                        "season": info.get("season"),
                        "episode": info.get("episode"),
                    }
                    save_processed_movies(processed_db)
                    time.sleep(1)
                    continue
                
                if result.get("success"):
                    if info["type"] == "episode":
                        stats["episodes_processed"] += 1
                        # Save to TV database
                        if tv_conn and info.get("series_name"):
                            series_id = save_series_to_db(tv_conn, info["series_name"])
                            save_episode_to_db(
                                tv_conn, series_id,
                                info.get("season"), info.get("episode"),
                                url, result.get("filecode"), result.get("tmdb_id")
                            )
                    else:
                        stats["movies_processed"] += 1
                    total_processed += 1
                    success_found = True
                    page_success = True
                    
                    print(f"[crawler] ✓ Success: filecode={result.get('filecode')}, tmdb_id={result.get('tmdb_id')}")
                    
                    # Save to processed_urls after successful upload
                    processed_db["processed_urls"][url] = {
                        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "filecode": result.get("filecode"),
                        "tmdb_id": result.get("tmdb_id"),
                        "type": info["type"],
                        "series_name": info.get("series_name"),
                        "season": info.get("season"),
                        "episode": info.get("episode"),
                    }
                    save_processed_movies(processed_db)
                    
                    print(f"[crawler] Video uploaded, continuing...")
                else:
                    page_has_fail = True
                    stats["errors"].append(f"Failed: {url}")
                    print(f"[crawler] ✗ Failed: {result.get('errors', [])}")
                
                # Small delay
                time.sleep(2)
                
            except Exception as e:
                page_has_fail = True
                error_msg = f"Error processing {url}: {e}"
                stats["errors"].append(error_msg)
                print(f"[crawler] Error: {error_msg}")
        
        # Handle page advancement
        if page_success:
            pass
        elif page_has_dup and not page_has_fail:
            print(f"[crawler] Page {current_page} fully duplicated, advancing")
        else:
            print(f"[crawler] ⚠ No successful upload on page {current_page}, not advancing")
            break

        # Update last processed page
        processed_db["last_pages"][category_key] = current_page
        stats["pages_processed"] += 1
        
        # Check if there's a next page
        if not has_next_page(html):
            print(f"[crawler] No more pages found")
            break
        
        current_page += 1
        time.sleep(1)
    
    # Final save
    save_processed_movies(processed_db)
    
    return stats


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Cima4u Category Crawler")
    parser.add_argument("--mode", choices=["movies", "tv", "all"], default="all",
                        help="Scraping mode: movies = film only, tv = mouselat only, all = both")
    parser.add_argument("--category", help="Category URL to process")
    parser.add_argument("--api-key", required=True, help="DoodStream API key")
    parser.add_argument("--max-pages", type=int, default=20, help="Maximum pages to process (default: 20)")
    parser.add_argument("--max-movies", type=int, default=50, help="Maximum movies/episodes to process (default: 50)")
    parser.add_argument("--all-categories", action="store_true", help="Process all predefined categories")
    parser.add_argument("--stop-on-first-success", action="store_true", help="Stop after first successful upload (deprecated)")
    
    args = parser.parse_args()
    
    # Select categories based on mode
    if args.mode == "movies":
        categories = MOVIE_CATEGORIES
        print(f"[crawler] Mode: MOVIES ONLY")
    elif args.mode == "tv":
        categories = TV_CATEGORIES
        print(f"[crawler] Mode: TV SERIES ONLY")
    else:
        categories = CATEGORIES
        print(f"[crawler] Mode: ALL (movies + TV)")
    
    if args.category:
        categories = [args.category]
    elif not args.all_categories:
        # Use default categories based on mode
        pass
    else:
        # Keep categories based on mode
        pass
    
    # Initialize TV database if needed
    tv_conn = None
    if args.mode in ("tv", "all"):
        tv_conn = init_tv_database()
        print(f"[crawler] TV database initialized: {TV_DB_FILE}")

    print(f"[crawler] Starting crawler with {len(categories)} categories")
    print(f"[crawler] Max pages: {args.max_pages or 'unlimited'}")
    print(f"[crawler] Max items: {args.max_movies or 'unlimited'}")
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
            stop_on_first_success=args.stop_on_first_success,
            mode=args.mode,
            tv_conn=tv_conn
        )
        all_stats.append(stats)
        
        print(f"\n[stats] Pages: {stats['pages_processed']}")
        print(f"[stats] Movies found: {stats['movies_found']}, Processed: {stats['movies_processed']}, Skipped: {stats['movies_skipped']}")
        print(f"[stats] Episodes found: {stats['episodes_found']}, Processed: {stats['episodes_processed']}, Skipped: {stats['episodes_skipped']}")
        print(f"[stats] Errors: {len(stats['errors'])}")
    
    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    total_movies_found = sum(s["movies_found"] for s in all_stats)
    total_movies_processed = sum(s["movies_processed"] for s in all_stats)
    total_movies_skipped = sum(s["movies_skipped"] for s in all_stats)
    total_episodes_found = sum(s["episodes_found"] for s in all_stats)
    total_episodes_processed = sum(s["episodes_processed"] for s in all_stats)
    total_episodes_skipped = sum(s["episodes_skipped"] for s in all_stats)
    
    print(f"Movies: {total_movies_found} found, {total_movies_processed} processed, {total_movies_skipped} skipped")
    print(f"Episodes: {total_episodes_found} found, {total_episodes_processed} processed, {total_episodes_skipped} skipped")
    
    # Print TV database stats
    if tv_conn:
        print(f"\n{'='*60}")
        print("TV SERIES DATABASE")
        print(f"{'='*60}")
        tv_stats = get_series_stats(tv_conn)
        print(f"Total series: {tv_stats['total_series']}")
        print(f"Total episodes: {tv_stats['total_episodes']}")
        if tv_stats['series_list']:
            print(f"\nTop series by episodes:")
            for name, count in tv_stats['series_list'][:10]:
                print(f"  {name}: {count} episodes")
        tv_conn.close()


if __name__ == "__main__":
    main()
