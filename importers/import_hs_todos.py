#!/usr/bin/env python3
"""
Import hs todos from GLOBAL_TODO.md into brain `todos` (cloud canonical).

Per Codex round-3 operational note: importer is **transactional**, doing
`BEGIN; DELETE FROM todos WHERE source_file=$1; INSERT ...; COMMIT;`. A crash
between delete and insert would otherwise wipe rows.

GLOBAL_TODO.md is the only `source_file` for now. The MCP `todo_create` path
deliberately does *not* expose source_file, so importer-owned rows can't
collide with ad-hoc todos created via Claude/MCP.

Source format (auto-generated from hs SQLite):
  ### 🔄 In Progress
  - 🔄 Some task -- detail _Manjur_
  - ⬜ Another task _Manjur, Brian_

Status emoji mapping:
  🔄 -> in-progress
  ⬜ -> open
  ⏳ -> waiting
  ⏸ -> waiting
  ✅ -> done
  ❌ -> cancelled
  🔁 -> in-progress  (recurring ongoing items)

Env: SUPABASE_DB_POOLER_URL or SUPABASE_DB_URL.

Usage:
  .venv/bin/python importers/import_hs_todos.py --dry-run
  .venv/bin/python importers/import_hs_todos.py
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

import psycopg2

HOME = Path.home()
SOURCE = HOME / ".harmoni-state" / "GLOBAL_TODO.md"

STATUS_BY_EMOJI = {
    "🔄": "in-progress",
    "⬜": "open",
    "⏳": "waiting",
    "⏸": "waiting",
    "✅": "done",
    "❌": "cancelled",
    "🔁": "in-progress",
}

ITEM_RE = re.compile(r"^-\s+([🔄⬜⏳⏸✅❌🔁])\s+(.+?)\s*$")
AUTHOR_TRAIL_RE = re.compile(r"\s+_([^_]+)_\s*$")


def vault_relpath(p: Path) -> str:
    try:
        return f"~/{p.relative_to(HOME)}"
    except ValueError:
        return str(p)


def parse_authors(s: str) -> list[str]:
    raw = [x.strip().lower() for x in s.split(",") if x.strip()]
    seen = set()
    out: list[str] = []
    for a in raw:
        if a in seen:
            continue
        seen.add(a)
        out.append(a)
    return out


def parse_file(p: Path) -> list[dict]:
    src = vault_relpath(p)
    rows: list[dict] = []
    with p.open() as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.rstrip("\n")
            m = ITEM_RE.match(line)
            if not m:
                continue
            emoji, rest = m.group(1), m.group(2)
            status = STATUS_BY_EMOJI.get(emoji, "open")

            authors: list[str] = []
            am = AUTHOR_TRAIL_RE.search(rest)
            content = rest
            if am:
                authors = parse_authors(am.group(1))
                content = rest[: am.start()].strip()
            content = content.strip()
            if not content:
                continue

            assignee = authors[0] if authors else None
            if assignee == "manjur":
                assignee = "manjur"

            fp = hashlib.sha256(
                f"todo|{src}|{lineno}|{content}".encode()
            ).hexdigest()

            rows.append({
                "content": content,
                "status": status,
                "assignee": assignee,
                "created_by": "import:hs-global-todo",
                "priority": "medium",
                "context": None,
                "tags": [],
                "source_file": src,
                "source_line": lineno,
                "content_fingerprint": fp,
            })
    return rows


INSERT_SQL = """
insert into todos (
  content, status, assignee, created_by, priority, context, tags,
  source_file, source_line, content_fingerprint
) values (
  %(content)s, %(status)s, %(assignee)s, %(created_by)s, %(priority)s,
  %(context)s, %(tags)s, %(source_file)s, %(source_line)s, %(content_fingerprint)s
);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not SOURCE.exists():
        print(f"error: source missing: {SOURCE}", file=sys.stderr)
        return 2

    rows = parse_file(SOURCE)
    src = vault_relpath(SOURCE)
    print(f"parsed {len(rows)} todos from {src}")

    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print("  by status:", ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))

    if args.dry_run:
        for r in rows[:5]:
            print(f"  L{r['source_line']:<3} {r['status']:<12} {r['assignee'] or '-':<10} {r['content'][:80]}")
        return 0

    dsn = os.environ.get("SUPABASE_DB_POOLER_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("error: SUPABASE_DB_POOLER_URL/SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("delete from todos where source_file = %s;", (src,))
                deleted = cur.rowcount
                for r in rows:
                    cur.execute(INSERT_SQL, r)
        print(f"done: deleted={deleted} inserted={len(rows)} (single transaction)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
