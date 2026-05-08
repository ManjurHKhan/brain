#!/usr/bin/env python3
"""
Import hs sessions markdown into brain `sessions` (cloud canonical).

Source: ~/.harmoni-state/memory/sessions/*.md (264 files).

Mapping:
  - session_id: filename prefix `c-YYYYMMDD-HHMMSS-XX` if present, else null
  - title: derived from filename (date/session prefix stripped) or first H1
  - project: frontmatter `project` (normalized lowercase)
  - started_at: frontmatter `created` (compact yyyyMMddHHmmss or ISO accepted)
  - body: full content after frontmatter
  - accomplishments/decisions/open_items/next_steps: bullet items under matching H2 sections

Idempotency: content_fingerprint = sha256(source_file|started_at|title).
ON CONFLICT (content_fingerprint) WHERE NOT NULL DO UPDATE.

Env: SUPABASE_DB_POOLER_URL or SUPABASE_DB_URL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg2
import yaml

HOME = Path.home()
ROOT = HOME / ".harmoni-state" / "memory" / "sessions"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)
SESSION_ID_RE = re.compile(r"^(c-\d{8}-\d{6}-[a-z0-9]+)", re.IGNORECASE)
DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
COMPACT_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$")
H2_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
BULLET_RE = re.compile(r"^[-*]\s+(.+?)\s*$")

SECTION_MAP = {
    "accomplished": "accomplishments",
    "accomplishments": "accomplishments",
    "what we did": "accomplishments",
    "decisions made": "decisions",
    "decisions": "decisions",
    "open items": "open_items",
    "open questions": "open_items",
    "next steps": "next_steps",
    "next session": "next_steps",
}


def vault_relpath(p: Path) -> str:
    try:
        return f"~/{p.relative_to(HOME)}"
    except ValueError:
        return str(p)


def normalize_ts(value, fallback_date: str | None) -> str:
    if value is None or str(value).strip() == "":
        return f"{fallback_date}T12:00:00" if fallback_date else "1970-01-01T00:00:00"
    s = str(value).strip().strip("'\"")
    cm = COMPACT_TS_RE.match(s)
    if cm:
        y, mo, d, h, mi, se = cm.groups()
        return f"{y}-{mo}-{d}T{h}:{mi}:{se}"
    return s


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


def parse_sections(body: str) -> dict[str, list[str]]:
    """Return dict of canonical section name -> list of bullet items."""
    out: dict[str, list[str]] = {
        "accomplishments": [],
        "decisions": [],
        "open_items": [],
        "next_steps": [],
    }
    matches = list(H2_SECTION_RE.finditer(body))
    for i, m in enumerate(matches):
        heading = m.group(1).strip().lower()
        canonical = SECTION_MAP.get(heading)
        if not canonical:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section = body[start:end]
        for line in section.splitlines():
            bm = BULLET_RE.match(line)
            if bm:
                item = bm.group(1).strip()
                if item:
                    out[canonical].append(item[:1000])
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

    # session_id from filename if it matches the c-prefixed pattern.
    session_id = None
    sm = SESSION_ID_RE.match(name)
    if sm:
        session_id = sm.group(1)
    elif fm.get("session_id"):
        session_id = str(fm["session_id"]).strip()

    # title: derive from filename, with date/session prefix stripped.
    title_src = name
    if session_id and title_src.startswith(session_id):
        title_src = title_src[len(session_id):].lstrip("-")
    elif DATE_PREFIX_RE.match(title_src):
        title_src = DATE_PREFIX_RE.sub("", title_src).lstrip("-")
    title = title_src.replace("-", " ").strip() or name

    # started_at fallback: use date prefix from filename if no frontmatter created.
    fb_date = None
    dm = DATE_PREFIX_RE.match(name)
    if dm:
        fb_date = dm.group(1)
    elif session_id:
        smm = re.match(r"c-(\d{4})(\d{2})(\d{2})", session_id)
        if smm:
            fb_date = f"{smm.group(1)}-{smm.group(2)}-{smm.group(3)}"
    started_at = normalize_ts(fm.get("created"), fb_date)

    project = fm.get("project")
    if project:
        project = str(project).strip().lower().rstrip(",").strip()
        if project in ("", "none", "null"):
            project = None

    sections = parse_sections(body)

    src = vault_relpath(p)
    fp = hashlib.sha256(
        f"session|{src}|{started_at}|{title}".encode()
    ).hexdigest()

    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    return {
        "session_id": session_id,
        "title": title[:500],
        "project": project,
        "status": "ended",  # historic sessions are always closed by the time they're imported
        "started_at": started_at,
        "ended_at": None,
        "body": body,
        "accomplishments": sections["accomplishments"],
        "decisions": sections["decisions"],
        "open_items": sections["open_items"],
        "next_steps": sections["next_steps"],
        "files_modified": [],
        "source_file": src,
        "content_fingerprint": fp,
        "metadata": {"tags": tags},
    }


UPSERT_SQL = """
insert into sessions (
  session_id, title, project, status, started_at, ended_at, body,
  accomplishments, decisions, open_items, next_steps, files_modified,
  source_file, content_fingerprint, metadata
) values (
  %(session_id)s, %(title)s, %(project)s, %(status)s,
  %(started_at)s::timestamptz, %(ended_at)s,
  %(body)s, %(accomplishments)s, %(decisions)s, %(open_items)s, %(next_steps)s,
  %(files_modified)s, %(source_file)s, %(content_fingerprint)s, %(metadata)s::jsonb
)
on conflict (content_fingerprint) where content_fingerprint is not null
do update set
  session_id = coalesce(excluded.session_id, sessions.session_id),
  title = excluded.title,
  project = coalesce(excluded.project, sessions.project),
  status = excluded.status,
  -- preserve original started_at if already populated (per session_start RPC pattern)
  started_at = coalesce(sessions.started_at, excluded.started_at),
  body = excluded.body,
  accomplishments = excluded.accomplishments,
  decisions = excluded.decisions,
  open_items = excluded.open_items,
  next_steps = excluded.next_steps,
  source_file = excluded.source_file,
  metadata = sessions.metadata || excluded.metadata,
  updated_at = now()
