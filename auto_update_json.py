#!/usr/bin/env python3
"""
سكريبت لتحديث ملف JSON تلقائياً باستخدام الدالة المحسنة والبحث في TMDB
"""

import os
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
if not TMDB_API_KEY:
    print("✗ لم يتم العثور على TMDB_API_KEY")
    exit(1)

print("✓ تم العثور على TMDB_API_KEY")

# Import the improved cleaning function
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catalog import _clean_search_title

def search_tmdb_api(title: str, media_type: str = "movie"):
    """البحث في TMDB API"""
    try:
        if media_type == "tv":
            url = "https://api.themoviedb.org/3/search/tv"
        else:
            url = "https://api.themoviedb.org/3/search/movie"
        
        params = {
            "api_key": TMDB_API_KEY,
            "query": title,
            "language": "en-US"
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        results = data.get("results", [])
        
        if results:
            return results[0].get("id")
        return None
    except Exception as e:
        print(f"  ❌ خطأ في البحث: {e}")
        return None

# Load JSON file
input_file = "fake_tmdb_ids_export.json"
try:
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"✓ تم تحميل {input_file}")
except Exception as e:
    print(f"✗ فشل تحميل الملف: {e}")
    exit(1)

# Update movies
print("\n" + "="*80)
print("🔄 تحديث الأفلام")
print("="*80)

movies = data.get("movies", [])
for i, movie in enumerate(movies, 1):
    original_title = movie.get("original_title", "")
    
    # Clean the title
    cleaned_title = _clean_search_title(original_title)
    movie["clean_title"] = cleaned_title
    
    # Search in TMDB
    new_tmdb_id = search_tmdb_api(cleaned_title, "movie")
    
    if new_tmdb_id and new_tmdb_id < 8000000:
        movie["new_tmdb_id"] = new_tmdb_id
        print(f"[{i}/{len(movies)}] ✅ {original_title[:60]}")
        print(f"   العنوان بعد التنظيف: {cleaned_title}")
        print(f"   tmdb_id: {new_tmdb_id}")
    else:
        movie["new_tmdb_id"] = None
        print(f"[{i}/{len(movies)}] ❌ {original_title[:60]}")
        print(f"   العنوان بعد التنظيف: {cleaned_title}")
        print(f"   لم يتم العثور على tmdb_id")
    
    # Rate limiting
    if i % 10 == 0:
        time.sleep(1)

# Update TV episodes
print("\n" + "="*80)
print("🔄 تحديث المسلسلات")
print("="*80)

tv_episodes = data.get("tv_episodes", [])
for i, tv in enumerate(tv_episodes, 1):
    original_title = tv.get("original_title", "")
    
    # Clean the title
    cleaned_title = _clean_search_title(original_title)
    tv["clean_title"] = cleaned_title
    
    # Search in TMDB
    new_tmdb_id = search_tmdb_api(cleaned_title, "tv")
    
    if new_tmdb_id and new_tmdb_id < 9000000:
        tv["new_tmdb_id"] = new_tmdb_id
        print(f"[{i}/{len(tv_episodes)}] ✅ {original_title[:60]}")
        print(f"   العنوان بعد التنظيف: {cleaned_title}")
        print(f"   tmdb_id: {new_tmdb_id}")
    else:
        tv["new_tmdb_id"] = None
        print(f"[{i}/{len(tv_episodes)}] ❌ {original_title[:60]}")
        print(f"   العنوان بعد التنظيف: {cleaned_title}")
        print(f"   لم يتم العثور على tmdb_id")
    
    # Rate limiting
    if i % 10 == 0:
        time.sleep(1)

# Save updated JSON file
with open(input_file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("\n" + "="*80)
print("✅ تم تحديث الملف JSON بنجاح")
print("="*80)

# Count results
movies_with_id = sum(1 for m in movies if m.get("new_tmdb_id"))
tv_with_id = sum(1 for t in tv_episodes if t.get("new_tmdb_id"))

print(f"الأفلام المحدثة بـ tmdb_id: {movies_with_id} من {len(movies)}")
print(f"المسلسلات المحدثة بـ tmdb_id: {tv_with_id} من {len(tv_episodes)}")
print(f"الإجمالي: {movies_with_id + tv_with_id} من {len(movies) + len(tv_episodes)}")
