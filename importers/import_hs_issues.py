#!/usr/bin/env python3
"""
Import hs issues markdown into brain `issues` (cloud canonical).

Source:
  ~/.harmoni-state/projects/<slug>/issues/*.md   (excluding memory/ subfolders)

Strategy:
  - Frontmatter -> typed columns; body = post-frontmatter content
  - Upsert by globally-unique `code` (per Codex round-1 P1.1 fix)
  - source_file (vault-relative) + content_fingerprint enable idempotent re-runs
  - project_slug derived from code prefix (uppercase) so the
    `code like project_slug || '-%'` check passes

Env (source ~/.harmoni-state/secrets/brain-supabase.env):
  SUPABASE_DB_URL

Usage:
  .venv/bin/python importers/import_hs_issues.py --dry-run
  .venv/bin/python importers/import_hs_issues.py --limit 5
  .venv/bin/python importers/import_hs_issues.py
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
import psycopg2.extras
import yaml

HOME = Path.home()
ROOT = HOME / ".harmoni-state" / "projects"
EXCLUDE_FRAGS = ("/memory/", "/archive/", "/.trash/")

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)

# Map free-form types from markdown onto schema-allowed types.
# Schema allows: task, bug, epic, spike, research, chore
TYPE_MAP = {
    "bug": "bug",
    "fix": "bug",
    "incident": "bug",
    "research": "research",
    "spike": "spike",
    "epic": "epic",
    "chore": "chore",
    "process": "chore",
    "decision": "chore",
    "investigation": "spike",
    "task": "task",
    "feature": "task",
    "enhancement": "task",
    "infrastructure": "task",
}

STATUS_MAP = {
    "backlog": "backlog",
    "in-progress": "in-progress",
    "review": "review",
    "blocked": "blocked",
    "done": "done",
    "wont-do": "wontfix",
    "wontfix": "wontfix",
}

PRIORITY_MAP = {
    "P0": "critical",
    "P1": "high",
    "P2": "medium",
    "P3": "low",
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}

CODE_RE = re.compile(r"^([A-Z]+)-\d+", re.IGNORECASE)


def _line_fallback(raw: str) -> dict:
    """Forgiving frontmatter parser for cases where YAML chokes on unquoted
    colons in titles (e.g. `title: GRC-010: SA-11 ...`)."""
    out: dict = {}
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        if line[0] in (" ", "\t", "-"):
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            out[key] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
        else:
            out[key] = val
    return out


def load_file(p: Path) -> tuple[dict, str] | None:
    text = p.read_text(errors="replace")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    raw = m.group(1)
    try:
        fm = yaml.safe_load(raw) or {}
        if not isinstance(fm, dict):
            fm = _line_fallback(raw)
    except yaml.YAMLError:
        fm = _line_fallback(raw)
    body = m.group(2).strip()
    return fm, body


def normalize_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        return [x.strip() for x in s.split(",") if x.strip()]
    return [str(v).strip()]


def vault_relpath(p: Path) -> str:
    try:
        return f"~/{p.relative_to(HOME)}"
    except ValueError:
        return str(p)


def fingerprint(code: str, title: str, body: str) -> str:
    return hashlib.sha256(f"issue|{code}|{title}|{body}".encode()).hexdigest()


def to_row(p: Path) -> dict | None:
    parsed = load_file(p)
    if not parsed:
        return None
    fm, body = parsed

    code = str(fm.get("id") or "").strip()
    if not code:
        return None
    m = CODE_RE.match(code)
    if not m:
        return None
    code = code.upper()
    project_slug = m.group(1).upper()

    title = str(fm.get("title") or code).strip()

    raw_type = str(fm.get("type") or "task").strip().lower()
    issue_type = TYPE_MAP.get(raw_type, "task")

    raw_status = str(fm.get("status") or "backlog").strip().lower()
    status = STATUS_MAP.get(raw_status, "backlog")

    raw_priority = str(fm.get("priority") or "medium").strip()
    priority = PRIORITY_MAP.get(raw_priority, PRIORITY_MAP.get(raw_priority.lower(), "medium"))

    owner = fm.get("owner")
    assignee = str(owner).strip().lower() if owner else None
    if assignee in ("", "none", "null"):
        assignee = None

    effort = fm.get("effort")
    effort_hours = None
    if effort not in (None, "", "null"):
        try:
            effort_hours = float(str(effort).split()[0])
        except (ValueError, AttributeError):
            effort_hours = None

    due = fm.get("due")
    due_date = None
    if due not in (None, "", "null"):
        due_date = str(due).strip()

    parent = fm.get("parent")
    parent_code = str(parent).strip().upper() if parent and str(parent).strip() else None

    controls = normalize_list(fm.get("controls"))
    tags = normalize_list(fm.get("tags"))

    jira_key = fm.get("jira_key") or fm.get("jira")
    jira_key = str(jira_key).strip() if jira_key else None

    src = vault_relpath(p)
    fp = fingerprint(code, title, body)

    return {
        "code": code,
        "project_slug": project_slug,
        "title": title,
        "body": body or None,
        "type": issue_type,
        "status": status,
        "priority": priority,
        "assignee": assignee,
        "effort_hours": effort_hours,
        "due_date": due_date,
        "parent_code": parent_code,
        "controls": controls,
        "tags": tags,
        "jira_key": jira_key,
        "source_file": src,
        "content_fingerprint": fp,
    }


UPSERT_SQL = """
insert into issues (
  code, project_slug, title, body, type, status, priority,
  assignee, effort_hours, due_date, parent_code,
  controls, tags, jira_key, source_file, content_fingerprint
) values (
  %(code)s, %(project_slug)s, %(title)s, %(body)s, %(type)s, %(status)s, %(priority)s,
  %(assignee)s, %(effort_hours)s, %(due_date)s, %(parent_code)s,
  %(controls)s, %(tags)s, %(jira_key)s, %(source_file)s, %(content_fingerprint)s
)
on conflict (code) do update set
  project_slug = excluded.project_slug,
  title = excluded.title,
  body = excluded.body,
  type = excluded.type,
  status = excluded.status,
  priority = excluded.priority,
  assignee = excluded.assignee,
  effort_hours = excluded.effort_hours,
  due_date = excluded.due_date,
  parent_code = excluded.parent_code,
  controls = excluded.controls,
  tags = excluded.tags,
  jira_key = coalesce(excluded.jira_key, issues.jira_key),
  source_file = excluded.source_file,
  content_fingerprint = excluded.content_fingerprint,
  updated_at = now()