returning xmax = 0 as inserted;
"""


def find_files():
    if not ROOT.exists():
        return
    for p in sorted(ROOT.rglob("*.md")):
        yield p


def upsert_one(dsn: str, row: dict) -> tuple[str, str]:
    row = dict(row)
    row["metadata"] = json.dumps(row["metadata"])
    conn = psycopg2.connect(dsn)
    try:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(UPSERT_SQL, row)
                    res = cur.fetchone()
        except psycopg2.errors.UniqueViolation:
            # Two markdown files share the same session_id (e.g. continuation logs).
            # Keep the body, drop session_id on this row so the partial unique index
            # is satisfied. Fingerprint still uniquely identifies the row.
            conn.rollback()
            row["session_id"] = None
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
    args = ap.parse_args()

    files = list(find_files())
    if args.limit:
        files = files[: args.limit]
    print(f"discovered {len(files)} session files in {ROOT}")

    rows: list[dict] = []
    skipped = 0
    for p in files:
        try:
            r = parse(p)
        except Exception as e:
            print(f"  PARSE-ERR {p}: {e}")
            r = None
        if r is None:
            skipped += 1
            continue
        rows.append(r)
    print(f"parsed {len(rows)} rows; skipped {skipped}")

    if args.dry_run:
        for r in rows[:5]:
            print(f"  {r['started_at']:<22} {r['project'] or '-':<10} {(r['session_id'] or '-'):<28} {r['title'][:60]}")
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
                print(f"  ERROR {r['source_file']}: {type(e).__name__}: {str(e)[:200]}")

    print(f"done: inserted={counts['inserted']} updated={counts['updated']} error={counts['error']}")
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
