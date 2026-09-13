#!/usr/bin/env python3
"""
سكريبت لتحديث clean_title فقط دون البحث في TMDB
"""

import os
import json
from dotenv import load_dotenv

load_dotenv()

# Import the improved cleaning function
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catalog import _clean_search_title

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
    
    print(f"[{i}/{len(movies)}] {original_title[:60]}")
    print(f"   العنوان بعد التنظيف: {cleaned_title}")

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
    
    print(f"[{i}/{len(tv_episodes)}] {original_title[:60]}")
    print(f"   العنوان بعد التنظيف: {cleaned_title}")

# Save updated JSON file
with open(input_file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("\n" + "="*80)
print("✅ تم تحديث الملف JSON بنجاح")
print("="*80)
print(f"تم تحديث {len(movies)} فيلم و {len(tv_episodes)} مسلسل")
