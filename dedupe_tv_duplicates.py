#!/usr/bin/env python3
"""
تنظيف صفوف tv_episodes المكررة التي فشلت في التحديث (duplicate unique constraint).

الأسباب:
  - قد يوجد صفّان لنفس (tmdb_id, season, episode) أحدهما بـ tmdb_id صحيح والآخر مزور.
  - عند محاولة تحديث المزور يرفضه UNIQUE(tmdb_id, season_number, episode_number).

العلاج:
  - لكل صف من repair_report.json بحالة dberr و media_type=tv:
    * إيجاد "التوأم" (نفس tmdb_id + season + episode بصف آخر)
    * دمج رابط doodstream إن ناقص في التوأم
    * حذف الصف المزور المكرر
    * إذا لم يوجد توأم → محاولة تحديث مباشر

Usage:
    python3 dedupe_tv_duplicates.py
    python3 dedupe_tv_duplicates.py --dry-run
"""

import os
import sys
import json
import argparse
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

supabase: Client | None = None
if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"):
    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def find_twins(tmdb_id: int, season, episode, exclude_id: int) -> list[dict]:
    q = (
        supabase.table("tv_episodes")
        .select("*")
        .eq("tmdb_id", tmdb_id)
        .eq("season_number", season)
        .eq("episode_number", episode)
        .neq("id", exclude_id)
    )
    return q.execute().data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default="repair_report.json")
    args = ap.parse_args()

    report = json.load(open(args.report, encoding="utf-8"))
    dberr = [x for x in report["results"]
             if x["status"].startswith("dberr") and x.get("table") == "tv_episodes"
             and x.get("new_tmdb_id")]
    print(f"Dberr tv rows: {len(dberr)}")

    deleted = 0
    merged = 0
    direct_updated = 0
    keepers: dict[int, dict] = {}

    # Identify keepers first: for every (tmdb,S,E) with duplicates, the first
    # (lowest-id) row stays, the rest are deleted.
    for row in dberr:
        fake_id = row["id"]
        tmdb = row["new_tmdb_id"]
        season = row.get("season")
        episode = row.get("episode")
        if season is None or episode is None:
            continue
        if args.dry_run:
            print(f"  [would-dedupe] id={fake_id} -> tmdb {tmdb} S{season}E{episode} {row['title'][:40]}")
            continue
        twins = find_twins(tmdb, season, episode, fake_id)
        if twins:
            twins.sort(key=lambda t: t["id"])
            keeper = twins[0]
            if keeper["id"] not in keepers:
                keepers[keeper["id"]] = keeper
            upd = {}
            if not keeper.get("doodstream_url"):
                fc = row.get("filecode") or ""
                if fc:
                    upd["doodstream_url"] = f"https://doodstream.com/e/{fc}"
            if not keeper.get("doodstream_download_url"):
                fc = row.get("filecode") or ""
                if fc:
                    upd["doodstream_download_url"] = f"https://playmogo.com/d/{fc}"
            if not keeper.get("playmogo_url"):
                fc = row.get("filecode") or ""
                if fc:
                    upd["playmogo_url"] = f"https://playmogo.com/e/{fc}"
            if upd:
                supabase.table("tv_episodes").update(upd).eq("id", keeper["id"]).execute()
                merged += 1
            supabase.table("tv_episodes").delete().eq("id", fake_id).execute()
            deleted += 1
        else:
            # لا توأم -> يحاول تحديث مباشر (التيقن من وجود الـ tmdb صحيح)
            try:
                supabase.table("tv_episodes").update({"tmdb_id": tmdb}).eq("id", fake_id).execute()
                direct_updated += 1
            except Exception as e:
                print(f"    [update-fail] id={fake_id}: {str(e)[:70]}")

    if args.dry_run:
        print("DRY-RUN (لا تغيير)")
        return

    print(f"مدمج روابط في التوائم: {merged}")
    print(f"محذوف مكرر          : {deleted}")
    print(f"تحديث مباشر          : {direct_updated}")

    with open("dedupe_report.json", "w", encoding="utf-8") as f:
        json.dump({"deleted": deleted, "merged": merged, "direct_updated": direct_updated},
                  f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()