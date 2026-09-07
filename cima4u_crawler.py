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

# Import catalog/supabase helpers for placement + duplicate pre-check
from catalog import (
    supabase,
    find_in_supabase,
    find_episode_in_supabase_by_title,
    search_tmdb_api,
    search_tmdb_by_slug,
    extract_cima4u_info,
    _known_override,
)

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


# ─── Supabase Duplicate Pre-check ─────────────────────────────────────────────

def resolve_episode_tmdb_precheck(info: dict) -> tuple:
    """Resolve (tmdb_id, season, episode) for a crawler episode item.

    Uses catalog overrides first (fast, no API call), then TMDB search.
    """
    name = info.get("series_name") or ""
    if not name:
        return None, info.get("season"), info.get("episode")
    hint_tmdb, hint_season = _known_override(name)
    if hint_tmdb:
        season = hint_season if hint_season is not None else info.get("season")
        return hint_tmdb, season, info.get("episode")
    tmdb_id = search_tmdb_api(name, "", "tv", info.get("season"), info.get("episode"))
    return tmdb_id, info.get("season"), info.get("episode")


def resolve_movie_tmdb_precheck(url: str) -> int | None:
    """Resolve tmdb_id for a movie item from the Cima4u URL, or None."""
    slug, year = extract_cima4u_info(url)
    if slug and year:
        return search_tmdb_by_slug(slug, year)
    return None


def already_in_supabase(url: str, info: dict) -> bool:
    """Return True if this movie/episode is already saved in Supabase."""
    if supabase is None:
        return False
    if info["type"] == "episode":
        ptmdb, psea, pep = resolve_episode_tmdb_precheck(info)
        # Check 1: by tmdb_id (fast, precise)
        if ptmdb and find_in_supabase(ptmdb, "tv", psea, pep):
            return True
        # Check 2: fallback by series_title + season + episode
        series_name = info.get("series_name") or ""
        season = info.get("season")
        episode = info.get("episode")
        if series_name and season is not None and episode is not None:
            if find_episode_in_supabase_by_title(series_name, season, episode):
                return True
        return False
    ptmdb = resolve_movie_tmdb_precheck(url)
    return bool(ptmdb) and find_in_supabase(ptmdb, "movie")


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


def get_page_with_flaresolverr(url: str, timeout: int = 45) -> str:
    """Fetch a page via local FlareSolverr (Cloudflare bypass), returns HTML or ''."""
    import requests as _req
    try:
        resp = _req.post(
            "http://localhost:8191/v1",
            json={
                "cmd": "request.get",
                "url": url,
                "maxTimeout": timeout * 1000,
                "request": {
                    "headers": {
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        )
                    }
                },
            },
            timeout=timeout + 10,
        )
        data = resp.json()
        if data.get("status") == "ok" and data.get("solution", {}).get("response"):
            return data["solution"]["response"]
        return ""
    except Exception as e:
        print(f"[crawler] FlareSolverr error: {str(e)[:120]}")
        return ""


