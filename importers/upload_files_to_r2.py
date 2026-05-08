#!/usr/bin/env python3
"""
Upload concrete-evidence files from the harmoni-state vault to Cloudflare R2,
with a RAG companion: extracted text goes into `thoughts` (searchable), bytes
stay in R2 (retrieved on-demand via presigned URL).

Layout convention: R2 keys mirror vault paths exactly with the
`~/.harmoni-state/` prefix stripped. So:
  ~/.harmoni-state/inbox/foo.pdf  ->  inbox/foo.pdf

Idempotent via the partial unique index on `files.r2_key` (added in
20260508000003_files_r2_link.sql).

Approved roots (per 2026-05-08 user approval):
  inbox/                       -- vendor PDFs, NIST/CMMC docs, quotes
  output/                      -- DOCX/XLSX/PDF deliverables (incl. .md for full bytes)
  comms/source/                -- raw call/meeting/email/Slack transcripts
  projects/<slug>/research/    -- per-project research
  research/                    -- top-level cross-project research

Env: SUPABASE_DB_POOLER_URL, R2_S3_ENDPOINT, R2_ACCESS_KEY_ID,
     R2_SECRET_ACCESS_KEY, R2_BUCKET.

Usage:
  .venv/bin/python importers/upload_files_to_r2.py --dry-run
  .venv/bin/python importers/upload_files_to_r2.py --limit 5
  .venv/bin/python importers/upload_files_to_r2.py --workers 6
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
import psycopg2
from botocore.config import Config

HOME = Path.home()
VAULT = HOME / ".harmoni-state"

ROOTS = [
    VAULT / "inbox",
    VAULT / "output",
    VAULT / "comms" / "source",
    VAULT / "research",
]
# Project-scoped research dirs (added explicitly so we don't sweep memory/, issues/ etc.)
for proj in ("personal", "infra", "grc", "dev"):
    ROOTS.append(VAULT / "projects" / proj / "research")

EXCLUDE_FRAGS = (
    "/.git/",
    "/.obsidian/",
    "/.trash/",
    "/archive/",
    "/cache/",
)

# Map extensions to schema `kind` enum. Anything else -> 'other'.
KIND_BY_EXT = {
    ".md": "md", ".txt": "txt", ".csv": "csv", ".json": "json",
    ".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx",
    ".png": "png", ".jpg": "jpg", ".jpeg": "jpeg", ".gif": "gif", ".svg": "svg",
    ".mp4": "mp4", ".mov": "mov", ".mp3": "mp3", ".wav": "wav",
    ".zip": "zip", ".tar": "tar",
}

# Extensions where markitdown does the heavy lifting (DOCX/PDF/XLSX/PPTX/HTML).
# Plain-text extensions are read directly to avoid markitdown dependency cost.
MARKITDOWN_EXTS = {".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm"}
PLAINTEXT_EXTS = {".md", ".txt", ".csv", ".json", ".svg", ".drawio"}

MAX_THOUGHT_CHARS = 8000


def vault_relpath(p: Path) -> str:
    """Vault-relative R2 key (no leading slash)."""
    return str(p.relative_to(VAULT))


def vault_abs_display(p: Path) -> str:
    """Display path with `~/` prefix for human readability."""
    try:
        return f"~/{p.relative_to(HOME)}"
    except ValueError:
        return str(p)


def detect_kind(p: Path) -> str:
    return KIND_BY_EXT.get(p.suffix.lower(), "other")


def extract_text(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in PLAINTEXT_EXTS:
        try:
            return p.read_text(errors="replace")
        except Exception:
            return ""
    if ext in MARKITDOWN_EXTS:
        try:
            from markitdown import MarkItDown
            md = MarkItDown(enable_plugins=False)
            res = md.convert(str(p))
            return res.text_content or ""
        except Exception as e:
            return f"[extraction failed: {type(e).__name__}: {str(e)[:120]}]"
    # Binary or unsupported (images, audio, video, archives) -> empty thought.
    # File still gets uploaded; bytes can be fetched on demand.
    return ""


def find_files():
    seen: set[Path] = set()
    for root in ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            sp = str(p)
            if any(frag in sp for frag in EXCLUDE_FRAGS):
                continue
            if p.name.startswith("."):
                continue
            if p in seen:
                continue
            seen.add(p)
            yield p


def get_s3():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_S3_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


UPSERT_FILE_SQL = """
insert into files (
  name, kind, content_type, size, sha256, r2_key, r2_bucket, status,
  thought_id, uploaded_by, metadata
) values (
  %(name)s, %(kind)s, %(content_type)s, %(size)s, %(sha256)s,
  %(r2_key)s, %(r2_bucket)s, 'ready',
  %(thought_id)s, 'import:r2-uploader', %(metadata)s::jsonb
)
on conflict (r2_key) where r2_key is not null
do update set
  name = excluded.name,
  kind = excluded.kind,
  content_type = excluded.content_type,
  size = excluded.size,
  sha256 = excluded.sha256,
  status = 'ready',
  thought_id = coalesce(excluded.thought_id, files.thought_id),
  metadata = files.metadata || excluded.metadata,
  updated_at = now()
