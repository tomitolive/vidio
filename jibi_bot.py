#!/usr/bin/env python3
"""
Jibi Bot - Specialized Stream Finder & Downloader for Cimafu / HTML5 Server Lists
Usage:
    python jibi_bot.py <URL> [--api-key <DOODSTREAM_KEY>] [--download]
    PAGE_URL=<url> python jibi_bot.py
"""

import os
import sys
import json
import time
import re
import random
import argparse
from urllib.parse import urlparse, urljoin, quote

import requests
from bs4 import BeautifulSoup

from catalog import get_entry_by_page, update_doodstream_in_catalog, find_tmdb_id_by_title, import_from_doodstream_account, save_to_supabase, search_tmdb_api, extract_cima4u_info, search_tmdb_by_slug, extract_episode_info

# ─── 1. Proxies Configuration ───────────────────────────────────────────────

PROXY_LISTS = {
    "http": "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/http.txt",
    "https": "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/https.txt",
    "socks5": "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/socks5.txt",
}

# Max proxies to download & test from each list (keep it fast)
MAX_PROXIES_PER_SOURCE = 60
PROXY_TEST_TIMEOUT = 3
PROXY_TEST_WORKERS = 15

PROXIES_LIST = []
PROXIES_LOADED = False

# Proxy rotation state
proxy_index = 0
failed_proxies = set()

# Import inside functions to avoid circular import issues
import concurrent.futures


