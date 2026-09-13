#!/usr/bin/env python3
"""Mirror DoodStream videos into FileMoon (second streaming server).

Modes:
    python3 mirror_doodstream_to_filemoon.py --filecode X
    python3 mirror_doodstream_to_filemoon.py --filecode X --source-url https://.../video.mp4
    python3 mirror_doodstream_to_filemoon.py --all --limit 5
    python3 mirror_doodstream_to_filemoon.py --list-account

What it does for each filecode:
  1. Gets the file info (title) from DoodStream via doodapi.
  2. Resolves a source video URL:
       - --source-url if given,
       - else best-effort: embed token exchange / FlareSolverr / yt-dlp.
       - if a direct URL is found  -> FileMoon REMOTE upload.
       - otherwise downloads locally (yt-dlp) -> FileMoon NORMAL upload.
  3. Saves the mapping (doodstream filecode <-> filemoon file id) in filemoon_mirror.json.
  4. Updates Supabase (movies + tv_episodes) and the local catalog with filemoon_url.

Env needed: DOODSTREAM_API_KEY, FILEMOON_API_KEY (and SUPABASE_URL/SUPABASE_KEY to write DB).
"""

import os
import sys
import json
import time
import re
import argparse

import requests

from catalog import supabase, load_catalog, save_catalog, normalize_page_key
import filemoon

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

MIRROR_DB = "filemoon_mirror.json"
DOODSTREAM_EMBED_HOSTS = (
    "https://doodstream.com/e/{fc}",
    "https://playmogo.com/e/{fc}",
    "https://doodstream.com/d/{fc}",
    "https://playmogo.com/d/{fc}",
)


# ─── DoodStream helpers ────────────────────────────────────────────────────

def _dood_key() -> str:
    key = os.environ.get("DOODSTREAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DOODSTREAM_API_KEY not set")
    return key


def dood_file_info(filecode: str) -> dict | None:
    r = requests.get(
        f"https://doodapi.com/api/file/info?key={_dood_key()}&file_code={filecode}",
        timeout=20,
    )
    d = r.json()
    if d.get("status") != 200:
        print(f"[doodapi] info {filecode}: status {d.get('status')} msg={d.get('msg')}")
        return None
    res = d.get("result")
    if isinstance(res, list):
        return res[0] if res else None
    return res if isinstance(res, dict) else None


def dood_account_files(limit: int = 0) -> list[dict]:
    files = []
    page = 1
    while True:
        r = requests.get(
            f"https://doodapi.com/api/file/list?key={_dood_key()}&page={page}&per_page=100",
            timeout=30,
        )
        d = r.json()
        if d.get("status") != 200:
            print(f"[doodapi] list: status {d.get('status')} msg={d.get('msg')}")
            break
        batch = d.get("result", {}).get("files", [])
        if not batch:
            break
        files.extend(batch)
        if limit and len(files) >= limit:
            return files[:limit]
        total = d.get("result", {}).get("total_pages", 1)
        if page >= total:
            break
        page += 1
    return files


# ─── Source resolution (best-effort) ───────────────────────────────────────

def _extract_media_from_html(html: str) -> list[str]:
    out = []
    for m in re.findall(r'https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*', html):
        u = m.replace("\\/", "/")
        if u not in out:
            out.append(u)
    return out


def _resolve_via_direct_requests(filecode: str) -> str | None:
    """Classic doodstream embed token exchange (works when not CF-blocked)."""
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    for tmpl in ("https://doodstream.com/e/{fc}", "https://playmogo.com/e/{fc}"):
        url = tmpl.format(fc=filecode)
        try:
            r = sess.get(url, headers={"Referer": "https://doodstream.com/"}, timeout=20)
            if r.status_code != 200:
                continue
            html = r.text
            media = _extract_media_from_html(html)
            if media:
                print(f"[resolve] direct media in {url}: {media[0][:100]}")
                return media[0]
            # token exchange attempt
            matches = re.findall(r'(?:fToken|token|pass|ir)\s*[=:]\s*["\']([a-zA-Z0-9]{8,})["\']', html)
            if matches:
                post = sess.post(
                    url,
                    data={"token": matches[0], "referer": "https://doodstream.com/"},
                    headers={"X-Requested-With": "XMLHttpRequest", "Referer": url},
                    timeout=20,
                )
                try:
                    data = post.json()
                    su = data.get("url") or data.get("stream_url")
                    if su:
                        print(f"[resolve] token exchange got stream: {su[:100]}")
                        return su
                except Exception:
                    pass
        except Exception as e:
            print(f"[resolve] direct request {url}: {str(e)[:100]}")
        time.sleep(1)
    return None


