#!/usr/bin/env python3
"""
نقل حلقات المسلسلات (التي كانت مخزنة في جدول movies) إلى جدول tv_episodes.

السبب: جدول movies له UNIQUE(tmdb_id)، فلا يمكن أن يحتوي على عدة حلقات
لنفس المسلسل. tv_episodes مصمم لذلك (UNIQUE(tmdb_id, season_number, episode_number)).

عملية:
  - يقرأ repair_report.json (حالات dberr التي أعادت نفس tmdb_id لحلقات متعددة)
  - كل حلقة (media_type=tv + S/E) → إدراج/دمج في tv_episodes ثم حذف من movies
  - صفوف أفلام مكررة (media_type=movie) → حذف الصف المكرر (نفس الفيلم مرتين)

Usage:
    python3 fix_movies_conflicts.py            # تنفيذ النقل والحذف
    python3 fix_movies_conflicts.py --dry-run  # عرض فقط
"""

import os
import sys
import json
import time
import argparse
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

supabase: Client | None = None
if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"):
    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def find_tv_episode(tmdb_id: int, season: int, episode: int) -> dict | None:
    rows = (
        supabase.table("tv_episodes")
        .select("*")
        .eq("tmdb_id", tmdb_id)
        .eq("season_number", season)
        .eq("episode_number", episode)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def move_episode_to_tv(row: dict, dry: bool) -> dict:
    tmdb_id = row["new_tmdb_id"]
    season = row.get("season")
    episode = row.get("episode")
    fc = row.get("filecode") or ""
    series_title = (row.get("name") or row.get("query") or row["title"])[:200]
    raw_title = row.get("title") or ""

    fc_url = (
        "https://doodstream.com/e/" + fc if fc else None
    )
    download_url = "https://playmogo.com/d/" + fc if fc else None
    playmogo_url = "https://playmogo.com/e/" + fc if fc else None
    vidsrc_url = f"https://vidsrc.sbs/embed/tv/{tmdb_id}"

    existing = None if dry else find_tv_episode(tmdb_id, season, episode)

    action = "insert"
    if dry:
        return {"row_id": row["id"], "tmdb_id": tmdb_id, "S/E": f"{season}/{episode}",
                "series": series_title, "action": "would-insert", "ok": True}

    if existing:
        action = "merge_existing"
        upd = {}
        if not existing.get("doodstream_url") and fc_url:
            upd["doodstream_url"] = fc_url
        if not existing.get("doodstream_download_url") and download_url:
            upd["doodstream_download_url"] = download_url
        if not existing.get("playmogo_url") and playmogo_url:
            upd["playmogo_url"] = playmogo_url
        if upd:
            supabase.table("tv_episodes").update(upd).eq("id", existing["id"]).execute()
    else:
        data = {
            "tmdb_id": tmdb_id,
            "series_title": series_title,
            "season_number": season,
            "episode_number": episode,
            "title": raw_title,
            "filecode": fc or None,
            "doodstream_url": fc_url,
            "doodstream_download_url": download_url,
            "playmogo_url": playmogo_url,
            "vidsrc_url": vidsrc_url,
        }
        supabase.table("tv_episodes").insert(data).execute()

    # حذف الصف من movies بعد النقل
    try:
        supabase.table("movies").delete().eq("id", row["id"]).execute()
        deleted = True
    except Exception as e:
        deleted = False
        print(f"    [delete] فشل حذف movies id={row['id']}: {str(e)[:70]}")

    return {"row_id": row["id"], "tmdb_id": tmdb_id, "S/E": f"{season}/{episode}",
            "series": series_title, "action": action, "deleted_from_movies": deleted, "ok": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default="repair_report.json")
    args = ap.parse_args()

    report = json.load(open(args.report, encoding="utf-8"))
    dberr = [
        x for x in report["results"]
        if x["status"].startswith("dberr") and x.get("table") == "movies" and x.get("new_tmdb_id")
    ]
    print(f"{'DRY-RUN ' if args.dry_run else ''}معالجة {len(dberr)} صف متعارض من جدول movies")

    moved, deleted_dup, movie_dups = 0, 0, 0
    fails = []
    for row in dberr:
        try:
            if row.get("media_type") == "tv":
                r = move_episode_to_tv(row, args.dry_run)
                if args.dry_run:
                    print("  [move?]", r["row_id"], r["series"][:40], r["S/E"])
                else:
                    moved += 1
            else:
                # فيلم مكرر (نفس tmdb_id موجودة في صف آخر) — نكتفي بالتسجيل ولا نحذف
                movie_dups += 1
                if args.dry_run:
                    print("  [dup?]", row["id"], row["title"][:40])
        except Exception as e:
            fails.append((row["id"], str(e)[:80]))

    if not args.dry_run:
        if fails:
            print(f"فشل: {len(fails)}")
            for i, msg in fails[:10]:
                print("   ", i, msg)
        print(f"نقل إلى tv_episodes: {moved}  | أفلام مكررة مسجلة (لم تُحذف): {movie_dups}")
        with open("move_report.json", "w", encoding="utf-8") as f:
            json.dump({"moved": moved, "movie_dups": movie_dups, "fails": fails},
                      f, ensure_ascii=False, indent=2)
    print("انتهى")


if __name__ == "__main__":
    main()