def _fetch_proxy_list(url: str, timeout: int = 20) -> list:
    """Download a proxy list from a raw GitHub URL."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        proxies = [
            line.strip()
            for line in resp.text.splitlines()
            if line.strip() and ":" in line
        ]
        return proxies[:MAX_PROXIES_PER_SOURCE]
    except Exception as e:
        print(f"[proxy] Failed to fetch proxy list from {url}: {e}")
        return []


def load_proxies():
    """Fetch fresh free proxies, test them in parallel, keep only working ones."""
    global PROXIES_LIST, PROXIES_LOADED, proxy_index
    if PROXIES_LOADED:
        return

    all_candidates = []
    # HTTP first (largest pool, most working), then HTTPS, then SOCKS5
    for kind in ("http", "https", "socks5"):
        scheme = "socks5" if kind == "socks5" else "http"
        for raw in _fetch_proxy_list(PROXY_LISTS[kind]):
            proxy = raw.strip().lstrip("socks5h://socks4://").strip()
            if not proxy:
                continue
            all_candidates.append(scheme + "://" + proxy)

    # Deduplicate keeping order
    seen = set()
    unique = []
    for p in all_candidates:
        if p in seen:
            continue
        seen.add(p)
        unique.append(p)

    if not unique:
        print(f"[proxy] No proxy candidates found, will connect directly")
        PROXIES_LIST = []
        PROXIES_LOADED = True
        proxy_index = 0
        return

    # Test all candidates in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=PROXY_TEST_WORKERS) as ex:
        results = list(ex.map(_test_proxy, unique))

    tested = [p for p, ok in zip(unique, results) if ok]

    if tested:
        PROXIES_LIST = tested
        print(f"[proxy] Loaded {len(tested)} working proxies")
    else:
        PROXIES_LIST = []
        print(f"[proxy] No working proxies found, will connect directly")

    PROXIES_LOADED = True
    proxy_index = 0


def _test_proxy(proxy: str, timeout: int = PROXY_TEST_TIMEOUT) -> bool:
    """Return True if the proxy can make an HTTPS CONNECT tunnel (what Playwright needs).
    Tests against an HTTPS endpoint so dead/HTTP-only proxies are excluded."""
    proxies = get_requests_proxies(proxy) if proxy else None
    if not proxies:
        return False
    try:
        r = requests.get("https://api.iplocate.io/ip", proxies=proxies, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def get_next_proxy() -> str:
    """Return next working proxy, loading fresh list if not yet loaded.
    Returns empty string (direct connection) if none available."""
    global proxy_index, PROXIES_LOADED

    # Load tested proxies on first use
    if not PROXIES_LOADED:
        load_proxies()

    env_proxy = os.environ.get("PROXY_URL", "").strip()
    if env_proxy:
        return env_proxy

    if not PROXIES_LIST:
        return ""

    # Try to find a working proxy
    attempts = 0
    max_attempts = len(PROXIES_LIST)

    while attempts < max_attempts:
        proxy = PROXIES_LIST[proxy_index % len(PROXIES_LIST)]
        proxy_index += 1

        if proxy not in failed_proxies:
            return proxy

        attempts += 1

    # All proxies failed, reset and try again
    failed_proxies.clear()
    proxy_index = 0
    return ""


def mark_proxy_failed(proxy_url: str):
    """Mark a proxy as failed to skip it in future requests."""
    if proxy_url:
        failed_proxies.add(proxy_url)
        print(f"[proxy] Marked proxy as failed: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")


def reset_failed_proxies():
    """Reset failed proxies list (call periodically to retry them)."""
    global failed_proxies
    if failed_proxies:
        print(f"[proxy] Resetting {len(failed_proxies)} failed proxies")
        failed_proxies.clear()


def get_random_proxy() -> str:
    """Return a proxy string from environment or rotated list (legacy function)."""
    return get_next_proxy()


def get_all_proxies() -> list:
    """Return the full list of loaded working proxies (loading on first use)."""
    global PROXIES_LOADED
    if not PROXIES_LOADED:
        load_proxies()
    return list(PROXIES_LIST)


def get_requests_proxies(proxy_url: str):
    return {"http": proxy_url, "https": proxy_url} if proxy_url else None


def parse_playwright_proxy(proxy_url: str):
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if parsed.username and parsed.password:
        return {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            "username": parsed.username,
            "password": parsed.password,
        }
    return {"server": proxy_url}


# ─── 2. Utility Functions ───────────────────────────────────────────────────

def is_video_url(url: str, content_type: str = "") -> bool:
    """Check if the given URL points directly to a video resource."""
    video_exts = (".m3u8", ".mp4", ".webm", ".ts", ".mkv", ".avi")
    video_ct = (
        "video/",
        "application/x-mpegurl",
        "application/vnd.apple.mpegurl",
        "audio/mpegurl",
    )
    url_lower = url.lower().split("?")[0]
    if any(url_lower.endswith(ext) for ext in video_exts):
        return True
    if content_type and any(ct in content_type.lower() for ct in video_ct):
        return True
    return False


def clean_url(url: str) -> str:
    url = url.strip()
    # Fix common typos (htpps, htpps, htttp, etc.)
    url = re.sub(r"^ht+p+s?://", "https://", url, flags=re.IGNORECASE)
    # Strip duplicate protocols (e.g. https://https:// -> https://)
    url = re.sub(r"^(https?:/*)+", "https://", url, flags=re.IGNORECASE)
    if url.startswith("://"):
        url = "https" + url
    elif not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url


DOODSTREAM_HOSTS = (
    "doodstream.com", "dood.", "d0o0d.", "ds2play.", "ds2video.",
    "dood.wf", "dood.so", "dood.cx", "dood.la",
    "dood.re", "dood.yt", "dood.to", "dood.watch", "dood.pm",
)

# Blacklist: servers known to fail or be unsupported
BLACKLISTED_SERVERS = (
    "luluvdo.com", "lulustream.com", "earnvids.xyz",
    "streamwish.fun", "miixdrop.top", "miixdrop.co",
)

# Session-level: track servers that failed during this run
_failed_servers_session = set()


def is_doodstream_embed(url: str) -> bool:
    """Return True if URL is a DoodStream-family embed page."""
    lower = url.lower()
    return any(host in lower for host in DOODSTREAM_HOSTS)


def extract_doodstream_filecode(url: str) -> str | None:
    """Extract DoodStream filecode from embed / download URLs."""
    patterns = (
        r"/(?:e|f|d|embed)/([a-zA-Z0-9]+)",
        r"/([a-zA-Z0-9]{8,})(?:[/?#]|$)",
    )
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            return match.group(1)
    return None


PROCESSED_DB_FILE = "processed_movies.json"


def normalize_page_key(url: str) -> str:
    """Normalize page URL for duplicate detection."""
    url = clean_url(url).rstrip("/").lower()
    return url


def load_processed_db() -> dict:
    """Load local DB of already-uploaded movies."""
    if os.path.exists(PROCESSED_DB_FILE):
        try:
            with open(PROCESSED_DB_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_processed_db(db: dict):
    """Persist processed movies DB."""
    with open(PROCESSED_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def extract_movie_title_from_page(page_url: str, proxy_url: str = "") -> str:
    """Extract movie title from watch page (og:title or <title>)."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    try:
        proxies = get_requests_proxies(proxy_url)
        resp = requests.get(clean_url(page_url), headers=headers, proxies=proxies, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"].strip()
        title_tag = soup.find("title")
        if title_tag and title_tag.text:
            return title_tag.text.split("|")[0].strip()
    except Exception as e:
        print(f"[dedup] Could not extract page title: {e}")
    return ""


def extract_title_keywords(title: str) -> list[str]:
    """Extract meaningful keywords from a movie title for fuzzy matching."""
    if not title:
        return []
    # Remove common Arabic/English filler words
    stopwords = {
        "مشاهدة", "فيلم", "وتحميل", "مترجم", "مباشر", "watch", "download",
        "movie", "film", "online", "free", "hd", "full",
    }
    # Keep alphanumeric tokens and years
    tokens = re.findall(r"[a-z0-9\u0600-\u06ff]+", title.lower())
    keywords = [t for t in tokens if t not in stopwords and len(t) > 2]
    # Always keep 4-digit years
    years = re.findall(r"\b((?:19|20)\d{2})\b", title)
    keywords.extend(years)
    return list(dict.fromkeys(keywords))  # dedupe, preserve order


def list_doodstream_files(api_key: str, proxy_url: str = "") -> list[dict]:
    """Fetch all files from DoodStream account.
    DoodStream API is reliable directly, so we never route it through the
    (unreliable free) proxy — only cimafu scraping needs the proxy."""
    if not api_key:
        return []
    files = []
    page = 1
    proxies = None  # doodapi.com is reliable direct; free proxies cause 502s
    while True:
        try:
            resp = requests.get(
                f"https://doodapi.com/api/file/list?key={api_key}&page={page}&per_page=100",
                proxies=proxies,
                timeout=20,
            )
            data = resp.json()
            if data.get("status") != 200:
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
            print(f"[dedup] Error listing files: {e}")
            break
    return files


def find_existing_upload(
    api_key: str,
    page_url: str,
    movie_title: str = "",
    source_filecode: str = "",
    proxy_url: str = "",
) -> dict | None:
    """
    Check if this movie was already uploaded.
    Returns existing file info dict or None.
    """
    page_key = normalize_page_key(page_url)
    db = load_processed_db()

    # 1. Check local processed DB by page URL
    if page_key in db:
        entry = db[page_key]
        print(f"[dedup] Already processed (local DB): {entry.get('title', page_key)}")
        print(f"[dedup] Existing filecode: {entry.get('filecode')}")
        return entry

    # 2. Check local DB by source filecode
    if source_filecode:
        for entry in db.values():
            if entry.get("source_filecode") == source_filecode:
                print(f"[dedup] Source filecode already cloned: {source_filecode}")
                print(f"[dedup] Existing filecode: {entry.get('filecode')}")
                return entry

    # 3. Check DoodStream account by filecode (most accurate)
    if api_key and source_filecode:
        account_files = list_doodstream_files(api_key, proxy_url)
        for f in account_files:
            file_code = f.get("file_code") or f.get("filecode")
            if file_code == source_filecode:
                print(f"[dedup] Source filecode already exists in account: {source_filecode}")
                return {
                    "filecode": file_code,
                    "title": f.get("title"),
                    "source": "account_filecode_match",
                    "skipped": True,
                }

    # 4. Check DoodStream account by title keywords (fallback)
    if api_key and movie_title:
        keywords = extract_title_keywords(movie_title)
        if len(keywords) >= 2:
            account_files = list_doodstream_files(api_key, proxy_url)
            for f in account_files:
                file_title = (f.get("title") or "").lower()
                file_code = f.get("file_code") or f.get("filecode")
                if not file_code:
                    continue
                # Match if most keywords appear in existing file title
                matches = sum(1 for kw in keywords if kw in file_title)
                if matches >= max(2, len(keywords) - 1):
                    print(f"[dedup] Found existing file on account by title: '{f.get('title')}'")
                    print(f"[dedup] Existing filecode: {file_code}")
                    return {
                        "filecode": file_code,
                        "title": f.get("title"),
                        "source": "account_title_match",
                        "skipped": True,
                    }

    return None


def mark_as_processed(
    page_url: str,
    filecode: str,
    title: str = "",
    source_filecode: str = "",
    embed_url: str = "",
):
    """Record a successful upload in local DB."""
    db = load_processed_db()
    page_key = normalize_page_key(page_url)
    db[page_key] = {
        "filecode": filecode,
        "title": title,
        "source_filecode": source_filecode,
        "embed_url": embed_url,
        "page_url": page_url,
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_processed_db(db)
    print(f"[dedup] Saved to {PROCESSED_DB_FILE}: {title or page_key}")


# ─── 3. Server List Parser ──────────────────────────────────────────────────

def extract_servers_from_page(page_url: str, proxy_url: str) -> list[dict]:
    """
    Parses the main HTML page looking for <ul class="server_list"> or iframe elements.
    Returns list of dicts: [{"name": "سيرفر 1", "embed_url": "..."}, ...]
    Attempts proxy rotation if initial proxy fails.
    For Cima4u URLs, uses Playwright for better JavaScript rendering.
    """
    page_url = clean_url(page_url)
    print(f"[jibi] Scraping servers from: {page_url}")

    # Use Playwright for Cima4u URLs (they likely need JavaScript)
    if "cimafu.cam" in page_url.lower():
        return extract_servers_with_playwright(page_url, proxy_url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    # Try requested proxy first, then fallback to random proxies if needed
    proxies_to_try = [proxy_url]
    if PROXIES_LIST:
        proxies_to_try += random.sample(PROXIES_LIST, k=min(3, len(PROXIES_LIST)))
    resp = None

    for p_url in proxies_to_try:
        proxies = get_requests_proxies(p_url)
        try:
            resp = requests.get(page_url, headers=headers, proxies=proxies, timeout=15)
            resp.raise_for_status()
            break
        except Exception as e:
            print(f"[jibi] HTTP request error with proxy {p_url.split('@')[-1] if '@' in p_url else p_url}: {e}")
            continue

    if not resp or resp.status_code != 200:
        print(f"[jibi] Failed to fetch page content from {page_url}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    servers = []

    # 1. Parse <ul class="server_list">
    server_ul = soup.find("ul", class_="server_list")
    if server_ul:
        for li in server_ul.find_all("li"):
            embed_url = li.get("data-server") or li.get("data-link")
            if not embed_url:
                # Check for nested iframe or noscript iframe
                iframe = li.find("iframe")
                if iframe and iframe.get("src"):
                    embed_url = iframe["src"]

            if embed_url:
                server_name = li.text.strip() or f"Server {len(servers)+1}"
                servers.append({
                    "name": server_name,
                    "embed_url": clean_url(embed_url)
                })

    # 2. Fallback: Parse top-level iframes
    if not servers:
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src")
            if src and src.strip() and src != "about:blank":
                servers.append({
                    "name": f"Iframe {len(servers)+1}",
                    "embed_url": clean_url(src)
                })

    # Deduplicate by embed_url
    seen = set()
    unique_servers = []
    for s in servers:
        if s["embed_url"] not in seen:
            seen.add(s["embed_url"])
            unique_servers.append(s)

    print(f"[jibi] Total servers found: {len(unique_servers)}")
    for i, s in enumerate(unique_servers, 1):
        print(f"  [{i}] {s['name']} -> {s['embed_url']}")

    return unique_servers


def extract_servers_with_playwright(page_url: str, proxy_url: str = "") -> list[dict]:
    """Extract servers using Playwright for pages that need JavaScript rendering.
    Tries the passed proxy, then other live proxies, then direct connection."""
    from playwright.sync_api import sync_playwright

    print(f"[jibi-playwright] Using Playwright for: {page_url}")

    # Ordered proxy candidates: supplied one, other live proxies, then direct (None)
    proxies_to_try = []
    if proxy_url:
        proxies_to_try.append(proxy_url)
    for p in get_all_proxies():
        if p not in proxies_to_try and len(proxies_to_try) < 4:
            proxies_to_try.append(p)
    proxies_to_try.append(None)  # direct connection as last resort

    with sync_playwright() as p:
        for attempt_idx, attempt_proxy in enumerate(proxies_to_try):
            if attempt_proxy:
                masked = attempt_proxy.split('@')[-1] if '@' in attempt_proxy else attempt_proxy
                print(f"[jibi-playwright] Attempt {attempt_idx+1} via proxy {masked}")
            else:
                print(f"[jibi-playwright] Attempt {attempt_idx+1} direct (no proxy)")

            launch_kwargs = {
                "headless": True,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--window-size=1920,1080",
                ],
            }

            pw_proxy = parse_playwright_proxy(attempt_proxy) if attempt_proxy else None
            if pw_proxy:
                launch_kwargs["proxy"] = pw_proxy

            servers = []
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
                    page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(5)  # Wait for JavaScript to load

                    # Try to interact with page to load servers
                    try:
                        # Try clicking on server links or watch buttons
                        page.click('.serversWatchSide a, .watch-btn, .play-button', timeout=5000)
                        time.sleep(3)
                    except:
                        pass

                    # Wait for server elements to appear
                    try:
                        page.wait_for_selector('.serversWatchSide a, iframe, .server_list li', timeout=10000)
                        time.sleep(2)
                    except:
                        pass

                    html = page.content()

                    soup = BeautifulSoup(html, "html.parser")

                    # Parse <ul class="server_list">
                    server_ul = soup.find("ul", class_="server_list")
                    if server_ul:
                        for li in server_ul.find_all("li"):
                            embed_url = li.get("data-server") or li.get("data-link")
                            if not embed_url:
                                iframe = li.find("iframe")
                                if iframe and iframe.get("src"):
                                    embed_url = iframe["src"]

                            if embed_url:
                                server_name = li.text.strip() or f"Server {len(servers)+1}"
                                servers.append({
                                    "name": server_name,
                                    "embed_url": clean_url(embed_url)
                                })

                    # Parse Cima4u specific server structure (.serversWatchSide a)
                    if not servers:
                        server_links = soup.select(".serversWatchSide a")
                        for link in server_links:
                            href = link.get("href")
                            data_link = link.get("data-link")
                            if href or data_link:
                                embed_url = data_link or href
                                server_name = link.text.strip() or f"Server {len(servers)+1}"
                                servers.append({
                                    "name": server_name,
                                    "embed_url": clean_url(embed_url)
                                })

                    # Fallback: Parse top-level iframes
                    if not servers:
                        for iframe in soup.find_all("iframe"):
                            src = iframe.get("src")
                            if src and src.strip() and src != "about:blank":
                                servers.append({
                                    "name": f"Iframe {len(servers)+1}",
                                    "embed_url": clean_url(src)
                                })
                except Exception as e:
                    print(f"[jibi-playwright] Error: {str(e)[:140]}")
                finally:
                    browser.close()
            except Exception as e:
                print(f"[jibi-playwright] Launch failed: {str(e)[:120]}")
                continue

            # If we got servers on this attempt, stop
            if servers:
                break

    # Deduplicate by embed_url
    seen = set()
    unique_servers = []
    for s in servers:
        if s["embed_url"] not in seen:
            seen.add(s["embed_url"])
            unique_servers.append(s)

    print(f"[jibi-playwright] Total servers found: {len(unique_servers)}")
    for i, s in enumerate(unique_servers, 1):
        print(f"  [{i}] {s['name']} -> {s['embed_url']}")

    return unique_servers


# ─── 4. Playwright Embed Resolver & Anti-Popunder ───────────────────────────

def resolve_stream_from_embed(embed_url: str, proxy_url: str, referer: str = "") -> str | None:
    """
    Opens embed_url in Playwright with popup auto-close, anti-bot flags,
    and captures underlying m3u8/mp4 video streams.
    """
    from playwright.sync_api import sync_playwright

    print(f"[jibi-browser] Resolving embed: {embed_url}")
    found_streams = []

    with sync_playwright() as p:
        launch_kwargs = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080",
                "--disable-popup-blocking=false",
            ],
        }

        pw_proxy = parse_playwright_proxy(proxy_url)
        if pw_proxy:
            launch_kwargs["proxy"] = pw_proxy

        browser = p.chromium.launch(**launch_kwargs)

        context_kwargs = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }

        ctx = browser.new_context(**context_kwargs)
        page = ctx.new_page()

        # ANTI-POPUNDER Guard: Auto-close any newly opened popups / tabs
        def handle_popup(popup):
            print(f"  [popunder-block] Closed popup window: {popup.url[:80]}")
            try:
                popup.close()
            except Exception:
                pass

        page.on("popup", handle_popup)
        ctx.on("page", lambda new_p: new_p.on("popup", handle_popup))

        # Network Interceptor
        def on_response(resp):
            try:
                url = resp.url
                ct = resp.headers.get("content-type", "")
                if is_video_url(url, ct) and url not in found_streams:
                    print(f"  [captured stream] -> {url[:120]}")
                    found_streams.append(url)
            except Exception:
                pass

        page.on("response", on_response)

        # Set Headers
        if referer:
            page.set_extra_http_headers({"Referer": referer})

        try:
            page.goto(embed_url, wait_until="domcontentloaded", timeout=25000)
        except Exception as e:
            print(f"  [warn] Page load timeout/warn: {e}")

        time.sleep(4)

        # Attempt Play Button Clicks
        play_selectors = [
            ".play-btn", ".play_btn", ".btn-play", "#play-btn", "#btnPlay",
            "button[class*='play']", "div[class*='play']", ".jw-icon-display",
            ".vjs-big-play-button", ".plyr__control--overlaid", ".fa-play",
            "[aria-label='Play']", ".playBtn", ".play-button",
        ]
        for sel in play_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    print(f"  [click] Clicked player button: {sel}")
                    break
            except Exception:
                pass

        time.sleep(5)

        # Scrape HTML directly for embedded m3u8/mp4 URLs
        try:
            html = page.content()
            patterns = [
                r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
                r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
                r'"file"\s*:\s*"(https?://[^"]+)"',
            ]
            for pat in patterns:
                for m in re.findall(pat, html):
                    m = clean_url(m.replace("\\/", "/"))
                    if is_video_url(m) and m not in found_streams:
                        print(f"  [html regex match] -> {m[:120]}")
                        found_streams.append(m)
        except Exception:
            pass

        browser.close()

    return found_streams[0] if found_streams else None


