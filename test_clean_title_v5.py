#!/usr/bin/env python3
"""
اختبار دالة _clean_search_title مع التركيز على إزالة حرف 'ة' من نهاية الكلمات فقط
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catalog import _clean_search_title

test_titles = [
    "مشاهدة مسلسل وتحميل الولادة الجديدة Born Again الحلقة 20 مترجمة",
    "مشاهدة مسلسل وتحميل مواجهة المجاعة برفقة زوجتي Braving the Famine الحلقة 1 مترجمة",
    "مشاهدة مسلسل وتحميل Rescue Me الموسم الاول الحلقة 1 مترجمة",
    "مشاهدة مسلسل وتحميل فخ يدعي الرغبة A Trap Called Desire الحلقة 20 مترجمة",
]

print("🧪 اختبار دالة _clean_search_title مع التركيز على إزالة حرف 'ة' من نهاية الكلمات فقط")
print("="*80)

for title in test_titles:
    cleaned = _clean_search_title(title)
    print(f"\n📝 الأصلي: {title}")
    print(f"✨ بعد التنظيف: {cleaned}")

print("\n" + "="*80)
