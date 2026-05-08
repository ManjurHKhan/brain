#!/usr/bin/env python3
"""
Import hs comms files into brain `comms` (cloud canonical).

Per Codex round-3 P1: importer **always sets source_file** so dedup goes
through the strongest key (file path) instead of the structural fingerprint.
content_fingerprint = sha256(source_file + occurred_at + summary).

Source: ~/.harmoni-state/comms/*.md (flat dir only).
Only files with YYYY-MM-DD in the filename are imported. Per-person rollup
files (e.g. `aaron-farrell.md` with no date) describe relationships, not
events; they're skipped.

Env: SUPABASE_DB_POOLER_URL or SUPABASE_DB_URL.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg2
import yaml

HOME = Path.home()
ROOT = HOME / ".harmoni-state" / "comms"

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
COMPACT_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)


def normalize_ts(value: str, fallback_date: str) -> str:
    """Normalize a frontmatter `created` value into something Postgres can cast
    with `::timestamptz`. Handles ISO, ISO without seconds, and compact
    yyyyMMddHHmmss. Falls back to noon on the filename date if all else fails."""
    if not value:
        return f"{fallback_date}T12:00:00"
    s = str(value).strip().strip("'\"")
    if not s:
        return f"{fallback_date}T12:00:00"
    cm = COMPACT_TS_RE.match(s)
    if cm:
        y, mo, d, h, mi, se = cm.groups()
        return f"{y}-{mo}-{d}T{h}:{mi}:{se}"
    # ISO-ish; let Postgres parse.
    return s

TYPE_MAP = {
    "call": "call",
    "call-notes": "call",
    "voicemail": "call",
    "meeting": "meeting",
    "meeting-notes": "meeting",
    "session": "meeting",
    "email": "email",
    "slack": "slack",
    "vendor-thread": "slack",
    "dm": "dm",
    "sms": "text",
    "text": "text",
}


def vault_relpath(p: Path) -> str:
    try:
        return f"~/{p.relative_to(HOME)}"
    except ValueError:
        return str(p)


def _line_fallback(raw: str) -> dict:
    out: dict = {}
    for line in raw.splitlines():
        if not line or line.startswith("#") or line.startswith(" ") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            out[key] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
        else:
            out[key] = val
    return out


def parse(p: Path) -> dict | None:
    text = p.read_text(errors="replace")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    raw_fm, body = m.group(1), m.group(2).strip()
    try:
        fm = yaml.safe_load(raw_fm) or {}
        if not isinstance(fm, dict):
            fm = _line_fallback(raw_fm)
    except yaml.YAMLError:
        fm = _line_fallback(raw_fm)

    name = p.stem
    dm = DATE_RE.search(name)
    if not dm:
        return None
    date_str = dm.group(1)

    # occurred_at: prefer frontmatter `created` (ISO timestamp), fallback to date.
    occurred_at = normalize_ts(fm.get("created"), date_str)

    raw_type = str(fm.get("type") or "").strip().lower()
    comm_type = TYPE_MAP.get(raw_type, "meeting")

    # contacts: filename head before the date, split on '-' (skip 1-letter chunks).
    head = name.split(date_str)[0].rstrip("-")
    parts = [w for w in head.split("-") if w and len(w) > 1]
    contacts: list[str] = []
    if parts:
        contacts.append(" ".join(p.capitalize() for p in parts).strip())

    # summary: title (first '# ' line) or topic suffix; trim to schema-friendly length.
    title = ""
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    if not title:
        tail = name.split(date_str, 1)[1].lstrip("-")
        title = tail.replace("-", " ").strip() or name

    summary = title[:500]

    src = vault_relpath(p)
    occurred_norm = occurred_at  # raw is fine; DB will parse to timestamptz
    fp = hashlib.sha256(
        f"comm|{src}|{date_str}|{summary}".encode()
    ).hexdigest()

    return {
        "occurred_at": occurred_at,
        "comm_type": comm_type,
        "platform": fm.get("platform"),
        "summary": summary,
        "body": body,
        "contacts": contacts,
        "source_file": src,
        "content_fingerprint": fp,
        "metadata": {
            "tags": fm.get("tags") or [],
            "raw_type": raw_type or None,
        },
    }


UPSERT_SQL = """
insert into comms (
  occurred_at, comm_type, platform, summary, body, contacts,
  source_file, content_fingerprint, metadata
) values (
  %(occurred_at)s::timestamptz, %(comm_type)s, %(platform)s, %(summary)s,
  %(body)s, %(contacts)s, %(source_file)s, %(content_fingerprint)s,
  %(metadata)s::jsonb
)
on conflict (content_fingerprint) where content_fingerprint is not null
do update set
  occurred_at = excluded.occurred_at,
  comm_type = excluded.comm_type,
  platform = coalesce(excluded.platform, comms.platform),
  summary = excluded.summary,
  body = excluded.body,
  contacts = excluded.contacts,
  source_file = excluded.source_file,
  metadata = comms.metadata || excluded.metadata,
  updated_at = now()
returning xmax = 0 as inserted;
"""


def find_files():
    if not ROOT.exists():
        return
    for p in sorted(ROOT.glob("*.md")):
        if not DATE_RE.search(p.stem):
            continue
        yield p


def upsert_one(dsn: str, row: dict) -> tuple[str, str]:
    import json
    row = dict(row)
    row["metadata"] = json.dumps(row["metadata"])
    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(UPSERT_SQL, row)
                res = cur.fetchone()
        if res is None:
            return ("nochange", row["source_file"])
        return ("inserted" if res[0] else "updated", row["source_file"])
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    files = list(find_files())
    if args.limit:
        files = files[: args.limit]
    print(f"discovered {len(files)} dated comm files in {ROOT}")

    rows: list[dict] = []
    skipped = 0
    for p in files:
        try:
            r = parse(p)
        except Exception as e:
            r = None
            if args.verbose:
                print(f"  PARSE-ERR {p}: {e}")
        if r is None:
            skipped += 1
            continue
        rows.append(r)
    print(f"parsed {len(rows)} rows; skipped {skipped}")

    if args.dry_run:
        for r in rows[:5]:
            print(f"  {r['occurred_at']:<22} {r['comm_type']:<8} {r['summary'][:60]:<60}  {r['contacts']}")
        return 0

    dsn = os.environ.get("SUPABASE_DB_POOLER_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("error: SUPABASE_DB_POOLER_URL/SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    counts = {"inserted": 0, "updated": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(upsert_one, dsn, r): r for r in rows}
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                outcome, _ = fut.result()
                counts[outcome] += 1
            except Exception as e:
                counts["error"] += 1
                print(f"  ERROR {r['source_file']}: {type(e).__name__}: {str(e)[:160]}")

    print(f"done: inserted={counts['inserted']} updated={counts['updated']} error={counts['error']}")
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
