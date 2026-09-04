"""Shared catalog for TMDB IDs and DoodStream URLs per page/movie."""

import json
import os
import re
import time
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Initialize Supabase client
supabase: Client | None = None
try:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if supabase_url and supabase_key:
        supabase = create_client(supabase_url, supabase_key)
        print("[supabase] Connected to Supabase")
except Exception as e:
    print(f"[supabase] Failed to connect: {e}")

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
                if "tv_episodes" not in data:
                    data["tv_episodes"] = {}
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"movies": {}, "tv_episodes": {}, "last_updated": None}


def save_catalog(catalog: dict):
    catalog["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)


def _find_catalog_key(catalog: dict, page_url: str = "", title: str = "", media_type: str = "movie") -> str | None:
    """Find existing entry key by page URL or title."""
    # Search in the appropriate section based on media_type
    if media_type == "tv":
        entries = catalog.get("tv_episodes", {})
    else:
        entries = catalog.get("movies", {})
    
    if page_url:
        page_key = normalize_page_key(page_url)
        if page_key in entries:
            return page_key
        for key, entry in entries.items():
            if normalize_page_key(entry.get("page_url", "")) == page_key:
                return key
    if title:
        title_key = normalize_key(title)
        if title_key in entries:
            return title_key
        for key, entry in entries.items():
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
    media_type: str = "movie",
    season: int | None = None,
    episode: int | None = None,
    series_name: str = "",
) -> dict:
    """Add or update DoodStream URLs for a page/movie/episode in the shared catalog."""
    if not filecode:
        return {}

    catalog = load_catalog()
    
    # Choose the right section based on media_type
    if media_type == "tv":
        entries = catalog.setdefault("tv_episodes", {})
    else:
        entries = catalog.setdefault("movies", {})

    key = _find_catalog_key(catalog, page_url, title, media_type)
    if not key:
        key = normalize_page_key(page_url) if page_url else normalize_key(title or filecode)

    entry = entries.get(key, {})
    entry.update({
        "success": True,
        "filecode": filecode,
        "doodstream_url": f"https://doodstream.com/e/{filecode}",
        "playmogo_url": f"https://playmogo.com/e/{filecode}",
        "doodstream_download_url": f"https://playmogo.com/d/{filecode}",
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "media_type": media_type,
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
        entry["vidsrc_url"] = f"https://vidsrc.sbs/embed/{media_type}/{tmdb_id}"
    
    # TV episode specific fields
    if media_type == "tv":
        if season is not None:
            entry["season"] = season
        if episode is not None:
            entry["episode"] = episode
        if series_name:
            entry["series_name"] = series_name

    entries[key] = entry
    save_catalog(catalog)
    print(f"[tmdb] Catalog saved to {CATALOG_FILE}")
    print(f"[catalog] Saved DoodStream URL for: {entry.get('title') or page_url or key}")
    print(f"[catalog] -> {entry['doodstream_url']}")
    return entry


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


# Known series where a number before "E" (or in the title) is NOT a season number.
# Format: {series name keyword: (tmdb_id, season_for_eps)}
#   season=None -> resolved live via TMDB (find_season_for_episode)
KNOWN_EPISODE_OVERRIDES = {
    # Turkish drama "Daha" (Torn Apart) — "Daha 17 E14" the 17 is part of the show name,
    # not a season; TMDB has only season 1.
    "daha": (317883, 1),
    # One Piece uses GLOBAL episode numbers inside TMDB's arc-based seasons (e.g. E1176 = S23E1176).
    "one piece": (37854, None),
    "mushoku tensei": (94664, None),
    "its always sunny": (2710, None),
    "black trick": (322852, 1),
    "bai ri cheng wang": (326844, 1),
    "four hands two sonatas": (305644, 1),
    "four hands, two sonatas": (305644, 1),
    "alti ustu istanbul": (321928, 1),
    "until the t-shirt dries": (322570, 1),
    "until the t shirt dries": (322570, 1),
    # Arabic "صحوة Awaken 2026" -> Chinese drama "醒来 / Awaken" (S1), NOT tmdb 1254074.
    "صحوة": (289761, 1),
    "awaken": (289761, 1),
}


def _known_override(cleaned_title: str):
    """Return (tmdb_id, season) for known series, else (None, None)."""
    base_name = normalize_key(cleaned_title)
    for known, (known_tmdb, known_season) in KNOWN_EPISODE_OVERRIDES.items():
        if known in base_name:
            return known_tmdb, known_season
    return None, None

# Arabic season word -> number
ARABIC_SEASON_PATTERNS = {
    "الموسم-الأول": 1, "الموسم-الاول": 1, "الموسم-1": 1,
    "الموسم-الثاني": 2, "الموسم-التاني": 2, "الموسم-2": 2,
    "الموسم-الثالث": 3, "الموسم-3": 3,
    "الموسم-الرابع": 4, "الموسم-4": 4,
    "الموسم-الخامس": 5, "الموسم-5": 5,
    "الموسم-السادس": 6, "الموسم-6": 6,
    "الموسم-السابع": 7, "الموسم-7": 7,
    "الموسم-الثامن": 8, "الموسم-8": 8,
    "الموسم-التاسع": 9, "الموسم-9": 9,
    "الموسم-العاشر": 10, "الموسم-10": 10,
}


def find_season_for_episode(tmdb_id: int, episode: int, api_key: str = "") -> int | None:
    """Find which TMDB season contains a given global episode number.

    Used for shows like One Piece where episode numbers are global
    (E1176 lives in the arc-based season 23 as episode 1176).
    """
    api_key = api_key or os.environ.get("TMDB_API_KEY")
    if not api_key:
        return None
    try:
        url = f"https://api.themoviedb.org/3/tv/{tmdb_id}"
        resp = requests.get(url, params={"api_key": api_key, "language": "en-US"}, timeout=10)
        data = resp.json()
        for season in data.get("seasons", []):
            sn = season.get("season_number")
            if sn in (0,):
                continue
            eps = requests.get(
                f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{sn}",
                params={"api_key": api_key, "language": "en-US"}, timeout=10,
            ).json()
            nums = [e.get("episode_number") for e in eps.get("episodes", [])]
            if nums and min(nums) <= episode <= max(nums):
                return sn
    except Exception as e:
        print(f"[catalog] find_season_for_episode error: {e}")
    return None


def extract_episode_info(title: str) -> dict:
    """Extract season and episode numbers from title.

    Examples:
    "One Piece E1176" -> {"season": None, "episode": 1176, "media_type": "tv"}
    "Mushoku Tensei S03E10" -> {"season": 3, "episode": 10, "media_type": "tv"}
    "Daha 17 E14" -> {"season": 17, "episode": 14, "media_type": "tv"}
    "مشاهدة مسلسل وتحميل صحوة Awaken 2026 الحلقة 14 مترجمة" -> {"season": None, "episode": 14, "media_type": "tv"}
    "Icefall 2025" -> {"season": None, "episode": None, "media_type": "movie"}

    Returns:
        dict with keys: season, episode, media_type, cleaned_title
    """
    if not title:
        return {"season": None, "episode": None, "media_type": "movie", "cleaned_title": ""}

    # Pattern 0: Arabic episode markers (الحلقة / حلقة / الموسم)
    arabic_ep_match = re.search(r'الحلقة[-\s]*(\d+)|حلقة[-\s]*(\d+)', title)
    if arabic_ep_match:
        episode = int(arabic_ep_match.group(1) or arabic_ep_match.group(2))
        season = None
        for pattern, num in ARABIC_SEASON_PATTERNS.items():
            if pattern in title:
                season = num
                break
        cleaned_title = re.sub(
            r'الموسم[-\s]*(?:الأول|الاول|الثاني|التاني|الثالث|الرابع|الخامس|السادس|السابع|الثامن|التاسع|العاشر|\d+)|الحلقة[-\s]*\d+|حلقة[-\s]*\d+|مشاهدة[-\s]*مسلسل[-\s]*وتحميل|مشاهدة[-\s]*مسلسل|مترجمة|مترجم|مباشر',
            '', title
        )
        cleaned_title = re.sub(r'\s+', ' ', cleaned_title).strip(' -')
        hint_tmdb, hint_season = _known_override(cleaned_title)
        if hint_tmdb and hint_season is not None:
            season = hint_season
        return {
            "season": season,
            "episode": episode,
            "media_type": "tv",
            "cleaned_title": cleaned_title,
            "tmdb_id_hint": hint_tmdb,
        }

    # Pattern 1b: glued resolution like "S04E051080p" -> S4 E05
    alt_s_e_match = re.search(r'[Ss](\d+)[Ee](\d+?)\s*(?:1080p?|720p?|2160p?|4k)(?![0-9])', title, re.IGNORECASE)
    if alt_s_e_match:
        season = int(alt_s_e_match.group(1))
        episode = int(alt_s_e_match.group(2))
        cleaned_title = re.sub(r'\b[Ss]\d+[Ee]\d+?\s*(?:1080p?|720p?|2160p?|4k)(?![0-9])', '', title, flags=re.IGNORECASE)
        cleaned_title = re.sub(r'\s+', ' ', cleaned_title).strip()
        hint_tmdb, hint_season = _known_override(cleaned_title)
        return {
            "season": season,
            "episode": episode,
            "media_type": "tv",
            "cleaned_title": cleaned_title,
            "tmdb_id_hint": hint_tmdb,
        }

    # Pattern 1: S##E## (e.g., S03E10)
    s_e_match = re.search(r'[Ss](\d+)[Ee](\d+)', title)
    if s_e_match:
        season = int(s_e_match.group(1))
        episode = int(s_e_match.group(2))
        # Guard: glued resolution made the episode absurdly large (e.g. "E051080p")
        if episode > 2000 and re.search(r'[Ee]\d+(?:1080|720|2160)', title, re.IGNORECASE):
            alt2 = re.search(r'[Ee](\d+?)(?:1080|720|2160)', title, re.IGNORECASE)
            if alt2:
                episode = int(alt2.group(1))
        cleaned_title = re.sub(r'\b[Ss]\d+[Ee]\d+\b', '', title)
        cleaned_title = re.sub(r'\s+', ' ', cleaned_title).strip()
        hint_tmdb, hint_season = _known_override(cleaned_title)
        return {
            "season": season,
            "episode": episode,
            "media_type": "tv",
            "cleaned_title": cleaned_title,
            "tmdb_id_hint": hint_tmdb,
        }

    # Pattern 2: E### (e.g., E1176)
    e_match = re.search(r'[Ee](\d+)', title)
    if e_match:
        episode = int(e_match.group(1))
        cleaned_title = re.sub(r'\b[Ee]\d+\b', '', title)
        cleaned_title = re.sub(r'\s+', ' ', cleaned_title).strip()

        hint_tmdb, hint_season = _known_override(cleaned_title)
        return {
            "season": hint_season,
            "episode": episode,
            "media_type": "tv",
            "cleaned_title": cleaned_title,
            "tmdb_id_hint": hint_tmdb,
        }

    # No episode pattern found, treat as movie
    return {
        "season": None,
        "episode": None,
        "media_type": "movie",
        "cleaned_title": title
    }


def find_tmdb_id_by_title(title: str) -> int | None:
    """Search for tmdb_id by movie/series title in the catalog."""
    if not title:
        return None
    
    catalog = load_catalog()
    title_normalized = normalize_key(title)
    
    # Search in movies first
    movies = catalog.get("movies", {})
    if title_normalized in movies:
        return movies[title_normalized].get("tmdb_id")
    
    for key, entry in movies.items():
        entry_title = normalize_key(entry.get("title") or entry.get("source_title", ""))
        if entry_title and entry_title == title_normalized:
            return entry.get("tmdb_id")
    
    # Search in tv_episodes
    tv_episodes = catalog.get("tv_episodes", {})
    if title_normalized in tv_episodes:
        return tv_episodes[title_normalized].get("tmdb_id")
    
    for key, entry in tv_episodes.items():
        entry_title = normalize_key(entry.get("title") or entry.get("source_title", ""))
        if entry_title and entry_title == title_normalized:
            return entry.get("tmdb_id")
    
    return None


def extract_cima4u_info(url: str) -> tuple[str | None, str | None]:
    """Extract slug and year from Cima4u URL.
    
    Examples:
    https://cimafu.cam/مشاهدة-فيلم-وتحميل-hippos-revenge-2025-مترجم-مباشر/watch/
    https://cimafu.cam/مشاهدة-مشاهدة-فيلم-وتحميل-mutiny-2026-مترجم/
    Returns: ("hippos-revenge", "2025") or ("mutiny", "2026")
    """
    try:
        # Decode URL if it's percent-encoded
        from urllib.parse import unquote
        url = unquote(url)
        
        # Extract slug (movie name) from URL
        # Pattern 1: .../slug-year-.../watch/
        slug_match = re.search(r'/([a-z0-9-]+)-(\d{4})-', url)
        if slug_match:
            slug = slug_match.group(1)
            year = slug_match.group(2)
            print(f"[cima4u] Extracted slug: {slug}, year: {year}")
            return slug, year
        
        # Pattern 2: .../slug-year/ (without /watch/ at end)
        alt_match = re.search(r'/([a-z0-9-]+)-(\d{4})/?$', url)
        if alt_match:
            slug = alt_match.group(1)
            year = alt_match.group(2)
            print(f"[cima4u] Extracted slug: {slug}, year: {year}")
            return slug, year
        
        # Pattern 3: Handle Arabic text before slug (e.g., "مشاهدة-فيلم-mutiny-2026")
        # Match any lowercase letters followed by dash and year
        fallback_match = re.search(r'/.*?([a-z0-9-]+)-(\d{4})', url)
        if fallback_match:
            slug = fallback_match.group(1).lstrip('-')  # Remove leading dashes
            year = fallback_match.group(2)
            print(f"[cima4u] Extracted slug (fallback): {slug}, year: {year}")
            return slug, year
        
        print(f"[cima4u] Could not extract slug/year from URL: {url}")
        return None, None
        
    except Exception as e:
        print(f"[cima4u] Error extracting info: {e}")
        return None, None


def search_tmdb_by_slug(slug: str, year: str) -> int | None:
    """Search TMDB API for movie ID by slug and year.
    
    Args:
        slug: Movie slug (e.g., "hippos-revenge")
        year: Release year (e.g., "2025")
    
    Returns:
        TMDB ID if found, None otherwise
    """
    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        print("[tmdb] TMDB_API_KEY not found, skipping API search")
        return None
    
    if not slug:
        return None
    
    try:
        # Convert slug to search query (replace hyphens with spaces)
        query = slug.replace("-", " ")
        
        # Search TMDB
        url = "https://api.themoviedb.org/3/search/movie"
        params = {
            "api_key": api_key,
            "query": query,
            "language": "en-US"
        }
        if year:
            params["year"] = year
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data.get("results"):
            # Return first result's ID
            first_result = data["results"][0]
            tmdb_id = first_result.get("id")
            print(f"[tmdb] Found TMDB ID: {tmdb_id} for '{query}' ({year})")
            return tmdb_id
        
        print(f"[tmdb] No results found for '{query}' ({year})")
        return None
        
    except Exception as e:
        print(f"[tmdb] Error searching TMDB API: {e}")
        return None


def _clean_search_title(title: str) -> str:
    """Strip release/quality noise from a title before querying TMDB."""
    if not title:
        return ""
    noisy = re.compile(
        r'\b(?:'
        r'1080p|1080|720p|720|480p|2160p|4k|4kuhd|uhd|hdr|bluray|blu-ray|'
        r'brrip|bdrip|web-dl|webdl|webrip|web|hdtv|hdtvrip|hdrip|dvdrip|'
        r'x264|x265|hevc|aac|ac3|dts|'
        r'nf|netflix|prime|amzn|atvp|aapple|hulu|disney|disneyplus|\btv\b|'
        r'egydead|egybest|eg01|mycima|cima4u|cimafu|movizland|mkvmovies|'
        r'mkv|mp4|avi|complete|season|series|multi|vosten|subbed|[Ss]\d+[Ee]\d+|[Ee]\d{2,4}|'
        r'com|wmv'
        r')\b|\.|,|\(|\)',
        re.IGNORECASE,
    )
    clean = noisy.sub(' ', title)
    clean = re.sub(r'\b(?:19|20)\d{2}\b', ' ', clean)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()
    return clean[:80]


def search_tmdb_api(title: str, year: str = "", media_type: str = "movie", season: int = None, episode: int = None) -> int | None:
    """Search TMDB API for movie or TV ID by title and year."""
    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        print("[tmdb] TMDB_API_KEY not found, skipping API search")
        return None
    
    if not title:
        return None
    
    try:
        # Extract year from title if not provided
        if not year:
            year_match = re.search(r'\b(19|20)\d{2}\b', title)
            if year_match:
                year = year_match.group()
        
        # Clean title for search (drop quality tags / host names / punctuation)
        clean_title = _clean_search_title(title)
        if not clean_title:
            return None
        
        # Search TMDB based on media type
        if media_type == "tv":
            url = "https://api.themoviedb.org/3/search/tv"
        else:
            url = "https://api.themoviedb.org/3/search/movie"
        
        params = {
            "api_key": api_key,
            "query": clean_title,
            "language": "en-US"
        }
        if year:
            params["first_air_date_year" if media_type == "tv" else "year"] = year
        
        def _do_search(q: str, y: str):
            p = dict(params)
            p["query"] = q
            if y:
                p["first_air_date_year" if media_type == "tv" else "year"] = y
            return requests.get(url, params=p, timeout=10).json()
        
        data = _do_search(clean_title, year)
        results = data.get("results") or []
        
        # Fallback: shorten until a likely word boundary if nothing found
        if not results and clean_title != title:
            data = _do_search(title[:60], year)
            results = data.get("results") or []
        
        if results:
            # Return first result's ID
            first_result = results[0]
            tmdb_id = first_result.get("id")
            print(f"[tmdb] Found TMDB ID: {tmdb_id} for '{clean_title}' ({media_type}, {year})")
            if media_type == "tv" and season is not None and episode is not None:
                print(f"[tmdb] Season: {season}, Episode: {episode}")
            return tmdb_id
        
        print(f"[tmdb] No results found for '{clean_title}' ({media_type}, {year})")
        return None
        
    except Exception as e:
        print(f"[tmdb] Error searching TMDB API: {e}")
        return None


def import_from_doodstream_account(api_key: str) -> dict:
    """Import all videos from DoodStream account and update catalog with tmdb_id matches."""
    import requests
    
    if not api_key:
        print("[import] Error: DoodStream API key required")
        return {"success": False, "error": "No API key"}
    
    print("[import] Fetching videos from DoodStream account...")
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
            print(f"[import] Error fetching page {page}: {e}")
            break
    
    print(f"[import] Found {len(files)} videos in account")
    
    catalog = load_catalog()
    movies = catalog.setdefault("movies", {})
    updated_count = 0
    
    for file_info in files:
        filecode = file_info.get("file_code") or file_info.get("filecode")
        title = file_info.get("title", "")
        
        if not filecode:
            continue
        
        # Try to find tmdb_id by title
        tmdb_id = find_tmdb_id_by_title(title)
        
        # Find existing entry by filecode or create new key
        existing_key = None
        for key, entry in movies.items():
            if entry.get("filecode") == filecode:
                existing_key = key
                break
        
        # Use title as key if no existing entry
        if not existing_key:
            existing_key = normalize_key(title) if title else filecode
        
        entry = movies.get(existing_key, {})
        entry.update({
            "success": True,
            "filecode": filecode,
            "doodstream_url": f"https://doodstream.com/e/{filecode}",
            "playmogo_url": f"https://playmogo.com/e/{filecode}",
            "doodstream_download_url": f"https://playmogo.com/d/{filecode}",
            "title": title or entry.get("title", ""),
            "source_title": title or entry.get("source_title", ""),
            "uploaded_at": file_info.get("upload_date") or entry.get("uploaded_at", time.strftime("%Y-%m-%d %H:%M:%S")),
        })
        
        if tmdb_id:
            entry["tmdb_id"] = tmdb_id
            media_type = entry.get("type", "movie")
            entry["vidsrc_url"] = f"https://vidsrc.sbs/embed/{media_type}/{tmdb_id}"
            print(f"[import] Matched: '{title}' -> tmdb_id={tmdb_id}")
            updated_count += 1
        else:
            print(f"[import] No match: '{title}' (filecode: {filecode})")
        
        movies[existing_key] = entry
    
    save_catalog(catalog)
    print(f"[import] Catalog updated. Total entries: {len(movies)}, Matched with tmdb_id: {updated_count}")
    
    return {
        "success": True,
        "total_files": len(files),
        "updated_entries": updated_count,
        "catalog_entries": len(movies),
    }


def get_entry_by_page(page_url: str, media_type: str = "movie") -> dict | None:
    """Get catalog entry by page URL."""
    catalog = load_catalog()
    key = _find_catalog_key(catalog, page_url=page_url, media_type=media_type)
    if key:
        if media_type == "tv":
            return catalog["tv_episodes"].get(key)
        return catalog["movies"].get(key)
    return None


def save_movie_to_supabase(tmdb_id: int, title: str, doodstream_url: str, 
                           doodstream_download_url: str, year: int = None) -> bool:
    """Save movie data to Supabase movies table."""
    if not supabase:
        print("[supabase] Not connected, skipping database save")
        return False
    
    try:
        data = {
            "tmdb_id": tmdb_id,
            "title": title,
            "doodstream_url": doodstream_url,
            "doodstream_download_url": doodstream_download_url,
            "media_type": "movie",
        }
        if year:
            data["year"] = year
        
        # Check if record already exists
        existing = supabase.table("movies").select("*").eq("tmdb_id", tmdb_id).execute()
        
        if existing.data:
            supabase.table("movies").update(data).eq("tmdb_id", tmdb_id).execute()
            print(f"[supabase] Updated movie: {title} (tmdb_id: {tmdb_id})")
        else:
            supabase.table("movies").insert(data).execute()
            print(f"[supabase] Inserted movie: {title} (tmdb_id: {tmdb_id})")
        
        return True
    except Exception as e:
        print(f"[supabase] Error saving movie: {e}")
        return False


def save_episode_to_supabase(tmdb_id: int, series_title: str, season: int, episode: int,
                             doodstream_url: str, doodstream_download_url: str, 
                             title: str = None) -> bool:
    """Save TV episode data to Supabase.

    The live Supabase 'movies' table stores episodes using
    media_type='tv' + season_number + episode_number columns.
    """
    if not supabase:
        print("[supabase] Not connected, skipping database save")
        return False
    
    try:
        data = {
            "tmdb_id": tmdb_id,
            "title": title or series_title,
            "doodstream_url": doodstream_url,
            "doodstream_download_url": doodstream_download_url,
            "media_type": "tv",
            "season_number": season,
            "episode_number": episode,
        }
        
        # Check if record already exists (by tmdb_id, season, episode)
        existing = supabase.table("movies").select("*").eq("tmdb_id", tmdb_id).eq("media_type", "tv").eq("season_number", season).eq("episode_number", episode).execute()
        
        if existing.data:
            supabase.table("movies").update(data).eq("tmdb_id", tmdb_id).eq("media_type", "tv").eq("season_number", season).eq("episode_number", episode).execute()
            print(f"[supabase] Updated episode: {series_title} S{season}E{episode}")
        else:
            supabase.table("movies").insert(data).execute()
            print(f"[supabase] Inserted episode into movies (tv): {series_title} S{season}E{episode}")
        
        return True
    except Exception as e:
        msg = str(e)
        if "Could not find the table" in msg or ("relation" in msg and "does not exist" in msg):
            print(f"[supabase] ❌ TV table missing! Run create_supabase_table.sql in the Supabase SQL Editor. tmdb_id={tmdb_id} S{season}E{episode}")
        else:
            print(f"[supabase] Error saving episode: {e}")
        return False


def save_to_supabase(tmdb_id: int, title: str, doodstream_url: str, doodstream_download_url: str, 
                    media_type: str = "movie", season: int = None, episode: int = None) -> bool:
    """Save movie or TV episode data to Supabase database (backward compatible)."""
    if media_type == "tv":
        if season is not None and episode is not None:
            return save_episode_to_supabase(tmdb_id, title, season, episode, doodstream_url, doodstream_download_url)
        print(f"[supabase] ⚠ TV episode skipped (missing season/episode for tmdb_id={tmdb_id}, title={title!r}); NOT saving as movie")
        return False
    else:
        return save_movie_to_supabase(tmdb_id, title, doodstream_url, doodstream_download_url)