where issues.content_fingerprint is distinct from excluded.content_fingerprint
returning xmax = 0 as inserted;
"""


def find_files(root: Path):
    for p in root.rglob("*.md"):
        sp = str(p)
        if "/issues/" not in sp:
            continue
        if any(frag in sp for frag in EXCLUDE_FRAGS):
            continue
        yield p


def upsert_one(dsn: str, row: dict) -> tuple[str, str]:
    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(UPSERT_SQL, row)
                res = cur.fetchone()
        if res is None:
            return ("nochange", row["code"])
        return ("inserted" if res[0] else "updated", row["code"])
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    # Prefer the IPv4-reachable pooler; fall back to direct (IPv6-only on Mac).
    dsn = os.environ.get("SUPABASE_DB_POOLER_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn and not args.dry_run:
        print(
            "error: SUPABASE_DB_POOLER_URL/SUPABASE_DB_URL not set; source brain-supabase.env",
            file=sys.stderr,
        )
        return 2

    files = list(find_files(ROOT))
    files.sort()
    if args.limit:
        files = files[: args.limit]

    print(f"discovered {len(files)} issue markdown files under {ROOT}")

    rows: list[dict] = []
    skipped = 0
    for p in files:
        row = to_row(p)
        if row is None:
            skipped += 1
            if args.verbose:
                print(f"  SKIP {p}")
            continue
        rows.append(row)

    print(f"parsed {len(rows)} valid rows; skipped {skipped}")

    if args.dry_run:
        for r in rows[:5]:
            print(
                f"  {r['code']:<12} {r['project_slug']:<6} {r['type']:<8} {r['status']:<12} {r['priority']:<8} tags={r['tags']}"
            )
        return 0

    counts = {"inserted": 0, "updated": 0, "nochange": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(upsert_one, dsn, r): r for r in rows}
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                outcome, code = fut.result()
                counts[outcome] += 1
                if args.verbose:
                    print(f"  {outcome:<10} {code}")
            except Exception as e:
                counts["error"] += 1
                print(f"  ERROR {r['code']}: {type(e).__name__}: {str(e)[:160]}")

    print(
        f"done: inserted={counts['inserted']} updated={counts['updated']} "
        f"nochange={counts['nochange']} error={counts['error']}"
    )
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
