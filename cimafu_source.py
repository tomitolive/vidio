#!/usr/bin/env python3
"""Cimafu source finder for the DoodStream -> FileMoon mirror.

For a DoodStream filecode, this finds the same episode/movie on Cimafu
(search by title), extracts the watch page's real server embeds
(<meta itemprop="contentUrl">), and resolves a playable direct URL from
the hosts we can drain (Streamtape get_video flow first).
"""

import re
import requests
from urllib.parse import quote

from catalog import extract_episode_info

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

ARABIC_SEASON = {
    "الاول": 1, "الثاني": 2, "التاني": 2, "الثالث": 3, "الرابع": 4,
    "الخامس": 5, "السادس": 6, "السابع": 7, "الثامن": 8, "التاسع": 9, "العاشر": 10,
}


def _get(url: str, extra: dict | None = None, timeout: int = 25) -> requests.Response | None:
    try:
        return requests.get(url, headers={"User-Agent": UA, **(extra or {})}, timeout=timeout)
    except Exception as e:
        print(f"[cimafu] GET error {url[:60]}: {str(e)[:90]}")
        return None


def _og_title(html: str) -> str:
    m = re.search(r'property="og:title" content="([^"]*)"', html)
    if m:
        return m.group(1).strip()
    m = re.search(r"<title[^>]*>([^<]*)</title>", html)
    return re.sub(r"\s*\|\s*Cima4U.*$", "", m.group(1)).strip() if m else ""


def _content_urls(html: str) -> list[str]:
    return re.findall(r'itemprop="contentUrl" content="([^"]+)"', html)


def _matches_episode(og_title: str, season: int | None, episode: int | None) -> bool:
    if episode is not None:
        ep_m = re.search(r"الحلقة\s*(\d+)|\b(?:E|e)\s*(\d+)", og_title)
        if not ep_m:
            return False
        ep_num = int(ep_m.group(1) or ep_m.group(2))
        if ep_num != episode:
            return False
    if season is not None:
        # Arabic: الموسم الاول ...; English: S01 / Season 1
        s_m = re.search(
            r"الموسم[-\s]*(الاول|الثاني|التاني|الثالث|الرابع|الخامس|السادس|السابع|الثامن|التاسع|العاشر|\d+)|\b[Ss]\s*(\d+)\b|\b[Ss]eason\s*(\d+)",
            og_title,
        )
        if s_m:
            if s_m.group(1) and s_m.group(1).isdigit():
                s_num = int(s_m.group(1))
            elif s_m.group(1):
                s_num = ARABIC_SEASON.get(s_m.group(1))
            else:
                s_num = int(s_m.group(2) or s_m.group(3))
            if s_num != season:
                return False
    return True


def find_cimafu_page(cleaned_title: str, season: int | None, episode: int | None, max_candidates: int = 12) -> dict | None:
    """Search Cimafu for the watch page matching series+season+episode.
    Returns {"page_url", "og_title", "servers": [embed urls]} or None."""
    if not cleaned_title:
        return None
    search_url = f"https://cimafu.cam/?s={quote(cleaned_title)}"
    resp = _get(search_url)
    if not resp:
        return None
    html = resp.text
    links = re.findall(r'href="(https://cimafu\.cam/[^"]+)"', html)
    seen, candidates = set(), []
    for l in links:
        if any(x in l for x in ("feed", "comments", "category", "search/", "wp-json", "page")):
            continue
        if l in seen:
            continue
        seen.add(l)
        candidates.append(l)
        if len(candidates) >= max_candidates:
            break

    for page in candidates:
        r = _get(page)
        if not r:
            continue
        og = _og_title(r.text)
        if not og:
            continue
        if episode is not None or season is not None:
            if not _matches_episode(og, season, episode):
                print(f"[cimafu] skip (mismatch): {og[:60]}")
                continue
        servers = _content_urls(r.text)
        if servers:
            print(f"[cimafu] MATCH: {og[:70]}")
            return {"page_url": page, "og_title": og, "servers": servers}
    return None


# ─── Host resolvers ────────────────────────────────────────────────────────

