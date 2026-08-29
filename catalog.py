"""Shared catalog for TMDB IDs and DoodStream URLs per page/movie."""

import json
import os
import re
import time

CATALOG_FILE = "tmdb_movies.json"


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def normalize_page_key(url: str) -> str:
    url = url.strip().rstrip("/").lower()
    if url.startswith("://"):
        url = "https" + url
    elif not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/")


def load_catalog() -> dict:
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if "movies" not in data:
                    data["movies"] = {}
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"movies": {}, "last_updated": None}


def save_catalog(catalog: dict):
    catalog["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)


def _find_catalog_key(catalog: dict, page_url: str = "", title: str = "") -> str | None:
    """Find existing entry key by page URL or title."""
    movies = catalog.get("movies", {})
    if page_url:
        page_key = normalize_page_key(page_url)
        if page_key in movies:
            return page_key
        for key, entry in movies.items():
            if normalize_page_key(entry.get("page_url", "")) == page_key:
                return key
    if title:
        title_key = normalize_key(title)
        if title_key in movies:
            return title_key
        for key, entry in movies.items():
            if normalize_key(entry.get("source_title", "") or entry.get("title", "")) == title_key:
                return key
    return None


def update_doodstream_in_catalog(
    filecode: str,
    page_url: str = "",
    title: str = "",
    embed_url: str = "",
    source_filecode: str = "",
    tmdb_id: int | None = None,
) -> dict:
    """Add or update DoodStream URLs for a page/movie in the shared catalog."""
    if not filecode:
        return {}

    catalog = load_catalog()
    movies = catalog.setdefault("movies", {})

    key = _find_catalog_key(catalog, page_url, title)
    if not key:
        key = normalize_page_key(page_url) if page_url else normalize_key(title or filecode)

    entry = movies.get(key, {})
    entry.update({
        "success": True,
        "filecode": filecode,
        "doodstream_url": f"https://doodstream.com/e/{filecode}",
        "playmogo_url": f"https://playmogo.com/e/{filecode}",
        "doodstream_download_url": f"https://playmogo.com/d/{filecode}",
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    if page_url:
        entry["page_url"] = page_url
    if title:
        entry["source_title"] = title
        if not entry.get("title"):
            entry["title"] = title
    if embed_url:
        entry["embed_url"] = embed_url
    if source_filecode:
        entry["source_filecode"] = source_filecode
    if tmdb_id:
        entry["tmdb_id"] = tmdb_id
        media_type = entry.get("type", "movie")
        entry["vidsrc_url"] = f"https://vidsrc.sbs/embed/{media_type}/{tmdb_id}"

    movies[key] = entry
    save_catalog(catalog)
    print(f"[tmdb] Catalog saved to {CATALOG_FILE}")
    print(f"[catalog] Saved DoodStream URL for: {entry.get('title') or page_url or key}")
    print(f"[catalog] -> {entry['doodstream_url']}")
    return entry


def get_entry_by_page(page_url: str) -> dict | None:
    catalog = load_catalog()
    key = _find_catalog_key(catalog, page_url=page_url)
    if key:
        return catalog["movies"][key]
    return None
