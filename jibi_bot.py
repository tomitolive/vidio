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
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

# ─── 1. Proxies Configuration ───────────────────────────────────────────────

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


def get_random_proxy() -> str:
    """Return a proxy string from environment or rotated list."""
    env_proxy = os.environ.get("PROXY_URL", "").strip()
    return env_proxy if env_proxy else random.choice(PROXIES_LIST)


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
    if url.startswith("://"):
        url = "https" + url
    elif not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url


# ─── 3. Server List Parser ──────────────────────────────────────────────────

def extract_servers_from_page(page_url: str, proxy_url: str) -> list[dict]:
    """
    Parses the main HTML page looking for <ul class="server_list"> or iframe elements.
    Returns list of dicts: [{"name": "سيرفر 1", "embed_url": "..."}, ...]
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    print(f"[jibi] Scraping servers from: {page_url}")
    proxies = get_requests_proxies(proxy_url)

    try:
        resp = requests.get(page_url, headers=headers, proxies=proxies, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[jibi] Initial HTTP request error: {e}")
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


def try_doodstream_upload(stream_url: str, api_key: str, proxy_url: str = "", title: str = "Video") -> dict:
    """Remote upload stream_url to DoodStream."""
    if not api_key:
        return {"success": False, "error": "No DoodStream API key"}

    print(f"[doodstream] Triggering remote upload for: {stream_url[:80]}")
    try:
        proxies = get_requests_proxies(proxy_url)
        resp = requests.get(
            f"https://doodapi.com/api/upload/url?key={api_key}&url={stream_url}",
            proxies=proxies,
            timeout=30,
        )
        data = resp.json()
        if data.get("status") == 200:
            filecode = data.get("result", {}).get("filecode")
            print(f"[doodstream] Remote upload success! Filecode: {filecode}")

            if filecode and title:
                requests.get(
                    f"https://doodapi.com/api/file/rename?key={api_key}&file_code={filecode}&title={title}",
                    proxies=proxies,
                    timeout=15,
                )
            return {"success": True, "filecode": filecode}
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

def run_jibi_bot(page_url: str, api_key: str = "", download: bool = False):
    page_url = clean_url(page_url)
    proxy_url = get_random_proxy()
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
    }

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

    # Step 2: Iterate through servers to find a working direct stream
    if not result["direct_stream_url"]:
        # Prioritize servers known for direct video / fast processing
        def server_priority(s):
            url = s["embed_url"].lower()
            if any(k in url for k in ("dood", "ds2play", "d0o0d")):
                return 1
            if "earnvids" in url or "lulustream" in url or "luluvdo" in url:
                return 2
            if "streamwish" in url or "streamtape" in url or "filemoon" in url:
                return 3
            return 10

        sorted_servers = sorted(servers, key=server_priority)

        for s in sorted_servers:
            embed_url = s["embed_url"]
            print(f"\n[jibi] Testing server '{s['name']}': {embed_url[:90]}")

            # 2a. Direct DoodStream embed remote upload optimization
            if any(k in embed_url.lower() for k in ("dood.", "ds2play.", "d0o0d.")):
                result["success"] = True
                result["selected_server"] = s
                result["direct_stream_url"] = embed_url
                print(f"[jibi] Recognized DoodStream embed: {embed_url}")
                if api_key:
                    up_res = try_doodstream_upload(embed_url, api_key, proxy_url)
                    if up_res["success"]:
                        result["filecode"] = up_res["filecode"]
                        print(f"[jibi] DoodStream remote upload/clone success! Filecode: {up_res['filecode']}")
                        break

            # 2b. Quick yt-dlp check on embed URL
            yt_res = try_yt_dlp_extract(embed_url, proxy_url)
            if yt_res["success"] and is_video_url(yt_res["stream_url"]):
                stream_url = yt_res["stream_url"]
                if download:
                    dl_ok = download_locally(stream_url, proxy_url, referer=embed_url)
                    if dl_ok:
                        result["success"] = True
                        result["selected_server"] = s
                        result["direct_stream_url"] = stream_url
                        print(f"[jibi] Download succeeded from server '{s['name']}'!")
                        break
                else:
                    result["success"] = True
                    result["selected_server"] = s
                    result["direct_stream_url"] = stream_url
                    print(f"[jibi] Stream extracted via yt-dlp: {stream_url[:100]}")
                    break

            # 2c. Playwright popup-safe network interception
            stream_found = resolve_stream_from_embed(embed_url, proxy_url, referer=page_url)
            if stream_found:
                if download:
                    dl_ok = download_locally(stream_found, proxy_url, referer=embed_url)
                    if dl_ok:
                        result["success"] = True
                        result["selected_server"] = s
                        result["direct_stream_url"] = stream_found
                        print(f"[jibi] Download succeeded from server '{s['name']}'!")
                        break
                    else:
                        print(f"[jibi] Download failed for server '{s['name']}', trying next server...")
                        continue
                else:
                    result["success"] = True
                    result["selected_server"] = s
                    result["direct_stream_url"] = stream_found
                    print(f"[jibi] Stream extracted via Playwright: {stream_found[:100]}")
                    break

    # Step 3: Handle Upload if not already done in 2a
    if result["success"] and result["direct_stream_url"] and api_key and not result["filecode"]:
        up_res = try_doodstream_upload(result["direct_stream_url"], api_key, proxy_url)
        if up_res["success"]:
            result["filecode"] = up_res["filecode"]
        else:
            result["errors"].append(f"Upload failed: {up_res.get('error')}")

    _save_result(result)
    return result


def _save_result(result: dict):
    with open("jibi_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Standardized upload_result.json for GitHub Actions & downstream integrations
    upload_res = {
        "status": "success" if result.get("success") else "error",
        "page_url": result.get("page_url"),
        "video_url": result.get("direct_stream_url"),
        "filecode": result.get("filecode"),
        "doodstream_url": f"https://doodstream.com/e/{result['filecode']}" if result.get("filecode") else None,
        "selected_server": result.get("selected_server"),
        "errors": result.get("errors", []),
    }
    with open("upload_result.json", "w", encoding="utf-8") as f:
        json.dump(upload_res, f, indent=2, ensure_ascii=False)

    print(f"\n[jibi] Results saved to jibi_result.json & upload_result.json")


def main():
    parser = argparse.ArgumentParser(description="Jibi Stream Scraper Bot")
    parser.add_argument("url", nargs="?", default=os.environ.get("PAGE_URL"),
                        help="Movie watch page URL to scrape")
    parser.add_argument("--api-key", default=os.environ.get("DOODSTREAM_API_KEY", ""),
                        help="DoodStream API Key for Remote Upload")
    parser.add_argument("--download", action="store_true",
                        help="Download stream locally")
    args = parser.parse_args()

    if not args.url:
        print("Error: Please provide a URL argument or set PAGE_URL environment variable.")
        sys.exit(1)

    res = run_jibi_bot(args.url, api_key=args.api_key, download=args.download)
    sys.exit(0 if res["success"] else 1)


if __name__ == "__main__":
    main()