def resolve_streamtape(embed_url: str) -> str | None:
    """Resolve a streamtape embed to a direct get_video URL (plain requests)."""
    m = re.match(r"https?://([^/]+)/(?:e|v)/([A-Za-z0-9_\-]+)", embed_url)
    if not m:
        return None
    host, fid = m.group(1), m.group(2)
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Referer": "https://cimafu.cam/"})
    v = sess.get(f"https://{host}/v/{fid}", timeout=30)
    if v.status_code != 200:
        return None
    tok = re.search(
        r"get_video\?id=([A-Za-z0-9]+)&expires=(\d+)&ip=([A-Za-z0-9]+)&token=([A-Za-z0-9_\-]+)",
        v.text,
    )
    if not tok:
        return None
    gv = (
        f"https://{host}/get_video?id={tok.group(1)}&expires={tok.group(2)}"
        f"&ip={tok.group(3)}&token={tok.group(4)}"
    )
    print(f"[cimafu] streamtape direct: {gv[:110]}...")
    return gv


def _streamtape_browser(embed_url: str, max_wait: int = 28) -> str | None:
    """Open the streamtape /v/ page in a real (headless) browser and capture
    the actual direct video URL from the network (bypasses the JS-built token)."""
    m = re.match(r"https?://([^/]+)/(?:e|v)/([A-Za-z0-9_\-]+)", embed_url)
    if not m:
        return None
    host, fid = m.group(1), m.group(2)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[cimafu] playwright unavailable: {str(e)[:80]}")
        return None
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled",
        ])
        ctx = b.new_context(
            user_agent=UA, viewport={"width": 1280, "height": 720},
        )
        pg = ctx.new_page()
        found = []

        def on_resp(r):
            ct = r.headers.get("content-type", "").lower()
            if "video" in ct or r.url.lower().endswith((".mp4", ".m3u8")) or "video" in r.url.lower():
                if r.url not in found:
                    found.append(r.url)
                    print(f"[cimafu] captured video: {r.url[:110]}")

        pg.on("response", on_resp)
        try:
            pg.goto(f"https://{host}/v/{fid}", wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(6000)
            for sel in (".plyr__control--overlaid", ".plyr__control", "button", "video"):
                try:
                    el = pg.query_selector(sel)
                    if el:
                        el.click(timeout=1500)
                        print(f"[cimafu] clicked {sel}")
                        break
                except Exception:
                    pass
            import time as _t
            deadline = _t.time() + max_wait
            while _t.time() < deadline and not found:
                pg.wait_for_timeout(2500)
        finally:
            b.close()
    return found[0] if found else None


def resolve_embed(embed_url: str) -> str | None:
    """Try to get a playable direct URL from a server embed."""
    host = embed_url.split("/")[2].lower() if "//" in embed_url else embed_url
    if "streamtape" in host:
        return _streamtape_browser(embed_url)
    # Hosts we already know need a browser token flow we don't run yet: skip fast.
    if any(k in host for k in ("cybervynx", "doodstream", "playmogo", "mixdrop", "miixdrop")):
        return None
    # Generic hosts: try yt-dlp impersonate.
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({
            "quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
            "force_generic_extractor": True,
            "extractor_args": {"generic": {"impersonate": ["chrome"]}},
        }) as ydl:
            info = ydl.extract_info(embed_url, download=False)
        for k in ("url", "hls_url", "manifest_url"):
            if info.get(k):
                print(f"[cimafu] yt-dlp stream for {host}: {info[k][:90]}")
                return info[k]
        fmts = info.get("formats") or []
        if fmts:
            best = max((f for f in fmts if f.get("url")), key=lambda f: f.get("height") or 0, default=None)
            if best:
                return best["url"]
    except Exception as e:
        print(f"[cimafu] yt-dlp failed for {host}: {str(e)[:80]}")
    return None


def find_source_url_from_cimafu(title: str) -> str | None:
    """High-level: get a mirrorable source URL for a DoodStream title via Cimafu."""
    info = extract_episode_info(title)
    season = info.get("season")
    episode = info.get("episode")
    cleaned = info.get("cleaned_title") or title
    print(f"[cimafu] Looking for {cleaned!r} S{season}E{episode} on Cimafu...")
    page = find_cimafu_page(cleaned, season, episode)
    if not page:
        print("[cimafu] no matching watch page found")
        return None
    for emb in page["servers"]:
        u = resolve_embed(emb)
        if u:
            return u
    return None