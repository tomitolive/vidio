#!/usr/bin/env python3
"""
إصلاح كل الـ tmdb_id المزورة (8xxxxxx / 9xxxxxx) في Supabase
باستخدام التنظيف والبحث المحسّنين في catalog.py.

- يقرأ كل الصفوف المزورة من جدولي movies و tv_episodes
- يستعمل filecode لجلب العنوان الحقيقي من DoodStream إن وُجد
- يحدد النوع (movie/tv) من العنوان وليس من الجدول فقط
- يبحث في TMDB ويحدّث الـ tmdb_id الصحيح
- اللي ما يجموش نتلاقا يكتبهم في data.json (يبقى الـ fake id كما هو)

Usage:
    python3 repair_fake_tmdb_ids.py                   # إصلاح كامل
    python3 repair_fake_tmdb_ids.py --dry-run         # بدون كتابة
    python3 repair_fake_tmdb_ids.py --limit 50        # عينة فقط
"""

import os
import sys
import json
import time
import argparse
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catalog import _clean_search_title, extract_episode_info

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
DOOD_API_KEY = os.environ.get("DOODSTREAM_API_KEY", "")
USE_DOODSTREAM = False  # عنوان DoodStream اختياري (يتطلب شبكة مستقرة)

MOVIE_FAKE_MIN = 8_000_000
TV_FAKE_MIN = 9_000_000

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# caches to avoid repeated API calls
_search_cache: dict[str, int | None] = {}
_name_cache: dict[int, str] = {}
_dood_title_cache: dict[str, str | None] = {}


def tmdb_search(query: str, media_type: str) -> int | None:
    """Search TMDB with caching and rate-limit friendly requests."""
    if not query:
        return None
    key = f"{media_type}|{query.strip().lower()}"
    if key in _search_cache:
        return _search_cache[key]
    url = "https://api.themoviedb.org/3/search/tv" if media_type == "tv" \
        else "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": query, "language": "en-US"}
    found = None
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=12)
            if resp.status_code == 429:
                time.sleep(2 + attempt)
                continue
            data = resp.json()
            results = data.get("results") or []
            if results:
                from catalog import _pick_best_tmdb_result
                found = _pick_best_tmdb_result(results, query, "")
            break
        except Exception as e:
            time.sleep(1 + attempt)
            if attempt == 2:
                print(f"    [tmdb] error({media_type}) {query[:40]}: {str(e)[:70]}")
    _search_cache[key] = found
    return found


def tmdb_name(tmdb_id: int, media_type: str) -> str:
    if tmdb_id in _name_cache:
        return _name_cache[tmdb_id]
    try:
        url = f"https://api.themoviedb.org/3/{'tv' if media_type == 'tv' else 'movie'}/{tmdb_id}"
        r = requests.get(url, params={"api_key": TMDB_API_KEY, "language": "en-US"}, timeout=12)
        name = r.json().get("name") or r.json().get("title") or "?"
        _name_cache[tmdb_id] = name
        return name
    except Exception:
        return "?"


def doodstream_title(filecode: str) -> str | None:
    if not filecode or not DOOD_API_KEY or not USE_DOODSTREAM:
        return None
    if filecode in _dood_title_cache:
        return _dood_title_cache[filecode]
    try:
        resp = requests.get(
            f"https://doodapi.com/api/file/info?key={DOOD_API_KEY}&file_code={filecode}",
            timeout=4,
        )
        data = resp.json()
        if data.get("status") == 200 and data.get("result"):
            res = data["result"]
            row = res[0] if isinstance(res, list) and res else res
            title = (row or {}).get("title") or None
            _dood_title_cache[filecode] = title
            return title
    except Exception:
        pass
    _dood_title_cache[filecode] = None
    return None


def resolve_title(title: str, filecode: str) -> tuple[str, str | None]:
    """Return (best_raw_title, effective_title_for_search)."""
    ds = doodstream_title(filecode) if filecode else None
    src = ds or title
    info = extract_episode_info(src)
    cleaned = _clean_search_title(info["cleaned_title"] or src)
    return src, cleaned


def repair_movie(row: dict, dry: bool) -> dict:
    rid = row["id"]
    old = row["tmdb_id"]
    title = row.get("title") or ""
    fc = ""
    if row.get("doodstream_url"):
        fc = row["doodstream_url"].rsplit("/", 1)[-1]
    info = extract_episode_info(title)
    mtype = info["media_type"]  # detect from title
    season = row.get("season_number")
    episode = row.get("episode_number")
    if season is None:
        season = info["season"]
    if episode is None:
        episode = info["episode"]

    src, cleaned = resolve_title(title, fc)
    found = tmdb_search(cleaned, mtype)
    tried_other = False
    if not found and mtype == "movie":
        found2 = tmdb_search(cleaned, "tv")
        if found2:
            found, mtype, tried_other = found2, "tv", True
    elif not found and mtype == "tv":
        found2 = tmdb_search(cleaned, "movie")
        if found2:
            found, mtype, tried_other = found2, "movie", True

    status = "found" if found else "unresolved"
    if status == "found" and not dry:
        upd = {"tmdb_id": found}
        if mtype == "tv":
            upd["media_type"] = "tv"
            if season is not None:
                upd["season_number"] = season
            if episode is not None:
                upd["episode_number"] = episode
        try:
            supabase.table("movies").update(upd).eq("id", rid).execute()
        except Exception as e:
            status = f"dberr:{str(e)[:60]}"

    return {
        "table": "movies",
        "id": rid,
        "old_tmdb_id": old,
        "new_tmdb_id": found,
        "media_type": mtype,
        "season": season,
        "episode": episode,
        "filecode": fc,
        "title": title,
        "source_title": src,
        "query": cleaned,
        "name": tmdb_name(found, mtype) if found else None,
        "attempted_other_type": tried_other,
        "status": status,
    }