returning id, xmax = 0 as inserted;
"""

UPSERT_THOUGHT_SQL = "select upsert_thought(%s, %s::jsonb) as result;"


def process_one(dsn: str, p: Path) -> tuple[str, str]:
    s3 = get_s3()
    bucket = os.environ["R2_BUCKET"]
    key = vault_relpath(p)
    name = p.name
    kind = detect_kind(p)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"

    with p.open("rb") as fh:
        body = fh.read()
    size = len(body)
    sha = hashlib.sha256(body).hexdigest()

    # Upload bytes (overwrites are idempotent — same key, same content yields same hash).
    # S3 metadata values must be ASCII; encode any non-ASCII chars (en-dashes etc).
    vault_disp = vault_abs_display(p)
    safe_vault_path = vault_disp.encode("ascii", "replace").decode("ascii")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
        Metadata={"sha256": sha, "vault_path": safe_vault_path},
    )

    # Extract text + capture as RAG companion thought (truncate to embedding window).
    text = extract_text(p) or ""
    text = text.strip()
    thought_id: str | None = None
    if text:
        truncated = text[:MAX_THOUGHT_CHARS]
        marker = f"[Source: {vault_abs_display(p)}]\n\n{truncated}"
        if len(text) > MAX_THOUGHT_CHARS:
            marker += f"\n\n[Truncated; original_size={len(text)}]"
        payload = {
            "metadata": {
                "source": "r2-upload",
                "r2_key": key,
                "r2_bucket": bucket,
                "vault_path": vault_abs_display(p),
                "kind": kind,
                "sha256": sha,
                "size_bytes": size,
            }
        }
        conn = psycopg2.connect(dsn)
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(UPSERT_THOUGHT_SQL, (marker, json.dumps(payload)))
                    res = cur.fetchone()
                    if res and res[0]:
                        result = res[0]
                        if isinstance(result, dict):
                            thought_id = result.get("id")
        finally:
            conn.close()

    # Insert/update files row pointing at R2 key + companion thought.
    metadata = {
        "vault_path": vault_abs_display(p),
        "extracted_chars": len(text) if text else 0,
        "extracted_truncated": len(text) > MAX_THOUGHT_CHARS,
    }
    row = {
        "name": name,
        "kind": kind,
        "content_type": content_type,
        "size": size,
        "sha256": sha,
        "r2_key": key,
        "r2_bucket": bucket,
        "thought_id": thought_id,
        "metadata": json.dumps(metadata),
    }
    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(UPSERT_FILE_SQL, row)
                file_row = cur.fetchone()
        outcome = "inserted" if (file_row and file_row[1]) else "updated"
    finally:
        conn.close()

    return (outcome, key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    files = list(find_files())
    files.sort()
    if args.limit:
        files = files[: args.limit]

    print(f"discovered {len(files)} candidate files across approved roots")
    by_root: dict[str, int] = {}
    for p in files:
        for r in ROOTS:
            if str(p).startswith(str(r)):
                by_root[r.name] = by_root.get(r.name, 0) + 1
                break
    for k, v in sorted(by_root.items()):
        print(f"  {k}: {v}")

    if args.dry_run:
        for p in files[:5]:
            print(f"  {detect_kind(p):<6} {p.stat().st_size:>10}  {vault_relpath(p)}")
        return 0

    dsn = os.environ.get("SUPABASE_DB_POOLER_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("error: SUPABASE_DB_POOLER_URL/SUPABASE_DB_URL not set", file=sys.stderr)
        return 2
    for k in ("R2_S3_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        if not os.environ.get(k):
            print(f"error: {k} not set in env", file=sys.stderr)
            return 2

    counts = {"inserted": 0, "updated": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process_one, dsn, p): p for p in files}
        for i, fut in enumerate(as_completed(futs), start=1):
            p = futs[fut]
            try:
                outcome, key = fut.result()
                counts[outcome] += 1
                if args.verbose or i % 50 == 0:
                    print(f"  [{i:>4}/{len(files)}] {outcome:<8} {key}")
            except Exception as e:
                counts["error"] += 1
                print(f"  ERROR {p}: {type(e).__name__}: {str(e)[:200]}")

    print(
        f"done: inserted={counts['inserted']} updated={counts['updated']} error={counts['error']}"
    )
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
