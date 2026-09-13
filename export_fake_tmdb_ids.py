#!/usr/bin/env python3
"""
سكريبت لتصدير جميع العناوين التي لها tmdb_id مولد إلى ملف JSON
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

# Fetch movies with fake tmdb_id (>= 8000000)
print("\n📊 جلب الأفلام بـ tmdb_id مولد...")
movies = supabase.table("movies").select("id, tmdb_id, title").gte("tmdb_id", 8000000).execute()
fake_movies = movies.data

# Fetch TV episodes with fake tmdb_id (>= 9000000)
print("📊 جلب المسلسلات بـ tmdb_id مولد...")
tv_episodes = supabase.table("tv_episodes").select("id, tmdb_id, series_title").gte("tmdb_id", 9000000).execute()
fake_tv = tv_episodes.data

# Create JSON structure
export_data = {
    "movies": [],
    "tv_episodes": []
}

# Add movies
for movie in fake_movies:
    export_data["movies"].append({
        "id": movie.get("id"),
        "tmdb_id": movie.get("tmdb_id"),
        "original_title": movie.get("title"),
        "clean_title": "",  # User will fill this
        "new_tmdb_id": None  # User will fill this
    })

# Add TV episodes
for tv in fake_tv:
    export_data["tv_episodes"].append({
        "id": tv.get("id"),
        "tmdb_id": tv.get("tmdb_id"),
        "original_title": tv.get("series_title"),
        "clean_title": "",  # User will fill this
        "new_tmdb_id": None  # User will fill this
    })

# Save to JSON file
output_file = "fake_tmdb_ids_export.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(export_data, f, ensure_ascii=False, indent=2)

print(f"\n✅ تم تصدير {len(fake_movies)} فيلم و {len(fake_tv)} مسلسل إلى {output_file}")
print(f"📝 يرجى تحديث الملف يدوياً بإضافة clean_title و new_tmdb_id")
