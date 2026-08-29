#!/usr/bin/env python3
"""
TMDB Bot - Search movie titles on TMDB and save IDs to tmdb_movies.json

Usage:
    python tmdb_bot.py "Ice Fall 2025" "Tron Ares 2025"
    python tmdb_bot.py --page-url "https://cimafu.cam/..."
    python tmdb_bot.py --file titles.txt
    MOVIE_TITLES="Ice Fall 2025,Tron Ares 2025" python tmdb_bot.py
"""

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

TMDB_CATALOG_FILE = "tmdb_movies.json"
DEFAULT_TMDB_API_KEY = "2dca580c2a14b55200e784d157207b4d"

STOPWORDS = {
    "مشاهدة", "فيلم", "وتحميل", "مترجم", "مباشر", "watch", "download",
    "movie", "film", "online", "free", "hd", "full", "cima4u", "cimafu",
    "مسلسل", "حلقة", "الحلقة", "موسم", "series", "season", "episode",
}


def detect_media_type(title: str, page_url: str = "") -> str:
    """Guess if content is a TV series or movie."""
    text = f"{title} {page_url}".lower()
    tv_signals = (
        "مسلسل", "موسم", "حلقة", "series", "season", "episode",
        "/tv/", "/series/", "مسلسل-",
    )
    if any(s in text for s in tv_signals):
        return "tv"
    return "movie"


def get_tmdb_api_key() -> str:
    return os.environ.get("TMDB_API_KEY", DEFAULT_TMDB_API_KEY).strip()


def load_catalog() -> dict:
    if os.path.exists(TMDB_CATALOG_FILE):
        try:
            with open(TMDB_CATALOG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"movies": {}, "last_updated": None}