def _resolve_via_flaresolverr(filecode: str) -> str | None:
    base = os.environ.get("FLARESOLVERR_URL", "http://localhost:8191/v1")
    for tmpl in ("https://playmogo.com/e/{fc}", "https://doodstream.com/e/{fc}"):
        url = tmpl.format(fc=filecode)
        try:
            payload = {
                "cmd": "request.get",
                "url": url,
                "maxTimeout": 45000,
                "request": {"headers": {"User-Agent": UA}},
            }
            r = requests.post(base, json=payload, timeout=60)
            d = r.json()
            if d.get("status") != "ok":
                continue
            html = (d.get("solution") or {}).get("response") or ""
            media = _extract_media_from_html(html)
            if media:
                print(f"[resolve] FlareSolverr media in {url}: {media[0][:100]}")
                return media[0]
            # token exchange via FS POST
            matches = re.findall(r'(?:fToken|token|pass|ir)\s*[=:]\s*["\']([a-zA-Z0-9]{8,})["\']', html)
            if matches:
                post = requests.post(
                    base,
                    json={
                        "cmd": "request.post",
                        "url": url,
                        "postData": f"token={matches[0]}&referer=https://doodstream.com/",
                    },
                    timeout=60,
                )
                pd = post.json()
                if pd.get("status") == "ok":
                    resp = (pd.get("solution") or {}).get("response") or ""
                    try:
                        body = json.loads(resp)
                        su = body.get("url") or body.get("stream_url")
                        if su:
                            print(f"[resolve] FS token exchange: {su[:100]}")
                            return su
                    except Exception:
                        media = _extract_media_from_html(resp)
                        if media:
                            return media[0]
        except Exception as e:
            print(f"[resolve] FlareSolverr {url}: {str(e)[:100]}")
        time.sleep(2)
    return None


def _resolve_via_ytdlp(filecode: str) -> str | None:
    try:
        import yt_dlp
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
            "force_generic_extractor": True,
            "extractor_args": {"generic": {"impersonate": ["chrome"]}},
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://doodstream.com/e/{filecode}", download=False)
        for k in ("url", "hls_url", "manifest_url"):
            if info.get(k):
                print(f"[resolve] yt-dlp stream: {info.get(k)[:100]}")
                return info[k]
        fmts = info.get("formats") or []
        if fmts:
            best = max((f for f in fmts if f.get("url")), key=lambda f: f.get("height") or 0, default=None)
            if best:
                print(f"[resolve] yt-dlp best fmt: {best['url'][:100]}")
                return best["url"]
    except Exception as e:
        print(f"[resolve] yt-dlp failed: {str(e)[:150]}")
    return None


def resolve_source_url(filecode: str) -> str | None:
    for fn in (_resolve_via_direct_requests, _resolve_via_flaresolverr, _resolve_via_ytdlp):
        u = fn(filecode)
        if u:
            return u
    return None


def download_locally(url: str, out_dir: str = "/tmp/filemoon_mirror") -> tuple[str, str]:
    """Download via yt-dlp. Returns (filepath, title)."""
    import yt_dlp
    os.makedirs(out_dir, exist_ok=True)
    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"generic": {"impersonate": ["chrome"]}},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        if not os.path.exists(path) and info.get("requested_downloads"):
            path = info["requested_downloads"][0].get("filepath") or path
        return path, info.get("title", "video")


# ─── FileMoon upload ───────────────────────────────────────────────────────

def upload_to_filemoon(url: str, title: str, job_id: str = "") -> dict:
    """Remote-upload a URL to FileMoon and poll until done."""
    print(f"[filemoon] Remote upload: {url[:100]}")
    job = filemoon.remote_upload([url], title=title)
    jid = job_id or job.get("id") or job.get("uuid") or job.get("job_id")
    if not jid:
        # Some payloads return the file id directly when already done.
        fid = job.get("file_id") or job.get("id")
        if fid and str(fid) not in ("0", "None", ""):
            print(f"[filemoon] immediate file_id: {fid}")
            return {"status": "completed", "file_id": fid, "file": job}
        raise RuntimeError(f"FileMoon remote-upload returned no job id: {json.dumps(job)[:300]}")
    print(f"[filemoon] Job submitted: {jid}")
    return filemoon.poll_remote_job(jid, title=title)


def upload_local_to_filemoon(path: str, title: str) -> dict:
    print(f"[filemoon] Normal upload: {path}")
    data = filemoon.normal_upload(path, title=title)
    fid = data.get("id") or data.get("file_id")
    if not fid:
        raise RuntimeError(f"FileMoon normal upload returned no id: {json.dumps(data)[:300]}")
    print(f"[filemoon] Uploaded file_id={fid}")
    return {"status": "completed", "file_id": fid, "file": data}


# ─── Persistence ───────────────────────────────────────────────────────────

def load_mirror_db() -> dict:
    if os.path.exists(MIRROR_DB):
        try:
            with open(MIRROR_DB, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"mapping": {}, "last_updated": None}