# ─── 5. Downloaders & Uploaders ─────────────────────────────────────────────

def try_yt_dlp_extract(url: str, proxy_url: str = "") -> dict:
    """Use yt-dlp to extract stream info from a page or embed URL."""
    try:
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        if proxy_url:
            ydl_opts["proxy"] = proxy_url

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return {"success": False, "error": "No info"}

            stream_url = info.get("url")
            fmts = info.get("formats", [])
            if fmts:
                best = max(
                    (f for f in fmts if f.get("url")),
                    key=lambda f: (f.get("height") or 0),
                    default=None,
                )
                if best:
                    stream_url = best["url"]

            return {
                "success": True,
                "stream_url": stream_url or url,
                "title": info.get("title", "Video"),
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


def verify_doodstream_file(
    api_key: str,
    filecode: str,
    proxy_url: str = "",
    max_wait: int = 45,
    interval: int = 5,
) -> dict:
    """Poll DoodStream until file actually exists and is playable."""
    if not filecode:
        return {"success": False, "error": "No filecode to verify"}

    proxies = None  # doodapi.com direct
    deadline = time.time() + max_wait
    print(f"[verify] Checking filecode {filecode} on account...")

    while time.time() < deadline:
        try:
            resp = requests.get(
                f"https://doodapi.com/api/file/info?key={api_key}&file_code={filecode}",
                proxies=proxies,
                timeout=15,
            )
            data = resp.json()
            if data.get("status") == 200:
                info = (data.get("result") or [{}])[0]
                if info.get("filecode") and str(info.get("canplay")) == "1":
                    print(f"[verify] File confirmed on account: {info.get('title')}")
                    return {"success": True, "info": info}
                if info.get("status") == "Not found or not your file":
                    print(f"[verify] File not ready yet, waiting...")
        except Exception as e:
            print(f"[verify] Check error: {e}")
        time.sleep(interval)

    return {"success": False, "error": "File not found on DoodStream account after upload"}


def is_remote_uploadable(url: str) -> bool:
    """DoodStream remote upload works best with direct mp4 URLs, not HLS/m3u8."""
    lower = url.lower()
    if ".m3u8" in lower:
        return False
    if lower.endswith((".mp4", ".mkv", ".avi", ".webm", ".mov")):
        return True
    # Allow other direct URLs but warn on streaming manifests
    if "master.m3u8" in lower or "/hls" in lower:
        return False
    return True


def try_doodstream_clone(embed_url: str, api_key: str, proxy_url: str = "") -> dict:
    """Clone an existing DoodStream file to account using filecode."""
    if not api_key:
        return {"success": False, "error": "No DoodStream API key"}

    file_code = extract_doodstream_filecode(embed_url)
    if not file_code:
        return {"success": False, "error": "Could not parse DoodStream filecode"}
    print(f"[doodstream] Triggering direct file clone for filecode: {file_code}")
    try:
        proxies = None  # doodapi.com direct
        resp = requests.get(
            f"https://doodapi.com/api/file/clone?key={api_key}&file_code={file_code}",
            proxies=proxies,
            timeout=25,
        )
        data = resp.json()
        if data.get("status") == 200:
            new_filecode = data.get("result", {}).get("filecode")
            verify = verify_doodstream_file(api_key, new_filecode, "", max_wait=20)
            if verify["success"]:
                print(f"[doodstream] Clone verified! Filecode: {new_filecode}")
                return {"success": True, "filecode": new_filecode}
            return {"success": False, "error": "Clone returned filecode but file not on account"}
        else:
            return {"success": False, "error": data.get("msg", "Clone API error")}
    except Exception as e:
        print(f"[doodstream] Clone error: {e}")
        return {"success": False, "error": str(e)}


def try_doodstream_upload(stream_url: str, api_key: str, proxy_url: str = "", title: str = "Video") -> dict:
    """Remote upload stream_url to DoodStream."""
    if not api_key:
        return {"success": False, "error": "No DoodStream API key"}

    print(f"[doodstream] Triggering remote upload for: {stream_url[:80]}")
    try:
        proxies = None  # doodapi.com direct
        resp = requests.get(
            f"https://doodapi.com/api/upload/url?key={api_key}&url={quote(stream_url, safe='')}",
            proxies=proxies,
            timeout=30,
        )
        data = resp.json()
        if data.get("status") == 200:
            filecode = data.get("result", {}).get("filecode")
            print(f"[doodstream] Remote upload queued, filecode: {filecode}")

            if filecode and title:
                requests.get(
                    f"https://doodapi.com/api/file/rename?key={api_key}&file_code={filecode}&title={title}",
                    proxies=proxies,
                    timeout=15,
                )

            verify = verify_doodstream_file(api_key, filecode, "")
            if verify["success"]:
                print(f"[doodstream] Remote upload verified! Filecode: {filecode}")
                return {"success": True, "filecode": filecode}
            return {
                "success": False,
                "error": "Upload API accepted but file never appeared on account (m3u8/HLS often fails)",
            }
        else:
            return {"success": False, "error": data.get("msg", "Unknown API error")}
    except Exception as e:
        print(f"[doodstream] Error: {e}")
        return {"success": False, "error": str(e)}


def download_locally(stream_url: str, proxy_url: str = "", output_dir: str = ".", referer: str = ""):
    """Download video locally using yt-dlp."""
    print(f"[download] Downloading stream locally: {stream_url[:80]}")
    try:
        import yt_dlp
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
            "merge_output_format": "mp4",
        }
        if proxy_url:
            ydl_opts["proxy"] = proxy_url

        if referer:
            ydl_opts["http_headers"] = {
                "Referer": referer,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([stream_url])
        print("[download] Completed successfully!")
        return True
    except Exception as e:
        print(f"[download] Failed: {e}")
        return False


# ─── 6. Main Orchestrator ───────────────────────────────────────────────────

def run_jibi_bot(page_url: str, api_key: str = "", download: bool = False, tmdb_id: int = None):
    page_url = clean_url(page_url)
    proxy_url = get_next_proxy()
    if proxy_url:
        masked = proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url
        print(f"[jibi] Using proxy: {masked}")

    result = {
        "page_url": page_url,
        "success": False,
        "selected_server": None,
        "direct_stream_url": None,
        "filecode": None,
        "servers_found": [],
        "errors": [],
        "skipped_duplicate": False,
    }

    # Step 0: Extract movie title and check for duplicates BEFORE any upload
    movie_title = extract_movie_title_from_page(page_url, proxy_url)
    if movie_title:
        print(f"[jibi] Movie title: {movie_title}")

    existing = find_existing_upload(api_key, page_url, movie_title, proxy_url=proxy_url)
    if existing and existing.get("filecode"):
        result["success"] = True
        result["filecode"] = existing["filecode"]
        result["skipped_duplicate"] = True
        result["movie_title"] = existing.get("title") or movie_title
        print(f"[jibi] Skipping upload — movie already exists! filecode={existing['filecode']}")
        # Remember this page so we skip faster next time
        mark_as_processed(
            page_url=page_url,
            filecode=existing["filecode"],
            title=existing.get("title") or movie_title,
        )
        _save_result(result)
        return result

    # Step 1: Extract Servers List
    servers = extract_servers_from_page(page_url, proxy_url)
    result["servers_found"] = servers

    if not servers:
        print("[jibi] No servers found, attempting direct yt-dlp on page URL...")
        r_direct = try_yt_dlp_extract(page_url, proxy_url)
        if r_direct["success"]:
            result["success"] = True
            result["direct_stream_url"] = r_direct["stream_url"]
            print(f"[jibi] Direct stream found: {result['direct_stream_url'][:100]}")
        else:
            result["errors"].append("No servers or direct streams detected.")
            _save_result(result)
            return result

    # Step 2a: Fast-path — try DoodStream clone on ALL dood embeds (no browser needed)
    if not result["filecode"] and api_key:
        dood_servers = [s for s in servers if is_doodstream_embed(s["embed_url"])]
        if dood_servers:
            print(f"\n[jibi] Phase 1: Trying DoodStream clone on {len(dood_servers)} embed(s)...")
        for s in dood_servers:
            embed_url = s["embed_url"]
            source_fc = extract_doodstream_filecode(embed_url) or ""

            # Check duplicate by source filecode before cloning
            dup = find_existing_upload(
                api_key, page_url, movie_title,
                source_filecode=source_fc, proxy_url=proxy_url,
            )
            if dup and dup.get("filecode"):
                result["success"] = True
                result["selected_server"] = s
                result["direct_stream_url"] = embed_url
                result["filecode"] = dup["filecode"]
                result["skipped_duplicate"] = True
                print(f"[jibi] Skipping clone — already exists! filecode={dup['filecode']}")
                break

            print(f"[jibi] Clone attempt '{s['name']}': {embed_url[:90]}")
            clone_res = try_doodstream_clone(embed_url, api_key, proxy_url)
            if clone_res["success"]:
                result["success"] = True
                result["selected_server"] = s
                result["direct_stream_url"] = embed_url
                result["filecode"] = clone_res["filecode"]
                result["source_filecode"] = source_fc
                print(f"[jibi] DoodStream clone success! Filecode: {clone_res['filecode']}")
                break
            print(f"[jibi] Clone failed: {clone_res.get('error')}, trying next dood server...")

    # Step 2b: Iterate remaining servers for direct stream extraction + upload
    if not result["filecode"]:
        # Blacklist filter: skip known-bad servers + per-session failures
        def is_blacklisted(url: str) -> bool:
            lower = url.lower()
            return any(host in lower for host in BLACKLISTED_SERVERS)

        def server_priority(s):
            url = s["embed_url"].lower()
            if is_doodstream_embed(url):
                return 1
            if "mixdrop" in url or "miixdrop" in url:
                return 2
            if "voe" in url:
                return 3
            if "streamtape" in url:
                return 4
            if "filemoon" in url:
                return 5
            if "streamwish" in url or "streamvid" in url:
                return 6
            return 10

        # Filter out blacklisted servers + session failures
        valid_servers = [
            s for s in servers
            if not is_blacklisted(s["embed_url"])
            and s["embed_url"] not in _failed_servers_session
        ]
        skipped = len(servers) - len(valid_servers)
        if skipped > 0:
            print(f"[jibi] Skipping {skipped} blacklisted/failed server(s)")

        sorted_servers = sorted(valid_servers, key=server_priority)

        for s in sorted_servers:
            if result["filecode"]:
                break
            if is_doodstream_embed(s["embed_url"]):
                continue  # already tried clone in phase 1

            embed_url = s["embed_url"]
            print(f"\n[jibi] Testing server '{s['name']}': {embed_url[:90]}")

            stream_url = None

            # Quick yt-dlp check on embed URL
            yt_res = try_yt_dlp_extract(embed_url, proxy_url)
            if yt_res["success"] and is_video_url(yt_res["stream_url"]):
                stream_url = yt_res["stream_url"]
                print(f"[jibi] Stream extracted via yt-dlp: {stream_url[:100]}")

            if not stream_url:
                stream_url = resolve_stream_from_embed(embed_url, proxy_url, referer=page_url)
                if stream_url:
                    print(f"[jibi] Stream extracted via Playwright: {stream_url[:100]}")

            if not stream_url:
                _failed_servers_session.add(embed_url)
                print(f"[jibi] No stream from server '{s['name']}', marking as failed")
                continue

            if download:
                dl_ok = download_locally(stream_url, proxy_url, referer=embed_url)
                if dl_ok:
                    result["success"] = True
                    result["selected_server"] = s
                    result["direct_stream_url"] = stream_url
                    print(f"[jibi] Download succeeded from server '{s['name']}'!")
                    break
                print(f"[jibi] Download failed for server '{s['name']}', trying next server...")
                continue

            if not api_key:
                result["success"] = True
                result["selected_server"] = s
                result["direct_stream_url"] = stream_url
                break

            if not is_remote_uploadable(stream_url):
                print(f"[jibi] Skipping m3u8/HLS stream for remote upload, trying next server...")
                result["errors"].append(f"Server '{s['name']}': m3u8 not supported for DoodStream upload")
                continue

            up_res = try_doodstream_upload(stream_url, api_key, proxy_url, title=movie_title)
            if up_res["success"]:
                result["success"] = True
                result["selected_server"] = s
                result["direct_stream_url"] = stream_url
                result["filecode"] = up_res["filecode"]
                print(f"[jibi] Upload verified from server '{s['name']}'!")
                break

            err = up_res.get("error", "Upload failed")
            print(f"[jibi] Upload failed for server '{s['name']}': {err}")
            result["errors"].append(f"Server '{s['name']}': {err}")

    # Final status
    if result["filecode"]:
        result["success"] = True
    elif not result["success"]:
        if not result["errors"]:
            result["errors"].append("No working server found for upload")

    # Step 4: Save to processed DB + catalog after successful upload
    if result["success"] and result["filecode"]:
        # Extract episode info from title
        episode_info = extract_episode_info(movie_title)
        media_type = episode_info.get("media_type", "movie")
        season = episode_info.get("season")
        episode = episode_info.get("episode")
        cleaned_title = episode_info.get("cleaned_title", movie_title)
        
        # Try to find tmdb_id: manual override > by title (catalog) > Cima4u slug search > TMDB API > by page URL
        resolved_tmdb_id = tmdb_id
        if not resolved_tmdb_id:
            resolved_tmdb_id = find_tmdb_id_by_title(cleaned_title)
        if not resolved_tmdb_id:
            # Try Cima4u slug-based search if URL is from Cima4u
            if "cimafu.cam" in page_url.lower():
                slug, year = extract_cima4u_info(page_url)
                if slug and year:
                    resolved_tmdb_id = search_tmdb_by_slug(slug, year)
        if not resolved_tmdb_id:
            # Use TMDB API with media type info
            year_match = re.search(r'\b(19|20)\d{2}\b', movie_title)
            year = year_match.group() if year_match else ""
            resolved_tmdb_id = search_tmdb_api(cleaned_title, year, media_type, season, episode)
        if not resolved_tmdb_id:
            resolved_tmdb_id = (get_entry_by_page(page_url, media_type) or {}).get("tmdb_id")
        
        catalog_entry = update_doodstream_in_catalog(
            filecode=result["filecode"],
            page_url=page_url,
            title=cleaned_title,
            embed_url=(result.get("selected_server") or {}).get("embed_url", ""),
            source_filecode=result.get("source_filecode", ""),
            tmdb_id=resolved_tmdb_id,
            media_type=media_type,
            season=season,
            episode=episode,
        )
        result["doodstream_url"] = catalog_entry.get("doodstream_url")
        result["playmogo_url"] = catalog_entry.get("playmogo_url")
        result["tmdb_id"] = catalog_entry.get("tmdb_id")
        result["media_type"] = media_type
        result["season"] = season
        result["episode"] = episode
        
        # Save to Supabase if tmdb_id is available
        if resolved_tmdb_id:
            save_to_supabase(
                tmdb_id=resolved_tmdb_id,
                title=cleaned_title,
                doodstream_url=catalog_entry.get("doodstream_url"),
                doodstream_download_url=catalog_entry.get("doodstream_download_url"),
                media_type=media_type,
                season=season,
                episode=episode
            )

        if not result.get("skipped_duplicate"):
            mark_as_processed(
                page_url=page_url,
                filecode=result["filecode"],
                title=movie_title,
                source_filecode=result.get("source_filecode", ""),
                embed_url=(result.get("selected_server") or {}).get("embed_url", ""),
            )

    _save_result(result)
    return result


def _save_result(result: dict):
    with open("jibi_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Standardized upload_result.json for GitHub Actions & downstream integrations
    fc = result.get("filecode")
    media_type = result.get("media_type", "movie")
    catalog_entry = get_entry_by_page(result.get("page_url", ""), media_type) or {}
    upload_res = {
        "status": "success" if result.get("success") and result.get("filecode") else "error",
        "page_url": result.get("page_url"),
        "video_url": result.get("direct_stream_url"),
        "tmdb_id": result.get("tmdb_id") or catalog_entry.get("tmdb_id"),
        "filecode": fc,
        "doodstream_url": result.get("doodstream_url") or (f"https://doodstream.com/e/{fc}" if fc else None),
        "playmogo_url": result.get("playmogo_url") or (f"https://playmogo.com/e/{fc}" if fc else None),
        "vidsrc_url": catalog_entry.get("vidsrc_url"),
        "selected_server": result.get("selected_server"),
        "servers_tried": len(result.get("servers_found", [])),
        "skipped_duplicate": result.get("skipped_duplicate", False),
        "errors": result.get("errors", []),
        "media_type": media_type,
        "season": result.get("season"),
        "episode": result.get("episode"),
    }
    with open("upload_result.json", "w", encoding="utf-8") as f:
        json.dump(upload_res, f, indent=2, ensure_ascii=False)

    print(f"\n[jibi] Results saved to jibi_result.json, upload_result.json & tmdb_movies.json")


def main():
    parser = argparse.ArgumentParser(description="Jibi Stream Scraper Bot")
    parser.add_argument("url", nargs="?", default=os.environ.get("PAGE_URL"),
                        help="Movie watch page URL to scrape")
    parser.add_argument("--api-key", default=os.environ.get("DOODSTREAM_API_KEY", ""),
                        help="DoodStream API Key for Remote Upload")
    parser.add_argument("--tmdb-id", type=int, default=None,
                        help="Manually specify TMDB ID (overrides auto-detection)")
    parser.add_argument("--download", action="store_true",
                        help="Download stream locally")
    parser.add_argument("--import-doodstream", action="store_true",
                        help="Import all videos from DoodStream account and update catalog")
    args = parser.parse_args()

    # Handle import mode
    if args.import_doodstream:
        if not args.api_key:
            print("Error: --api-key required for --import-doodstream")
            sys.exit(1)
        res = import_from_doodstream_account(args.api_key)
        sys.exit(0 if res.get("success") else 1)

    if not args.url:
        print("Error: Please provide a URL argument or set PAGE_URL environment variable.")
        sys.exit(1)

    res = run_jibi_bot(args.url, api_key=args.api_key, download=args.download, tmdb_id=args.tmdb_id)
    sys.exit(0 if res.get("success") and (res.get("filecode") or args.download) else 1)


if __name__ == "__main__":
    main()