def get_category_page_with_playwright(category_url: str, page_num: int = 1) -> str:
    """Load category page. Tries FlareSolverr first, then live proxies, then direct."""
    # Add page number to URL if not first page
    if page_num > 1:
        url = f"{category_url.rstrip('/')}/page/{page_num}/"
    else:
        url = category_url

    print(f"[crawler] Loading: {url}")

    # 1) Prefer FlareSolverr (Cloudflare bypass, runs locally in the workflow)
    html = get_page_with_flaresolverr(url)
    if html:
        print(f"[crawler] Loaded via FlareSolverr ({len(html)} bytes)")
        return html

    # 2) Fall back to Playwright through live proxies, then direct connection
    from playwright.sync_api import sync_playwright
    from jibi_bot import get_next_proxy, get_all_proxies, parse_playwright_proxy

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

            # Supabase pre-check: skip re-uploading content already in the DB.
            # This stops duplicate clones even when the local cache is lost.
            if supabase is not None:
                try:
                    dup = already_in_supabase(url, info)
                except Exception as e:
                    dup = False
                    print(f"[crawler] Supabase pre-check error ({str(e)[:80]}), continuing")
                if dup:
                    label = f"S{info.get('season')}E{info.get('episode')}" if info["type"] == "episode" else "movie"
                    print(f"[crawler] ↻ Already in Supabase, skipping upload ({label}): {url[:55]}...")
                    if info["type"] == "episode":
                        stats["episodes_skipped"] += 1
                    else:
                        stats["movies_skipped"] += 1
                    page_has_dup = True
                    processed_db["processed_urls"][url] = {
                        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "filecode": None,
                        "tmdb_id": None,
                        "type": info["type"],
                        "series_name": info.get("series_name"),
                        "season": info.get("season"),
                        "episode": info.get("episode"),
                        "reason": "already_in_supabase",
                    }
                    save_processed_movies(processed_db)
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
                    placement = "tv_episodes" if result.get("media_type") == "tv" else "movies"
                    print(f"[crawler] → saved to {placement} (tmdb_id={result.get('tmdb_id')}, filecode={result.get('filecode')})")
                    
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



# ─── Homepage Scraper (no video upload) ───────────────────────────────────────

HOMEPAGE_STATE_FILE = "homepage_scraper_state.json"


