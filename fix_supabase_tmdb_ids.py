#!/usr/bin/env python3
"""
سكريبت لتحديث قاعدة بيانات Supabase فعلياً
إعادة البحث عن tmdb_id للعناوين التي لها tmdb_id مولد باستخدام القواعد المحسنة
يتم تحديث فقط العناوين التي يمكن العثور على tmdb_id صحيح لها
"""

import os
import requests
import time
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Connect to Supabase
supabase: Client | None = None
try:
    _url = os.environ.get("SUPABASE_URL")
    _key = os.environ.get("SUPABASE_KEY")
    if _url and _key:
        supabase = create_client(_url, _key)
        print("✓ تم الاتصال بـ Supabase")
    else:
        print("✗ لم يتم العثور على SUPABASE_URL أو SUPABASE_KEY")
        exit(1)
except Exception as e:
    print(f"✗ فشل الاتصال: {e}")
    exit(1)

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

# Update movies with fake tmdb_id (>= 8000000)
print("\n" + "="*80)
print("🔄 تحديث الأفلام بـ tmdb_id مولد")
print("="*80)

movies = supabase.table("movies").select("id, tmdb_id, title").gte("tmdb_id", 8000000).execute()
fake_movies = movies.data

print(f"📊 تم العثور على {len(fake_movies)} فيلم بـ tmdb_id مولد")

updated_movies = 0
for i, movie in enumerate(fake_movies, 1):
    movie_id = movie.get("id")
    old_tmdb_id = movie.get("tmdb_id")
    title = movie.get("title", "")
    
    # Clean the title
    cleaned_title = _clean_search_title(title)
    
    # Search in TMDB
    new_tmdb_id = search_tmdb_api(cleaned_title, "movie")
    
    if new_tmdb_id and new_tmdb_id < 8000000:
        print(f"[{i}/{len(fake_movies)}] ✅ {title[:60]}")
        print(f"   العنوان بعد التنظيف: {cleaned_title}")
        print(f"   tmdb_id القديم: {old_tmdb_id} → الجديد: {new_tmdb_id}")
        
        # Update in Supabase
        try:
            supabase.table("movies").update({"tmdb_id": new_tmdb_id}).eq("id", movie_id).execute()
            updated_movies += 1
        except Exception as e:
            print(f"   ❌ خطأ في التحديث: {e}")
    else:
        print(f"[{i}/{len(fake_movies)}] ❌ {title[:60]}")
        print(f"   العنوان بعد التنظيف: {cleaned_title}")
        print(f"   لم يتم العثور على tmdb_id صحيح")
    
    # Rate limiting to avoid TMDB API limits
    if i % 10 == 0:
        time.sleep(1)

print(f"\n✅ تم تحديث {updated_movies} فيلم من أصل {len(fake_movies)}")

# Update TV episodes with fake tmdb_id (>= 9000000)
print("\n" + "="*80)
print("🔄 تحديث المسلسلات بـ tmdb_id مولد")
print("="*80)

tv_episodes = supabase.table("tv_episodes").select("id, tmdb_id, series_title").gte("tmdb_id", 9000000).execute()
fake_tv = tv_episodes.data

print(f"📊 تم العثور على {len(fake_tv)} مسلسل بـ tmdb_id مولد")

updated_tv = 0
for i, tv in enumerate(fake_tv, 1):
    tv_id = tv.get("id")
    old_tmdb_id = tv.get("tmdb_id")
    title = tv.get("series_title", "")
    
    # Clean the title
    cleaned_title = _clean_search_title(title)
    
    # Search in TMDB
    new_tmdb_id = search_tmdb_api(cleaned_title, "tv")
    
    if new_tmdb_id and new_tmdb_id < 9000000:
        print(f"[{i}/{len(fake_tv)}] ✅ {title[:60]}")
        print(f"   العنوان بعد التنظيف: {cleaned_title}")
        print(f"   tmdb_id القديم: {old_tmdb_id} → الجديد: {new_tmdb_id}")
        
        # Update in Supabase
        try:
            supabase.table("tv_episodes").update({"tmdb_id": new_tmdb_id}).eq("id", tv_id).execute()
            updated_tv += 1
        except Exception as e:
            print(f"   ❌ خطأ في التحديث: {e}")
    else:
        print(f"[{i}/{len(fake_tv)}] ❌ {title[:60]}")
        print(f"   العنوان بعد التنظيف: {cleaned_title}")
        print(f"   لم يتم العثور على tmdb_id صحيح")
    
    # Rate limiting to avoid TMDB API limits
    if i % 10 == 0:
        time.sleep(1)

print(f"\n✅ تم تحديث {updated_tv} مسلسل من أصل {len(fake_tv)}")

print("\n" + "="*80)
print("📊 ملخص التحديث")
print("="*80)
print(f"الأفلام المحدثة: {updated_movies} من {len(fake_movies)}")
print(f"المسلسلات المحدثة: {updated_tv} من {len(fake_tv)}")
print(f"الإجمالي المحدث: {updated_movies + updated_tv} من {len(fake_movies) + len(fake_tv)}")
print("="*80)