def repair_tv(row: dict, dry: bool) -> dict:
    rid = row["id"]
    old = row["tmdb_id"]
    title = row.get("series_title") or row.get("title") or ""
    fc = row.get("filecode") or ""
    season = row.get("season_number")
    episode = row.get("episode_number")

    src, cleaned = resolve_title(title, fc)
    found = tmdb_search(cleaned, "tv")
    found_movie = None
    tried = False
    if not found:
        found_movie = tmdb_search(cleaned, "movie")
        if found_movie:
            found, tried = found_movie, True
    else:
        tried = False

    status = "found" if found else "unresolved"
    if status == "found" and not dry:
        upd = {"tmdb_id": found}
        try:
            supabase.table("tv_episodes").update(upd).eq("id", rid).execute()
        except Exception as e:
            status = f"dberr:{str(e)[:60]}"

    return {
        "table": "tv_episodes",
        "id": rid,
        "old_tmdb_id": old,
        "new_tmdb_id": found,
        "media_type": "tv",
        "season": season,
        "episode": episode,
        "filecode": fc,
        "title": title,
        "source_title": src,
        "query": cleaned,
        "name": tmdb_name(found, "tv") if found else None,
        "attempted_other_type": tried,
        "status": status,
    }


def fetch_fake_rows(table: str, col: str, min_id: int) -> list[dict]:
    """Fetch all fake-tmdb rows, paginating defensively."""
    rows: list[dict] = []
    off = 0
    while True:
        batch = (
            supabase.table(table)
            .select("*")
            .gte("tmdb_id", min_id)
            .order("id")
            .range(off, off + 999)
            .execute()
            .data
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        off += 1000
    return rows


def main():
    global DOOD_API_KEY, USE_DOODSTREAM
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--api-key", default=DOOD_API_KEY)
    ap.add_argument("--with-doodstream", action="store_true",
                    help="جلب العنوان من DoodStream API (أبطأ، شبكة غير مستقرة أحياناً)")
    args = ap.parse_args()

    if args.api_key:
        DOOD_API_KEY = args.api_key
    if args.with_doodstream:
        USE_DOODSTREAM = True

    if not (SUPABASE_URL and SUPABASE_KEY):
        print("✗ SUPABASE_URL / SUPABASE_KEY غير موجودة")
        sys.exit(1)
    if not TMDB_API_KEY:
        print("✗ TMDB_API_KEY غير موجودة")
        sys.exit(1)

    print(f"{'DRY-RUN ' if args.dry_run else ''}إصلاح الـ tmdb_id المزورة في Supabase")
    print("=" * 80)

    movie_rows = fetch_fake_rows("movies", "title", MOVIE_FAKE_MIN)
    tv_rows = fetch_fake_rows("tv_episodes", "series_title", TV_FAKE_MIN)
    if args.limit:
        movie_rows = movie_rows[: max(1, args.limit)]
        tv_rows = tv_rows[: max(1, args.limit)]
    print(f"صفوف مزورة: movies={len(movie_rows)}  tv_episodes={len(tv_rows)}")

    results: list[dict] = []
    counters = {"found": 0, "unresolved": 0, "dberr": 0}

    def handle(res: dict):
        st = res["status"]
        if st == "found":
            counters["found"] += 1
        elif st == "unresolved":
            counters["unresolved"] += 1
        else:
            counters["dberr"] += 1
        results.append(res)

    t0 = time.time()
    for i, row in enumerate(movie_rows, 1):
        handle(repair_movie(row, args.dry_run))
        if i % 10 == 0:
            print(f"  ... movies {i}/{len(movie_rows)}  ({(time.time()-t0):.0f}s)")
    for i, row in enumerate(tv_rows, 1):
        handle(repair_tv(row, args.dry_run))
        if i % 10 == 0:
            print(f"  ... tv {i}/{len(tv_rows)}  ({(time.time()-t0):.0f}s)")

    print("=" * 80)
    print(f"تم عثور على tmdb_id صحيح: {counters['found']}")
    print(f"لم يُعثر على تطابق : {counters['unresolved']}")
    if counters["dberr"]:
        print(f"أخطاء قاعدة بيانات  : {counters['dberr']}")

    fixed = [r for r in results if r["status"] == "found"]
    unresolved = [r for r in results if r["status"] == "unresolved"]

    if fixed:
        print("\nنماذج أمثلة (قبل → بعد):")
        for r in fixed[:8]:
            print(f"  [{r['table']}] {r['old_tmdb_id']} → {r['new_tmdb_id']}  {r['name']}  | {r['title'][:45]}")

    print("\nحفظ data.json (غير المكتشف)...")
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "dry_run": args.dry_run,
                "total_unresolved": len(unresolved),
                "unresolved": unresolved,
            },
            f, ensure_ascii=False, indent=2,
        )
    print(f"✓ data.json: {len(unresolved)} عنوان غير مكتشف")

    print("\nتقرير كامل: repair_report.json")
    with open("repair_report.json", "w", encoding="utf-8") as f:
        json.dump({"counters": counters, "results": results}, f, ensure_ascii=False, indent=2)
    print("✓ انتهى الإصلاح")


if __name__ == "__main__":
    main()