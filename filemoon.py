#!/usr/bin/env python3
"""FileMoon provider — second streaming server mirror of DoodStream.

API: https://filemoon.org/api/v1  (Bearer token)
Docs used: https://filemoon.org/en/user/api-remote

Endpoints used:
    GET  /account
    GET  /files
    POST /files/upload                (normal/multipart upload)
    GET  /remote-uploads
    POST /remote-uploads              ({"urls": [...]})
    GET  /remote-uploads/{id}
    POST /remote-uploads/{id}/cancel

Token is read from env FILEMOON_API_KEY (never printed).
"""

import os
import time
import requests
from dotenv import load_dotenv

BASE_URL = "https://filemoon.org/api/v1"
EMBED_URL = "https://filemoon.org/e/{file_id}"
STATUS = {
    "pending": "pending",
    "running": "running",
    "processing": "processing",
    "completed": "completed",
    "finished": "completed",
    "done": "completed",
    "success": "completed",
    "failed": "failed",
    "error": "failed",
}


load_dotenv()


def _token() -> str:
    token = (os.environ.get("FILEMOON_API_KEY") or os.environ.get("FILEMOON_API_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("FILEMOON_API_KEY not set (add to .env / GitHub secret)")
    return token


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
    }


def _normalize_status(raw) -> str:
    if not raw:
        return "pending"
    key = str(raw).strip().lower()
    return STATUS.get(key, key)


def get_account() -> dict:
    """Return account info (id, username, storage, remote_jobs)."""
    resp = requests.get(f"{BASE_URL}/account", headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"FileMoon account error: {data}")
    return data["data"]


def remote_upload(urls: list[str], title: str = "") -> dict:
    """Submit remote-upload job(s). Returns the created job payload (a dict)."""
    body = {"urls": urls}
    if title:
        body["title"] = title
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{BASE_URL}/remote-uploads",
                headers=_headers(),
                json=body,
                timeout=60,
            )
            if resp.status_code in (520, 503, 429) and attempt < 2:
                last_err = f"HTTP {resp.status_code}"
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError(f"FileMoon remote-upload error: {data}")
            res = data["data"]
            if isinstance(res, list):
                res = res[0] if res else {}
            return res
        except requests.HTTPError as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(4)
    raise RuntimeError(f"FileMoon remote-upload failed repeatedly: {last_err}")


def list_remote_uploads() -> list[dict]:
    """Return the account's remote-upload jobs (for status polling)."""
    resp = requests.get(f"{BASE_URL}/remote-uploads", headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data") or data.get("remote_uploads") or []


def get_remote_upload(job_id) -> dict:
    resp = requests.get(f"{BASE_URL}/remote-uploads/{job_id}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"FileMoon remote-upload status error: {data}")
    return data["data"]


def list_files(limit: int = 200) -> list[dict]:
    """Return recent files on the account (matches by title after remote upload)."""
    resp = requests.get(
        f"{BASE_URL}/files",
        headers=_headers(),
        params={"limit": limit},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data") or data.get("files") or []


def normal_upload(filepath: str, title: str = "", visibility: int = 1) -> dict:
    """Upload a local file (multipart). Returns file payload (with id)."""
    with open(filepath, "rb") as fh:
        files = {"file": (os.path.basename(filepath), fh, "video/mp4")}
        data = {"visibility": str(visibility)}
        if title:
            data["title"] = title
        resp = requests.post(
            f"{BASE_URL}/files/upload",
            headers=_headers(),
            files=files,
            data=data,
            timeout=1800,
        )
    resp.raise_for_status()
    payload = resp.json()
    if not (payload.get("success") or payload.get("type") == "success"):
        raise RuntimeError(f"FileMoon upload error: {payload}")
    file_info = payload.get("file") or {}
    fid = (
        payload.get("id")
        or payload.get("file_id")
        or payload.get("shared_id")
        or file_info.get("id")
        or file_info.get("shared_id")
    )
    if not fid:
        raise RuntimeError(f"FileMoon upload returned no file id: {payload}")
    return {"id": fid, "watch_url": file_info.get("watch_url"), "raw": payload}


def poll_remote_job(job_id, title: str = "", max_wait: int = 1800, interval: int = 20) -> dict:
    """Poll a remote-upload job until it completes/fails.

    Returns {"status": ..., "file_id": ..., "file": {...}}.
    file_id is discovered from the job payload if present, otherwise matched
    against /files by title.
    """
    deadline = time.time() + max_wait
    known = {}

    def _discover():
        if known.get("file_id"):
            return known["file_id"]
        job = known.get("job") or {}
        # Newest-first matching against /files by title or remote URL filename.
        for f in list_files(limit=500):
            fname = (f.get("title") or f.get("file_name") or "").strip().lower()
            if fname and title and title.strip().lower()[:40] in fname[:100]:
                known["file_id"] = f.get("id")
                known["file"] = f
                return f.get("id")
        return None

    while time.time() < deadline:
        try:
            job = get_remote_upload(job_id)
            known["job"] = job
        except Exception as e:
            print(f"[filemoon] status error: {str(e)[:120]}")
            job = {}

        st = _normalize_status(job.get("status") or job.get("state") or "pending")

        fid = _discover()
        if fid and st in ("completed", "processing"):
            print(f"[filemoon] Remote upload job {job_id} -> {st} (file_id={fid})")
            return {"status": st, "file_id": fid, "file": known.get("file") or {}}
        if st == "failed":
            print(f"[filemoon] Remote upload job {job_id} FAILED: {job}")
            return {"status": "failed", "file_id": None, "file": {}}

        print(f"[filemoon] Remote job {job_id}: {st} (waiting {interval}s)...")
        time.sleep(interval)

    return {"status": "timeout", "file_id": _discover(), "file": known.get("file") or {}}


def build_urls(file_id: str) -> dict:
    """Return the DoodStream-style URLs for a FileMoon file."""
    embed = EMBED_URL.format(file_id=file_id)
    return {
        "filemoon_url": embed,
        "filemoon_embed_url": f"https://filemoon.org/{file_id}/embed",
        "filemoon_download_url": f"https://filemoon.org/f/{file_id}",
    }