def load_homepage_state() -> dict:
    """Load the state of previously scraped homepage URLs."""
    try:
        if os.path.exists(HOMEPAGE_STATE_FILE):
            with open(HOMEPAGE_STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return {"seen_urls": {}, "last_updated": None}


def save_homepage_state(state: dict):
    """Save the homepage scraper state."""
    state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(HOMEPAGE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def extract_card_title(block) -> str:
    """Extract the clean display title from a MovieBlock <li> element."""
    box = block.find("div", class_="BoxTitle")
    if not box:
        return ""
    # Get only the direct text nodes (not children tags like BoxTitleInfo)
    text_nodes = []
    for node in box.children:
        if isinstance(node, str):
            text_nodes.append(node.strip())
    full = " ".join(t for t in text_nodes if t)
    full = re.sub(r"\s{2,}", " ", full).strip()
    return full


def extract_homepage_cards(html: str) -> list[dict]:
    """Parse all movie/episode cards from the homepage HTML."""
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for block in soup.find_all("li", class_="MovieBlock"):
        link = block.find("a")
        if not link or not link.get("href"):
            continue
        raw_href = link["href"]
        url = raw_href.rstrip("/") + "/"
        title = extract_card_title(block)
        if not title:
            bt = block.find("div", class_="BoxTitle")
            title = bt.get_text(" ", strip=True) if bt else ""
        # Detect type from the RAW (encoded) href before normalization
        is_ep = is_episode_url(raw_href)
        ep_info = extract_episode_from_url(raw_href) if is_ep else {}
        cards.append({
            "url": url,
            "title": title,
            "is_episode": is_ep,
            "series_name": ep_info.get("series_name"),
            "season": ep_info.get("season"),
            "episode": ep_info.get("episode"),
        })
    return cards


def save_card_to_supabase(card: dict, tmdb_id: int | None) -> bool:
    """Save a discovered card (title only, no filecode) to Supabase."""
    if not supabase:
        return False

    if card["is_episode"]:
        import zlib
        s_title = card.get("series_name") or card["title"]
        tid = tmdb_id or (9000000 + (zlib.crc32(s_title.encode()) % 1000000))
        season = card.get("season") or 1
        episode = card.get("episode") or 1
        data = {
            "tmdb_id": tid,
            "series_title": s_title,
            "season_number": season,
            "episode_number": episode,
            "title": card["title"],
        }
        try:
            existing = (
                supabase.table("tv_episodes")
                .select("id")
                .eq("tmdb_id", tid)
                .eq("season_number", season)
                .eq("episode_number", episode)
                .execute()
            )
            if not existing.data:
                supabase.table("tv_episodes").insert(data).execute()
                print(f"[homepage] ✅ TV saved: {s_title} S{season}E{episode}")
            else:
                print(f"[homepage]    already in DB: {s_title} S{season}E{episode}")
            return True
        except Exception as e:
            print(f"[homepage] ✗ TV insert error: {e}")
            return False
    else:
        import zlib
        tid = tmdb_id or (8000000 + (zlib.crc32(card["title"].encode()) % 1000000))
        data = {
            "tmdb_id": tid,
            "title": card["title"],
            "media_type": "movie",
        }
        try:
            existing = supabase.table("movies").select("id").eq("tmdb_id", tid).execute()
            if not existing.data:
                supabase.table("movies").insert(data).execute()
                print(f"[homepage] ✅ Movie saved: {card['title']}")
            else:
                print(f"[homepage]    already in DB: {card['title']}")
            return True
        except Exception as e:
            print(f"[homepage] ✗ Movie insert error: {e}")
            return False


def get_max_page(html: str) -> int:
    """Extract the max page number from the pagination block."""
    soup = BeautifulSoup(html, "html.parser")
    pages = soup.select("ul.page-numbers a.page-numbers")
    nums = []
    for a in pages:
        try:
            nums.append(int(a.get_text(strip=True)))
        except ValueError:
            pass
    return max(nums) if nums else 1


def _fetch_page_html(url: str) -> str:
    """Fetch a page via FlareSolverr first, then direct requests."""
    html = get_page_with_flaresolverr(url)
    if html:
        return html
    try:
        import requests as _r
        resp = _r.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        return resp.text
    except Exception as e:
        print(f"[homepage] Fetch failed: {e}")
        return ""


def _process_cards(cards: list[dict], seen: dict, stats: dict, save_to_db: bool):
    """Process a list of cards: resolve TMDB, upload to Doodstream via jibi_bot, save to DB."""
    new_count = 0
    
    # Needs API key from environment for jibi_bot
    api_key = os.environ.get("DOODSTREAM_API_KEY", "")

    for card in cards:
        url_key = card["url"]
        
        # Check if URL was seen in local state
        if url_key in seen:
            stats["skipped"] += 1
            continue

        # Fast Supabase duplications pre-check before wasting time on jibi_bot
        if supabase:
            try:
                card["type"] = "episode" if card["is_episode"] else "movie"
                if already_in_supabase(url_key, card):
                    print(f"[homepage] ↻ Already in Supabase, skipping: {card['title']}")
                    seen[url_key] = time.strftime("%Y-%m-%d %H:%M:%S")
                    stats["skipped"] += 1
                    continue
            except Exception as e:
                pass

        seen[url_key] = time.strftime("%Y-%m-%d %H:%M:%S")
        new_count += 1
        
        item_type = "Episode" if card["is_episode"] else "Movie"
        print(f"[homepage] ⏳ Processing {item_type}: {card['title']}")
        
        try:
            # 1. RUN JIBI_BOT TO UPLOAD TO DOODSTREAM
            result = run_jibi_bot(url_key, api_key=api_key)
            
            if result.get("skipped_duplicate"):
                stats["skipped"] += 1
                print(f"[homepage] ↻ Duplicate (already uploaded): filecode={result.get('filecode')}")
                continue
                
            if not result.get("success"):
                stats["errors"].append(f"Upload failed: {url_key}")
                print(f"[homepage] ✗ Failed: {result.get('errors', [])}")
                continue
                
            filecode = result.get("filecode")
            tmdb_id = result.get("tmdb_id")
            
            if card["is_episode"]:
                stats["new_episodes"] += 1
            else:
                stats["new_movies"] += 1
                
            print(f"[homepage] ✓ Success: filecode={filecode}, tmdb={tmdb_id}")

            # 2. SAVE TO SUPABASE (Fallback to dummy ID if TMDB fails)
            if save_to_db and filecode:
                doodstream_url = f"https://doodstream.com/e/{filecode}"
                download_url = f"https://playmogo.com/d/{filecode}"
                
                if card["is_episode"]:
                    import zlib
                    s_title = card.get("series_name") or card["title"]
                    tid = tmdb_id or (9000000 + (zlib.crc32(s_title.encode()) % 1000000))
                    season = card.get("season") or 1
                    episode = card.get("episode") or 1
                    
                    data = {
                        "tmdb_id": tid,
                        "series_title": s_title,
                        "season_number": season,
                        "episode_number": episode,
                        "title": card["title"],
                        "filecode": filecode,
                        "doodstream_url": doodstream_url,
                        "doodstream_download_url": download_url
                    }
                    try:
                        existing = supabase.table("tv_episodes").select("id").eq("tmdb_id", tid).eq("season_number", season).eq("episode_number", episode).execute()
                        if not existing.data:
                            supabase.table("tv_episodes").insert(data).execute()
                            print(f"[homepage] ✅ TV saved to DB: {s_title} S{season}E{episode}")
                        else:
                            supabase.table("tv_episodes").update(data).eq("tmdb_id", tid).eq("season_number", season).eq("episode_number", episode).execute()
                            print(f"[homepage] 🔄 TV updated in DB: {s_title} S{season}E{episode}")
                        stats["saved"] += 1
                    except Exception as e:
                        print(f"[homepage] ✗ TV DB error: {e}")
                else:
                    import zlib
                    tid = tmdb_id or (8000000 + (zlib.crc32(card["title"].encode()) % 1000000))
                    data = {
                        "tmdb_id": tid,
                        "title": card["title"],
                        "doodstream_url": doodstream_url,
                        "doodstream_download_url": download_url,
                        "media_type": "movie"
                    }
                    try:
                        existing = supabase.table("movies").select("id").eq("tmdb_id", tid).execute()
                        if not existing.data:
                            supabase.table("movies").insert(data).execute()
                            print(f"[homepage] ✅ Movie saved to DB: {card['title']}")
                        else:
                            supabase.table("movies").update(data).eq("tmdb_id", tid).execute()
                            print(f"[homepage] 🔄 Movie updated in DB: {card['title']}")
                        stats["saved"] += 1
                    except Exception as e:
                        print(f"[homepage] ✗ Movie DB error: {e}")

        except Exception as e:
            print(f"[homepage] Error: {url_key[:60]}: {e}")
            stats["errors"].append(str(e))
            
        time.sleep(2)

    return new_count


def scrape_homepage(
    homepage_url: str = "https://cimafu.cam",
    check_new_pages: int = 3,
    save_to_db: bool = True,
) -> dict:
    """
    Scrape cimafu.cam homepage in two phases:

    Phase 1 — CHECK NEW (pages 1..check_new_pages):
        Quickly scan the first few pages for newly added content.
        Any new card gets saved to DB. Already-seen cards are skipped.

    Phase 2 — CONTINUE (from last_page onward):
        Resume from the last page we reached in a previous run,
        and keep going through ALL remaining pages until the end.
        This ensures we eventually scrape the entire site.

    State is saved to homepage_scraper_state.json between runs.
    """
    state = load_homepage_state()
    seen = state.setdefault("seen_urls", {})
    last_page = state.get("last_page", 0)  # 0 = never ran before

    stats = {
        "pages_checked": 0,
        "new_movies": 0,
        "new_episodes": 0,
        "skipped": 0,
        "saved": 0,
        "errors": [],
    }

    max_page = None  # will be detected from pagination

    # ── Phase 1: Check pages 1-3 for new content ──────────────────────────
    print(f"\n{'='*60}")
    print(f"PHASE 1: Checking pages 1-{check_new_pages} for new content")
    print(f"{'='*60}")

    for page_num in range(1, check_new_pages + 1):
        url = homepage_url if page_num == 1 else f"{homepage_url.rstrip('/')}/page/{page_num}/"
        print(f"\n[homepage] ─── Page {page_num} ─── ({url})")

        html = _fetch_page_html(url)
        if not html:
            stats["errors"].append(f"Page {page_num} fetch failed")
            break

        # Detect max page from pagination on first page
        if max_page is None:
            max_page = get_max_page(html)
            print(f"[homepage] Total pages on site: {max_page}")

        cards = extract_homepage_cards(html)
        if not cards:
            print(f"[homepage] No cards on page {page_num}, stopping phase 1.")
            break

        stats["pages_checked"] += 1
        new_count = _process_cards(cards, seen, stats, save_to_db)

        if new_count > 0:
            print(f"[homepage] ✓ {new_count} new item(s) on page {page_num}")
        else:
            print(f"[homepage] No new items on page {page_num}")

        save_homepage_state(state)
        time.sleep(0.5)

    # ── Phase 2: Continue from last_page ──────────────────────────────────
    resume_page = last_page + 1 if last_page > 0 else check_new_pages + 1
    if max_page is None:
        max_page = 336  # fallback

    # Don't re-check pages we already did in phase 1
    if resume_page <= check_new_pages:
        resume_page = check_new_pages + 1

    print(f"\n{'='*60}")
    print(f"PHASE 2: Continuing from page {resume_page} (last_page was {last_page})")
    print(f"{'='*60}")

    page_num = resume_page
    while page_num <= max_page:
        url = f"{homepage_url.rstrip('/')}/page/{page_num}/"
        print(f"\n[homepage] ─── Page {page_num}/{max_page} ─── ({url})")

        html = _fetch_page_html(url)
        if not html:
            stats["errors"].append(f"Page {page_num} fetch failed")
            # Save progress so far
            state["last_page"] = page_num - 1
            save_homepage_state(state)
            break

        cards = extract_homepage_cards(html)
        if not cards:
            print(f"[homepage] No cards on page {page_num}, reached the end.")
            state["last_page"] = page_num
            save_homepage_state(state)
            break

        stats["pages_checked"] += 1
        new_count = _process_cards(cards, seen, stats, save_to_db)
        print(f"[homepage] Page {page_num}: {new_count} new, {len(cards) - new_count} skipped")

        # Save progress after each page
        state["last_page"] = page_num
        save_homepage_state(state)

        page_num += 1
        time.sleep(0.5)

    # ── Summary ───────────────────────────────────────────────────────────
    save_homepage_state(state)

    print(f"\n{'='*60}")
    print(f"HOMEPAGE SCRAPE SUMMARY")
    print(f"{'='*60}")
    print(f"Pages checked     : {stats['pages_checked']}")
    print(f"New movies        : {stats['new_movies']}")
    print(f"New TV episodes   : {stats['new_episodes']}")
    print(f"Skipped (seen)    : {stats['skipped']}")
    print(f"Saved to DB       : {stats['saved']}")
    print(f"Errors            : {len(stats['errors'])}")
    print(f"Last page reached : {state.get('last_page', 0)}")
    print(f"Total URLs seen   : {len(seen)}")
    print(f"{'='*60}")
    return stats


if __name__ == "__main__":
    import sys as _sys
    if "--homepage" in _sys.argv:
        # Homepage scraper mode:
        #   python3 cima4u_crawler.py --homepage [--pages 3] [--dry-run]
        import argparse as _ap
        _p = _ap.ArgumentParser()
        _p.add_argument("--homepage", action="store_true")
        _p.add_argument("--pages", type=int, default=3,
                        help="Number of pages to check for new content (default 3)")
        _p.add_argument("--dry-run", action="store_true",
                        help="Don't save to DB")
        _args, _ = _p.parse_known_args()
        scrape_homepage(
            check_new_pages=_args.pages,
            save_to_db=not _args.dry_run,
        )
    else:
        main()

