#!/usr/bin/env python3
"""
Stream Bot - Universal Video Stream Finder & Downloader
Usage:
    python stream_bot.py <URL> [--api-key <KEY>] [--download]
    PAGE_URL=<url> python stream_bot.py
"""

import os
import sys
import json
import time
import re
import random
import argparse
from urllib.parse import urlparse, urljoin

import requests

# ─── Proxy Setup ────────────────────────────────────────────────────────────

PROXIES_LIST = [
    "http://ohzgotst:ea339u0rwqy8@31.59.20.176:6754",
    "http://ohzgotst:ea339u0rwqy8@45.38.107.97:6014",
    "http://ohzgotst:ea339u0rwqy8@198.105.121.200:6462",
    "http://ohzgotst:ea339u0rwqy8@64.137.96.74:6641",
    "http://ohzgotst:ea339u0rwqy8@198.23.243.226:6361",
    "http://ohzgotst:ea339u0rwqy8@38.154.185.97:6370",
    "http://ohzgotst:ea339u0rwqy8@84.247.60.125:6095",
    "http://ohzgotst:ea339u0rwqy8@142.111.67.146:5611",
    "http://ohzgotst:ea339u0rwqy8@191.96.254.138:6185",
    "http://ohzgotst:ea339u0rwqy8@31.58.9.4:6077",
]

PROXY_URL = os.environ.get("PROXY_URL", "").strip() or random.choice(PROXIES_LIST)
REQUESTS_PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

if PROXY_URL:
    masked = PROXY_URL.split("@")[-1] if "@" in PROXY_URL else PROXY_URL
    print(f"[proxy] {masked}")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def is_video_url(url: str, content_type: str = "") -> bool:
    """Return True if URL looks like a direct video stream."""
    video_exts = (".m3u8", ".mp4", ".webm", ".ts", ".mkv", ".avi")
    video_ct = ("video/", "application/x-mpegurl", "application/vnd.apple.mpegurl",
                 "audio/mpegurl")
    url_lower = url.lower().split("?")[0]
    if any(url_lower.endswith(ext) for ext in video_exts):
        return True
    if content_type and any(ct in content_type.lower() for ct in video_ct):
        return True
    return False


def clean_url(url: str) -> str:
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def parse_pw_proxy(proxy_url: str):
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


# ─── Playwright Browser Helper ───────────────────────────────────────────────

def make_browser(p, with_proxy=True):
    """Launch a stealth Playwright Chromium browser."""
    kwargs = {
        "headless": HEADLESS,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1920,1080",
        ],
    }
    if with_proxy and PROXY_URL:
        pw_proxy = parse_pw_proxy(PROXY_URL)
        if pw_proxy:
            kwargs["proxy"] = pw_proxy

    browser = p.chromium.launch(**kwargs)
    ctx = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )
    page = ctx.new_page()
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except ImportError:
        pass
    return browser, ctx, page


# ─── Core: Network Interception + iframe Traversal ───────────────────────────

def collect_streams_from_page(page, wait_seconds: int = 8) -> list[str]:
    """
    Listen to network responses on an already-open page and collect video URLs.
    Also parses <video src>, <source src>, and script-embedded m3u8/mp4 URLs.
    """
    found: list[str] = []

    def on_response(resp):
        try:
            url = resp.url
            ct = resp.headers.get("content-type", "")
            if is_video_url(url, ct) and url not in found:
                print(f"  [net] captured: {url[:120]}")
                found.append(url)
        except Exception:
            pass

    page.on("response", on_response)

    # Click play if present and wait
    try_click_play(page)
    time.sleep(wait_seconds)
    # Try clicking play again after initial wait (some players need 2 clicks)
    try_click_play(page)
    time.sleep(3)

    # Also scrape page HTML for video URLs
    try:
        html = page.content()
        patterns = [
            r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
            r'"file"\s*:\s*"(https?://[^"]+)"',
            r'"src"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
            r"'(https?://[^']+\.m3u8[^']*)'",
        ]
        for pat in patterns:
            for m in re.findall(pat, html):
                m = clean_url(m.replace("\\/", "/"))
                if m not in found and is_video_url(m):
                    print(f"  [html] found: {m[:120]}")
                    found.append(m)
    except Exception:
        pass

    return found


