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


def find_tmdb_id_by_title(title: str) -> int | None:
    """Search for tmdb_id by movie title in the catalog."""
    if not title:
        return None
    
    catalog = load_catalog()
    movies = catalog.get("movies", {})
    title_normalized = normalize_key(title)
    
    # Direct match
    if title_normalized in movies:
        return movies[title_normalized].get("tmdb_id")
    
    # Fuzzy match by title/source_title
    for key, entry in movies.items():
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
        
        # Pattern 3: just slug before year anywhere in URL
        fallback_match = re.search(r'/([a-z0-9-]+)-(\d{4})', url)
        if fallback_match:
            slug = fallback_match.group(1)
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


def search_tmdb_api(title: str, year: str = "") -> int | None:
    """Search TMDB API for movie ID by title and year."""
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
        
        # Clean title for search
        clean_title = re.sub(r'\b(19|20)\d{2}\b', '', title).strip()
        clean_title = re.sub(r'\s+', ' ', clean_title)
        
        # Search TMDB
        url = "https://api.themoviedb.org/3/search/movie"
        params = {
            "api_key": api_key,
            "query": clean_title,
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
            print(f"[tmdb] Found TMDB ID: {tmdb_id} for '{clean_title}' ({year})")
            return tmdb_id
        
        print(f"[tmdb] No results found for '{clean_title}' ({year})")
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


def get_entry_by_page(page_url: str) -> dict | None:
    catalog = load_catalog()
    key = _find_catalog_key(catalog, page_url=page_url)
    if key:
        return catalog["movies"][key]
    return None


def save_to_supabase(tmdb_id: int, title: str, doodstream_url: str, doodstream_download_url: str) -> bool:
    """Save movie data to Supabase database."""
    if not supabase:
        print("[supabase] Not connected, skipping database save")
        return False
    
    try:
        data = {
            "tmdb_id": tmdb_id,
            "title": title,
            "doodstream_url": doodstream_url,
            "doodstream_download_url": doodstream_download_url,
        }
        
        # Check if movie already exists
        existing = supabase.table("movies").select("*").eq("tmdb_id", tmdb_id).execute()
        
        if existing.data:
            # Update existing record
            supabase.table("movies").update(data).eq("tmdb_id", tmdb_id).execute()
            print(f"[supabase] Updated movie: {title} (tmdb_id: {tmdb_id})")
        else:
            # Insert new record
            supabase.table("movies").insert(data).execute()
            print(f"[supabase] Inserted movie: {title} (tmdb_id: {tmdb_id})")
        
        return True
    except Exception as e:
        print(f"[supabase] Error saving to database: {e}")
        return False
