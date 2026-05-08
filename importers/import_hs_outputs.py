#!/usr/bin/env python3
"""
Import hs output deliverables markdown into brain `outputs` (cloud canonical).

Source: ~/.harmoni-state/output/*.md (flat dir; subfolders skipped for v1).

Mapping:
  - filename: basename of file
  - title: first H1 in body (fallback: filename slug)
  - kind: heuristic from filename/title (runbook|rfi|rfp|plan|report|quote|memo|analysis|other)
  - project: frontmatter project (lowercased)
  - related_issue_code: frontmatter `issue` if present
  - recipient: frontmatter recipient
  - description: first paragraph or summary
  - registered_at: frontmatter created (or filename date)

Idempotency: source_file + content_fingerprint partial unique indexes (codex P1.2 fix).
ON CONFLICT (source_file).

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
ROOT = HOME / ".harmoni-state" / "output"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)
DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
COMPACT_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$")

KIND_KEYWORDS = [
    ("runbook", "runbook"),
    ("rfi", "rfi"),
    ("rfp", "rfp"),
    ("plan", "plan"),
    ("review", "analysis"),
    ("analysis", "analysis"),
    ("audit", "report"),
    ("report", "report"),
    ("quote", "quote"),
    ("pricing", "quote"),
    ("memo", "memo"),
    ("briefing", "memo"),
    ("update", "memo"),
    ("recommendation", "memo"),
    ("rollup", "report"),
    ("research", "analysis"),
]


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
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return f"{s}T12:00:00"
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


def detect_kind(name: str, title: str) -> str:
    haystack = f"{name} {title}".lower()
    for kw, kind in KIND_KEYWORDS:
        if kw in haystack:
            return kind
    return "other"


def first_paragraph(body: str) -> str:
    paras: list[str] = []
    cur: list[str] = []
    for line in body.splitlines():
        line = line.rstrip()
        if not line:
            if cur:
                paras.append(" ".join(cur))
                cur = []
            continue
        if line.startswith("#") or line.startswith(">"):
            continue
        cur.append(line)
        if len("".join(cur)) > 400:
            break
    if cur:
        paras.append(" ".join(cur))
    return paras[0] if paras else ""


def parse(p: Path) -> dict | None:
    text = p.read_text(errors="replace")
    m = FRONTMATTER_RE.match(text)
    if m:
        raw_fm, body = m.group(1), m.group(2).strip()
        try:
            fm = yaml.safe_load(raw_fm) or {}
            if not isinstance(fm, dict):
                fm = _line_fallback(raw_fm)
        except yaml.YAMLError:
            fm = _line_fallback(raw_fm)
    else:
        fm = {}
        body = text.strip()

    name = p.name
    stem = p.stem

    # Title: first H1 in body, fallback to filename slug.
    title = ""
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    if not title:
        slug = DATE_PREFIX_RE.sub("", stem).lstrip("-")
        title = slug.replace("-", " ").strip() or stem

    fb_date = None
    dm = DATE_PREFIX_RE.match(stem)
    if dm:
        fb_date = dm.group(1)
    registered_at = normalize_ts(fm.get("created"), fb_date)

    kind = detect_kind(name, title)

    project = fm.get("project")
    if project:
        project = str(project).strip().lower().rstrip(",").strip()
        if project in ("", "none", "null"):
            project = None

    recipient = fm.get("recipient")
    recipient = str(recipient).strip() if recipient else None

    related = fm.get("issue") or fm.get("related_issue") or fm.get("related_issue_code")
    related = str(related).strip().upper() if related else None

    description = first_paragraph(body)
    if not description:
        description = title

    src = vault_relpath(p)
    fp = hashlib.sha256(
        f"output|{src}|{registered_at}|{title}".encode()
    ).hexdigest()

    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    return {
        "filename": name,
        "title": title[:500],
        "kind": kind,
        "project": project,
        "related_issue_code": related,
        "recipient": recipient,
        "description": description[:2000],
        "source_file": src,
        "registered_at": registered_at,
        "content_fingerprint": fp,
        "metadata": {"tags": tags},
    }


UPSERT_SQL = """
insert into outputs (
  filename, title, kind, project, related_issue_code, recipient,
  description, source_file, registered_at, content_fingerprint, metadata
) values (
  %(filename)s, %(title)s, %(kind)s, %(project)s, %(related_issue_code)s,
  %(recipient)s, %(description)s, %(source_file)s,
  %(registered_at)s::timestamptz, %(content_fingerprint)s, %(metadata)s::jsonb
)
on conflict (source_file) where source_file is not null
do update set
  filename = excluded.filename,
  title = excluded.title,
  kind = excluded.kind,
  project = coalesce(excluded.project, outputs.project),
  related_issue_code = coalesce(excluded.related_issue_code, outputs.related_issue_code),
  recipient = coalesce(excluded.recipient, outputs.recipient),
  description = excluded.description,
  registered_at = excluded.registered_at,
  content_fingerprint = excluded.content_fingerprint,
  metadata = outputs.metadata || excluded.metadata,
  updated_at = now()
returning xmax = 0 as inserted;
"""


def find_files():
    if not ROOT.exists():
        return
    for p in sorted(ROOT.glob("*.md")):
        if p.name.startswith("_"):
            continue  # skip stub/scratch files like _stage3-schedule-notes.md
        yield p


def upsert_one(dsn: str, row: dict) -> tuple[str, str]:
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
    args = ap.parse_args()

    files = list(find_files())
    if args.limit:
        files = files[: args.limit]
    print(f"discovered {len(files)} output files in {ROOT}")

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
            print(f"  {r['kind']:<10} {r['project'] or '-':<8} {r['title'][:60]}")
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