def try_click_play(page):
    """Attempt to click any play button on the page."""
    selectors = [
        ".play-btn", ".play_btn", ".btn-play", "#play-btn", "#btnPlay",
        "button[class*='play']", "div[class*='play']", ".jw-icon-display",
        ".vjs-big-play-button", ".plyr__control--overlaid", ".fa-play",
        "[aria-label='Play']", ".playBtn", ".play-button",
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                print(f"  [click] clicked play: {sel}")
                return True
        except Exception:
            pass
    return False


def get_iframe_src_list(page) -> list[str]:
    """Return all iframe src attributes on the current page."""
    srcs = []
    try:
        iframes = page.query_selector_all("iframe")
        for fr in iframes:
            src = fr.get_attribute("src")
            if src and src.strip() and src.strip() != "about:blank":
                srcs.append(clean_url(src.strip()))
        
        # Also grab any data-link attributes that contain URLs (common for server lists)
        links = page.query_selector_all("[data-link]")
        for l in links:
            link = l.get_attribute("data-link")
            if link and (link.startswith("http") or link.startswith("//")):
                srcs.append(clean_url(link.strip()))
    except Exception:
        pass
        
    seen = set()
    res = []
    for s in srcs:
        if s not in seen:
            seen.add(s)
            res.append(s)
    return res


def _try_dedicated_handler_pw(src: str, ctx, referer: str) -> list[str]:
    """
    Dedicated Playwright handler for known embed hosts that need extra time:
    vidtube.one, doodstream embeds, streamtape, voe, etc.
    Opens the page, waits 20s, captures all video network responses.
    Returns list of found stream URLs (empty list if nothing found).
    """
    known_hosts = (
        "vidtube.", "dood.", "doodstream.", "ds2play.", "streamtape.", "voe.sx",
        "vidaraa.", "mixdrop.", "nxsha.", "videasy.", "hgcloud.", "cinesrc.",
        "vhq.", "moviesapi.", "multiembed.", "embed.", "player.",
        "morencius.", "playmogo.", "bysekoze.", "egydead."
    )
    domain = urlparse(src).netloc.lower()
    if not any(h in domain for h in known_hosts):
        return []

    print(f"    [dedicated] using dedicated handler for: {domain}")
    found: list[str] = []

    try:
        sub = ctx.new_page()
        sub.set_extra_http_headers({
            "Referer": referer,
            "Origin": urlparse(referer).scheme + "://" + urlparse(referer).netloc,
        })

        def on_resp(resp):
            try:
                u = resp.url
                ct = resp.headers.get("content-type", "")
                if is_video_url(u, ct) and u not in found:
                    print(f"    [dedicated-net] {u[:120]}")
                    found.append(u)
            except Exception:
                pass

        sub.on("response", on_resp)
        try:
            sub.goto(src, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"    [dedicated] goto warn: {e}")

        # Click play multiple times across a 20s window
        for _ in range(4):
            try_click_play(sub)
            time.sleep(5)

        # Also scrape HTML for embedded URLs
        try:
            html = sub.content()
            patterns = [
                r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
                r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
                r'"file"\s*:\s*"(https?://[^"]+)"',
                r'"src"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
                r"'(https?://[^']+\.m3u8[^']*)'",
            ]
            for pat in patterns:
                for m in re.findall(pat, html):
                    m = clean_url(m.replace("\\/", "/"))
                    if m not in found and is_video_url(m):
                        print(f"    [dedicated-html] {m[:120]}")
                        found.append(m)
        except Exception:
            pass

        sub.close()
    except Exception as e:
        print(f"    [dedicated] error: {e}")

    return found


def traverse_and_collect(
    page,
    base_url: str,
    depth: int = 0,
    max_depth: int = 4,
    visited: set | None = None,
) -> list[str]:
    """
    Recursively open each iframe src in a new sub-page and collect video URLs.
    Returns a flat list of all found stream URLs.
    """
    if visited is None:
        visited = set()
    if depth > max_depth:
        return []

    streams: list[str] = []

    # Collect from current page (longer wait at depth>0 so JS players load)
    wait = 12 if depth > 0 else 6
    streams += collect_streams_from_page(page, wait_seconds=wait)

    # Find iframes on current page
    iframe_srcs = get_iframe_src_list(page)
    print(f"  [depth={depth}] {len(iframe_srcs)} iframes found: {iframe_srcs}")

    ctx = page.context
    for src in iframe_srcs:
        # Make absolute
        if not src.startswith("http"):
            src = urljoin(base_url, src)

        if src in visited:
            continue
        visited.add(src)

        print(f"\n  [iframe→] entering: {src[:100]}")
        try:
            # Use a dedicated Playwright handler for known hosting sites
            dedicated = _try_dedicated_handler_pw(src, ctx, base_url)
            if dedicated:
                streams += dedicated
                continue

            sub_page = ctx.new_page()
            sub_page.set_extra_http_headers({
                "Referer": base_url,
                "Origin": urlparse(base_url).scheme + "://" + urlparse(base_url).netloc,
            })

            sub_streams: list[str] = []

            def on_sub_resp(resp):
                try:
                    u = resp.url
                    ct = resp.headers.get("content-type", "")
                    if is_video_url(u, ct) and u not in sub_streams:
                        print(f"    [net-sub] {u[:120]}")
                        sub_streams.append(u)
                except Exception:
                    pass

            sub_page.on("response", on_sub_resp)

            try:
                sub_page.goto(src, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"    [warn] goto failed: {e}")

            # Wait for JS player to init, then click play
            time.sleep(5)
            try_click_play(sub_page)
            time.sleep(3)

            streams += traverse_and_collect(
                sub_page, src, depth + 1, max_depth, visited
            )
            streams += sub_streams

            sub_page.close()
        except Exception as e:
            print(f"    [err] iframe error: {e}")

    return streams


# ─── Stream Extractors ────────────────────────────────────────────────────────

def try_yt_dlp(url: str, download: bool = False, output_dir: str = ".") -> dict:
    """
    Use yt-dlp to extract or download from URL.
    Returns dict with keys: success, stream_url, title, error
    """
    try:
        import yt_dlp

        ydl_opts: dict = {
            "quiet": False,
            "no_warnings": False,
            "extract_flat": False,
            "noplaylist": True,
        }
        if PROXY_URL:
            ydl_opts["proxy"] = PROXY_URL

        if download:
            ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            ydl_opts["outtmpl"] = os.path.join(output_dir, "%(title)s.%(ext)s")
            ydl_opts["merge_output_format"] = "mp4"

        print(f"[yt-dlp] extracting: {url[:100]}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=download)
            if not info:
                return {"success": False, "error": "yt-dlp returned no info"}

            title = info.get("title", "")
            stream_url = None

            # Prefer best format URL
            fmts = info.get("formats", [])
            if fmts:
                # Pick best video+audio
                best = max(
                    (f for f in fmts if f.get("url")),
                    key=lambda f: (f.get("height") or 0),
                    default=None,
                )
                if best:
                    stream_url = best["url"]
            if not stream_url:
                stream_url = info.get("url")

            print(f"[yt-dlp] ✓ title={title!r}  url={str(stream_url)[:100]}")
            return {"success": True, "stream_url": stream_url, "title": title}

    except Exception as e:
        print(f"[yt-dlp] ✗ {e}")
        return {"success": False, "error": str(e)}


def try_streamlink(url: str) -> dict:
    """
    Use streamlink to extract stream URL.
    Returns dict with keys: success, stream_url, error
    """
    try:
        import streamlink as sl

        print(f"[streamlink] extracting: {url[:100]}")
        session = sl.Streamlink()
        streams = session.streams(url)
        if not streams:
            return {"success": False, "error": "streamlink: no streams"}

        for quality in ("best", "1080p", "720p", "480p", "360p", "worst"):
            if quality in streams:
                stream = streams[quality]
                stream_url = stream.url
                print(f"[streamlink] ✓ quality={quality}  url={stream_url[:100]}")
                return {"success": True, "stream_url": stream_url, "quality": quality}

        return {"success": False, "error": "streamlink: no suitable quality"}
    except ImportError:
        return {"success": False, "error": "streamlink not installed"}
    except Exception as e:
        print(f"[streamlink] ✗ {e}")
        return {"success": False, "error": str(e)}


def try_doodstream_remote(stream_url: str, api_key: str, title: str = "Video") -> dict:
    """
    Upload to DoodStream via remote URL API.
    """
    if not api_key:
        return {"success": False, "error": "no api_key"}
    try:
        print(f"[doodstream] remote-uploading: {stream_url[:80]}")
        resp = requests.get(
            f"https://doodapi.com/api/upload/url?key={api_key}&url={stream_url}",
            proxies=REQUESTS_PROXIES,
            timeout=60,
        )
        data = resp.json()
        if data.get("status") == 200:
            result = data.get("result", {})
            filecode = result.get("filecode")
            print(f"[doodstream] ✓ filecode={filecode}")
            # Rename
            if filecode and title:
                requests.get(
                    f"https://doodapi.com/api/file/rename?key={api_key}"
                    f"&file_code={filecode}&title={title}",
                    proxies=REQUESTS_PROXIES,
                    timeout=15,
                )
            return {"success": True, "filecode": filecode, "result": result}
        else:
            return {"success": False, "error": data.get("msg", "unknown")}
    except Exception as e:
        print(f"[doodstream] ✗ {e}")
        return {"success": False, "error": str(e)}


# ─── Server-specific Decoder (reuse logic from scrape_upload.py) ─────────────

def decode_url_by_domain(url: str) -> str | None:
    """
    Try known extraction tricks for specific streaming domains.
    Returns the direct stream URL or None.
    """
    domain = urlparse(url).netloc.lower()

    # DoodStream: yt-dlp handles it natively
    if any(d in domain for d in ("dood.", "doodstream.", "dooood.", "ds2play.")):
        res = try_yt_dlp(url)
        if res["success"]:
            return res["stream_url"]

    # vidaraa.cc
    if "vidaraa.cc" in domain:
        path = urlparse(url).path
        filecode = path.strip("/").split("/")[-1]
        try:
            resp = requests.post(
                "https://vidaraa.cc/api/stream",
                json={"filecode": filecode, "device": "web"},
                headers={"Origin": "https://vidaraa.cc", "Referer": url},
                proxies=REQUESTS_PROXIES,
                timeout=15,
            )
            data = resp.json()
            su = data.get("streaming_url")
            if su:
                print(f"[vidaraa] ✓ {su[:80]}")
                return su
        except Exception as e:
            print(f"[vidaraa] ✗ {e}")

    return None


# ─── Main Orchestrator ────────────────────────────────────────────────────────

def find_streams(page_url: str) -> list[str]:
    """
    Open page_url in Playwright, traverse all iframes, and return
    all collected video stream URLs.
    """
    from playwright.sync_api import sync_playwright

    all_streams: list[str] = []

    with sync_playwright() as p:
        browser, ctx, page = make_browser(p)
        try:
            print(f"\n[bot] Opening: {page_url}")

            # Intercept top-level page responses too
            top_streams: list[str] = []

            def on_top_resp(resp):
                try:
                    u = resp.url
                    ct = resp.headers.get("content-type", "")
                    if is_video_url(u, ct) and u not in top_streams:
                        print(f"  [top-net] {u[:120]}")
                        top_streams.append(u)
                except Exception:
                    pass

            page.on("response", on_top_resp)

            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"[warn] page.goto: {e}")

            time.sleep(3)

            # --- Egydead-Specific: Click the View button that switches from Trailer to Watch
            try:
                btn_locator = page.locator('button:has(input[name="View"][value="1"])')
                if btn_locator.count() > 0:
                    print("  [bot] Found View=1 button (Egydead), clicking to load watch page...")
                    # Click and wait for navigation if it happens
                    try:
                        with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                            btn_locator.first.click()
                    except Exception:
                        # Fallback if expect_navigation times out but it loaded anyway
                        pass
                    time.sleep(3)
            except Exception as e:
                print(f"  [warn] Egydead button click failed: {e}")

            # Traverse iframes recursively
            all_streams = traverse_and_collect(page, page_url, depth=0, max_depth=4)
            all_streams += top_streams

        finally:
            browser.close()

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for s in all_streams:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def run(page_url: str, api_key: str = "", download: bool = False):
    """Full pipeline: find streams → try download strategies → save result."""

    result = {
        "page_url": page_url,
        "success": False,
        "stream_url": None,
        "title": None,
        "method": None,
        "filecode": None,
        "all_found": [],
        "errors": [],
    }

    # ── Step 1: Try yt-dlp directly on the page URL first ─────────────────
    print("\n══ Step 1: yt-dlp on page URL ══")
    r1 = try_yt_dlp(page_url, download=download)
    if r1["success"]:
        result.update({
            "success": True,
            "stream_url": r1.get("stream_url"),
            "title": r1.get("title"),
            "method": "yt-dlp-direct",
        })
        # If download requested yt-dlp already downloaded it
        _save(result)
        return result

    result["errors"].append(f"yt-dlp-direct: {r1.get('error')}")

    # ── Step 2: Playwright iframe traversal ─────────────────────────────────
    print("\n══ Step 2: Playwright iframe traversal ══")
    streams = find_streams(page_url)
    result["all_found"] = streams
    print(f"\n[bot] Total streams found: {len(streams)}")
    for s in streams:
        print(f"  → {s[:120]}")

    if not streams:
        # ── Step 3: Streamlink on the page URL ────────────────────────────
        print("\n══ Step 3: streamlink on page URL ══")
        r3 = try_streamlink(page_url)
        if r3["success"]:
            streams = [r3["stream_url"]]
        else:
            result["errors"].append(f"streamlink: {r3.get('error')}")

    # Try each stream URL found
    best_stream = None
    best_title = ""
    for stream in streams:
        print(f"\n[bot] Trying stream: {stream[:100]}")

        # Domain-specific decoder first
        decoded = decode_url_by_domain(stream)
        if decoded:
            best_stream = decoded
            break

        # yt-dlp on the stream URL
        r = try_yt_dlp(stream, download=download)
        if r["success"]:
            best_stream = r.get("stream_url") or stream
            best_title = r.get("title", "")
            break

        # streamlink on the stream URL
        r = try_streamlink(stream)
        if r["success"]:
            best_stream = r["stream_url"]
            break

        # If it's already a direct m3u8/mp4 just use it
        if is_video_url(stream):
            best_stream = stream
            break

    if best_stream:
        result.update({
            "success": True,
            "stream_url": best_stream,
            "title": best_title or "Video",
            "method": "iframe-traversal",
        })

        # ── Step 4: DoodStream remote upload (optional) ─────────────────
        if api_key:
            print("\n══ Step 4: DoodStream remote upload ══")
            r4 = try_doodstream_remote(best_stream, api_key, title=best_title or "Video")
            if r4["success"]:
                result["filecode"] = r4.get("filecode")
                result["method"] += "+doodstream-upload"
            else:
                result["errors"].append(f"doodstream: {r4.get('error')}")

        # ── Download locally if requested ────────────────────────────────
        if download and not result["filecode"]:
            print("\n══ Downloading locally with yt-dlp ══")
            try_yt_dlp(best_stream, download=True)
    else:
        result["errors"].append("No usable stream found after all strategies")

    _save(result)
    return result


def _save(result: dict):
    with open("stream_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n[bot] Result saved → stream_result.json")
    print(f"[bot] success={result['success']}  stream_url={str(result.get('stream_url', ''))[:120]}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Universal Stream Finder Bot")
    parser.add_argument("url", nargs="?", default=os.environ.get("PAGE_URL"),
                        help="Movie page URL to scrape")
    parser.add_argument("--api-key", default=os.environ.get("EARNVIDS_API_KEY", ""),
                        help="DoodStream API key for remote upload")
    parser.add_argument("--download", action="store_true",
                        help="Download the video locally with yt-dlp")
    args = parser.parse_args()

    if not args.url:
        print("Error: provide a URL as argument or set PAGE_URL env var")
        sys.exit(1)

    result = run(args.url, api_key=args.api_key, download=args.download)
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