def save_mirror_db(db: dict):
    db["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(MIRROR_DB, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def update_supabase(filecode: str, urls: dict):
    if not supabase:
        print("[supabase] not connected, skipping DB update")
        return 0
    updated = 0
    for table in ("movies", "tv_episodes"):
        try:
            rows = (
                supabase.table(table)
                .select("id")
                .ilike("doodstream_url", f"%{filecode}%")
                .execute()
            )
            for row in rows.data:
                supabase.table(table).update(urls).eq("id", row["id"]).execute()
                updated += 1
            if rows.data:
                print(f"[supabase] updated {table} rows for {filecode}: {len(rows.data)}")
        except Exception as e:
            print(f"[supabase] error on {table}: {str(e)[:120]}")
    return updated


def update_catalog(filecode: str, urls: dict, title: str = ""):
    catalog = load_catalog()
    touched = 0
    for section in ("movies", "tv_episodes"):
        for key, entry in catalog.get(section, {}).items():
            entry_url = entry.get("doodstream_url") or ""
            if filecode in entry_url or entry.get("filecode") == filecode:
                entry.update(urls)
                if title:
                    entry["filemoon_title"] = title
                touched += 1
    if touched:
        save_catalog(catalog)
        print(f"[catalog] updated {touched} entries")


# ─── Per-file pipeline ─────────────────────────────────────────────────────

def mirror_one(filecode: str, source_url: str = "", dry: bool = False) -> dict:
    print(f"\n{'='*64}\nMIRROR: {filecode}")
    info = dood_file_info(filecode)
    title = (info or {}).get("title", "").strip() or filecode
    print(f"  dood title: {title[:90]}")

    res = {
        "doodstream_filecode": filecode,
        "title": title,
        "status": "skipped",
        "source_url": source_url or None,
        "filemoon_file_id": None,
        "filemoon_url": None,
    }
    if not info:
        res["status"] = "error:file_not_in_dood_account"
        return res

    if dry:
        res["status"] = "dry_run"
        return res

    url = source_url or ""
    if not url:
        try:
            from cimafu_source import find_source_url_from_cimafu
            url = find_source_url_from_cimafu(title) or ""
        except Exception as e:
            print(f"[mirror] cimafu source finder error: {str(e)[:120]}")
    if not url:
        url = resolve_source_url(filecode) or ""
    if url:
        try:
            up = upload_to_filemoon(url, title)
        except Exception as e:
            print(f"[filemoon] remote upload failed ({str(e)[:140]}) — falling back to local download")
            up = {"status": "failed", "file_id": None, "file": {}, "error": str(e)}
        if up.get("status") != "completed" or not up.get("file_id"):
            print("[mirror] remote path failed; trying local download + normal upload")
            try:
                path, dl_title = download_locally(url)
                up = upload_local_to_filemoon(path, title or dl_title)
                try:
                    os.remove(path)
                except OSError:
                    pass
            except Exception as e:
                print(f"[mirror] local fallback failed: {str(e)[:200]}")
                up = {"status": "failed", "file_id": None, "file": {}, "error": str(e)}
    else:
        print("[resolve] no source URL resolved — cannot mirror this file without --source-url")
        res["status"] = "error:no_source_url"
        return res

    fid = up.get("file_id")
    if not fid or up.get("status") != "completed":
        res["status"] = f"error:{up.get('status')}"
        res["error"] = str(up.get("error", ""))[:200]
        return res

    urls = filemoon.build_urls(fid)
    res.update({
        "status": "mirrored",
        "filemoon_file_id": str(fid),
        "filemoon_url": urls["filemoon_url"],
        "filemoon_download_url": urls["filemoon_download_url"],
    })
    print(f"  [mirror] DONE -> {urls['filemoon_url']}")

    db = load_mirror_db()
    db["mapping"][filecode] = res
    save_mirror_db(db)
    update_supabase(filecode, urls)
    update_catalog(filecode, urls, title)

    # drop transient fields from mapping json (keep it clean for later reads)
    return res


# ─── CLI ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Mirror DoodStream -> FileMoon")
    ap.add_argument("--filecode", default="", help="Single DoodStream file code to mirror")
    ap.add_argument("--source-url", default="", help="Direct video URL to re-use (skips dood extraction)")
    ap.add_argument("--all", action="store_true", help="Mirror all files from the DoodStream account")
    ap.add_argument("--limit", type=int, default=0, help="With --all: max files to process")
    ap.add_argument("--dry-run", action="store_true", help="Resolve info only; don't upload/write")
    ap.add_argument("--list-account", action="store_true", help="List account files and exit")
    args = ap.parse_args()

    if args.list_account:
        files = dood_account_files(args.limit or 0)
        print(f"TOTAL: {len(files)}")
        for f in files[: (args.limit or 60)]:
            print(" ", f.get("file_code"), "|", (f.get("title") or "")[:70])
        return

    if args.all:
        files = dood_account_files(args.limit or 0)
        print(f"Mirroring {len(files)} files...")
        results, okk = [], 0
        for f in files:
            fc = f.get("file_code")
            if not fc:
                continue
            try:
                r = mirror_one(fc, source_url=args.source_url, dry=args.dry_run)
            except Exception as e:
                r = {"doodstream_filecode": fc, "status": f"error:{str(e)[:120]}"}
            results.append(r)
            if r.get("status") == "mirrored":
                okk += 1
            time.sleep(2)
        with open("filemoon_mirror_results.json", "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, ensure_ascii=False)
        print(f"\nMirrored OK: {okk}/{len(results)}")
        return

    if not args.filecode:
        ap.error("Provide --filecode (or use --all / --list-account)")

    r = mirror_one(args.filecode, source_url=args.source_url, dry=args.dry_run)
    print(json.dumps(r, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()