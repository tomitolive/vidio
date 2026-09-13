#!/usr/bin/env python3
"""
سكريبت لتحديث قاعدة بيانات Supabase بناءً على الملف JSON المحدث
"""

import os
import json
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
updated_movies = 0
for movie in movies:
    movie_id = movie.get("id")
    new_tmdb_id = movie.get("new_tmdb_id")
    clean_title = movie.get("clean_title")
    
    if new_tmdb_id and new_tmdb_id < 8000000:
        print(f"✅ تحديث فيلم ID {movie_id}: {clean_title} → tmdb_id: {new_tmdb_id}")
        try:
            supabase.table("movies").update({"tmdb_id": new_tmdb_id}).eq("id", movie_id).execute()
            updated_movies += 1
        except Exception as e:
            print(f"   ❌ خطأ في التحديث: {e}")
    else:
        print(f"⏭️ تخطي فيلم ID {movie_id}: لا يوجد new_tmdb_id صحيح")

print(f"\n✅ تم تحديث {updated_movies} فيلم من أصل {len(movies)}")

# Update TV episodes
print("\n" + "="*80)
print("🔄 تحديث المسلسلات")
print("="*80)

tv_episodes = data.get("tv_episodes", [])
updated_tv = 0
for tv in tv_episodes:
    tv_id = tv.get("id")
    new_tmdb_id = tv.get("new_tmdb_id")
    clean_title = tv.get("clean_title")
    
    if new_tmdb_id and new_tmdb_id < 9000000:
        print(f"✅ تحديث مسلسل ID {tv_id}: {clean_title} → tmdb_id: {new_tmdb_id}")
        try:
            supabase.table("tv_episodes").update({"tmdb_id": new_tmdb_id}).eq("id", tv_id).execute()
            updated_tv += 1
        except Exception as e:
            print(f"   ❌ خطأ في التحديث: {e}")
    else:
        print(f"⏭️ تخطي مسلسل ID {tv_id}: لا يوجد new_tmdb_id صحيح")

print(f"\n✅ تم تحديث {updated_tv} مسلسل من أصل {len(tv_episodes)}")

print("\n" + "="*80)
print("📊 ملخص التحديث")
print("="*80)
print(f"الأفلام المحدثة: {updated_movies} من {len(movies)}")
print(f"المسلسلات المحدثة: {updated_tv} من {len(tv_episodes)}")
print(f"الإجمالي المحدث: {updated_movies + updated_tv} من {len(movies) + len(tv_episodes)}")
print("="*80)