def save_catalog(catalog: dict):
    catalog["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(TMDB_CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
    print(f"[tmdb] Catalog saved to {TMDB_CATALOG_FILE}")


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def clean_title(title: str) -> str:
    """Remove Arabic filler words and extra symbols from a title."""
    title = unquote(title).strip()
    title = re.sub(r"\s*\|\s*.*$", "", title)  # remove site suffix after |
    title = re.sub(r"[^\w\s:\-'.]", " ", title, flags=re.UNICODE)
    parts = [p for p in title.split() if p.lower() not in STOPWORDS]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def extract_year(text: str) -> str | None:
    match = re.search(r"\b((?:19|20)\d{2})\b", text)
    return match.group(1) if match else None


def extract_title_from_page(page_url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(page_url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return clean_title(og["content"])

    title_tag = soup.find("title")
    if title_tag and title_tag.text:
        return clean_title(title_tag.text)

    # Fallback: slug from URL
    slug = page_url.rstrip("/").split("/")[-2] if page_url.endswith("/watch/") else page_url.rstrip("/").split("/")[-1]
    slug = slug.replace("-", " ")
    return clean_title(slug)


def search_tmdb(title: str, year: str | None = None, api_key: str = "", media_type: str = "auto") -> dict:
    """Search TMDB movie or TV and return best match."""
    api_key = api_key or get_tmdb_api_key()

    queries = [title]
    if " " in title:
        queries.append(title.replace(" ", ""))
    queries.append(re.sub(r"[:.\-]", " ", title))
    queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))

    types_to_try = []
    if media_type == "auto":
        guessed = detect_media_type(title)
        types_to_try = [guessed, "tv" if guessed == "movie" else "movie"]
    else:
        types_to_try = [media_type]

    best_overall = None
    best_score = -1

    for mtype in types_to_try:
        for query in queries:
            for use_year in (True, False):
                if not use_year and not year:
                    continue
                results = _tmdb_api_search(query, year if use_year else None, mtype, api_key)
                if not results:
                    continue
                candidate = pick_best_match(results, title, year, mtype)
                score = _match_score(candidate, title, year, mtype)
                if score > best_score:
                    best_score = score
                    best_overall = _format_result(candidate, query, mtype)

    if best_overall:
        return best_overall
    return {"success": False, "error": "No results on TMDB", "query": title}


def _tmdb_api_search(query: str, year: str | None, media_type: str, api_key: str) -> list:
    endpoint = "tv" if media_type == "tv" else "movie"
    params = {
        "api_key": api_key,
        "query": query,
        "include_adult": "false",
        "language": "en-US",
    }
    if year and media_type == "movie":
        params["year"] = year
    if year and media_type == "tv":
        params["first_air_date_year"] = year

    label = f"{media_type}:{query}" + (f" ({year})" if year else "")
    print(f"[tmdb] Searching {label}")
    resp = requests.get(f"https://api.themoviedb.org/3/search/{endpoint}", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json().get("results", [])


def _match_score(item: dict, query: str, year: str | None, media_type: str) -> float:
    query_lower = query.lower()
    title = (item.get("title") or item.get("name") or "").lower()
    original = (item.get("original_title") or item.get("original_name") or "").lower()
    date = item.get("release_date") or item.get("first_air_date") or ""
    item_year = date[:4]
    s = item.get("popularity", 0) / 10.0

    if query_lower in title or query_lower in original:
        s += 50
    if title in query_lower or original in query_lower:
        s += 30

    query_tokens = set(re.findall(r"[a-z0-9]+", query_lower))
    title_tokens = set(re.findall(r"[a-z0-9]+", title + " " + original))
    s += len(query_tokens & title_tokens) * 5

    if year and item_year == year:
        s += 40
    elif year and item_year and abs(int(item_year) - int(year)) <= 1:
        s += 15

    if media_type == detect_media_type(query):
        s += 10

    return s


def _format_result(best: dict, query: str, media_type: str) -> dict:
    date = best.get("release_date") or best.get("first_air_date") or ""
    title = best.get("title") or best.get("name")
    original = best.get("original_title") or best.get("original_name")
    tmdb_id = best["id"]
    vidsrc_path = "tv" if media_type == "tv" else "movie"
    return {
        "success": True,
        "type": media_type,
        "tmdb_id": tmdb_id,
        "title": title,
        "original_title": original,
        "year": date[:4] if date else "",
        "overview": best.get("overview", ""),
        "poster_path": best.get("poster_path"),
        "vote_average": best.get("vote_average"),
        "popularity": best.get("popularity"),
        "query": query,
        "vidsrc_url": f"https://vidsrc.sbs/embed/{vidsrc_path}/{tmdb_id}",
    }


def pick_best_match(results: list, query: str, year: str | None = None, media_type: str = "movie") -> dict:
    return max(results, key=lambda item: _match_score(item, query, year, media_type))


def add_movie_to_catalog(
    catalog: dict,
    search_result: dict,
    source_title: str = "",
    page_url: str = "",
) -> dict:
    """Add or update a movie entry in the catalog."""
    if not search_result.get("success"):
        entry = {
            "success": False,
            "source_title": source_title,
            "page_url": page_url,
            "query": search_result.get("query", source_title),
            "error": search_result.get("error", "Unknown error"),
            "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        key = normalize_key(page_url or source_title or search_result.get("query", "unknown"))
        catalog["movies"][key] = entry
        return entry

    key = normalize_key(source_title or search_result.get("title") or str(search_result["tmdb_id"]))
    entry = {
        "success": True,
        "type": search_result.get("type", "movie"),
        "tmdb_id": search_result["tmdb_id"],
        "title": search_result.get("title"),
        "original_title": search_result.get("original_title"),
        "year": search_result.get("year"),
        "overview": search_result.get("overview"),
        "poster_url": (
            f"https://image.tmdb.org/t/p/w500{search_result['poster_path']}"
            if search_result.get("poster_path") else None
        ),
        "source_title": source_title,
        "page_url": page_url,
        "query": search_result.get("query"),
        "vidsrc_url": search_result.get("vidsrc_url"),
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    catalog["movies"][key] = entry
    print(
        f"[tmdb] Found [{entry['type']}]: {entry['title']} ({entry['year']}) "
        f"-> ID {entry['tmdb_id']}"
    )
    return entry


def process_title(title: str, catalog: dict, page_url: str = "", api_key: str = "") -> dict:
    cleaned = clean_title(title)
    if not cleaned:
        return {"success": False, "error": "Empty title", "source_title": title}

    year = extract_year(cleaned)
    query = re.sub(r"\b((?:19|20)\d{2})\b", "", cleaned).strip()
    query = query or cleaned

    key = normalize_key(page_url or cleaned)
    if key in catalog.get("movies", {}) and catalog["movies"][key].get("tmdb_id"):
        existing = catalog["movies"][key]
        print(f"[tmdb] Already in catalog: {existing.get('title')} -> ID {existing.get('tmdb_id')}")
        return existing

    media_type = detect_media_type(cleaned, page_url)
    result = search_tmdb(query, year=year, api_key=api_key, media_type=media_type)
    return add_movie_to_catalog(catalog, result, source_title=title, page_url=page_url)


def process_page_url(page_url: str, catalog: dict, api_key: str = "") -> dict:
    print(f"[tmdb] Extracting title from page: {page_url[:90]}")
    title = extract_title_from_page(page_url)
    print(f"[tmdb] Page title: {title}")
    return process_title(title, catalog, page_url=page_url, api_key=api_key)


def run_tmdb_bot(
    titles: list[str] | None = None,
    page_urls: list[str] | None = None,
    api_key: str = "",
) -> dict:
    catalog = load_catalog()
    results = []

    for url in page_urls or []:
        try:
            results.append(process_page_url(url.strip(), catalog, api_key=api_key))
        except Exception as e:
            err = {"success": False, "page_url": url, "error": str(e)}
            results.append(err)
            print(f"[tmdb] Page error: {e}")

    for title in titles or []:
        try:
            results.append(process_title(title.strip(), catalog, api_key=api_key))
        except Exception as e:
            err = {"success": False, "source_title": title, "error": str(e)}
            results.append(err)
            print(f"[tmdb] Title error: {e}")

    save_catalog(catalog)

    summary = {
        "total": len(results),
        "found": sum(1 for r in results if r.get("tmdb_id")),
        "failed": sum(1 for r in results if not r.get("tmdb_id")),
        "results": results,
    }

    with open("tmdb_search_result.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\n[tmdb] Done: {summary['found']}/{summary['total']} found")
    print(f"[tmdb] Results -> tmdb_search_result.json")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Search TMDB IDs for movie titles")
    parser.add_argument("titles", nargs="*", help="Movie titles to search")
    parser.add_argument("--page-url", action="append", default=[], help="Watch page URL (extract title)")
    parser.add_argument("--file", help="Text file with one title or URL per line")
    parser.add_argument("--api-key", default=get_tmdb_api_key(), help="TMDB API key")
    args = parser.parse_args()

    titles = list(args.titles)
    page_urls = list(args.page_url)

    env_titles = os.environ.get("MOVIE_TITLES", "").strip()
    if env_titles:
        titles.extend([t.strip() for t in env_titles.split(",") if t.strip()])

    env_page = os.environ.get("PAGE_URL", "").strip()
    if env_page:
        page_urls.append(env_page)

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("http"):
                    page_urls.append(line)
                else:
                    titles.append(line)

    if not titles and not page_urls:
        print("Error: provide titles, --page-url, --file, MOVIE_TITLES, or PAGE_URL")
        sys.exit(1)

    summary = run_tmdb_bot(titles=titles, page_urls=page_urls, api_key=args.api_key)
    sys.exit(0 if summary["found"] > 0 else 1)


if __name__ == "__main__":
    main()
