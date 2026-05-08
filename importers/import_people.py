#!/usr/bin/env python3
"""
Import personality matrix + contact directory into brain `people` table.

Two sources unified on slug (filename stem):
  ~/.harmoni-state/memory/people/*.md          (rich personas, 18 files)
  ~/.harmoni-state/comms/<name>.md             (light contact rollups, ~105 files)

When the same slug appears in both places, the rich `memory/people/` row wins
on persona fields (how_they_think, etc.); the rollup contributes contact_info.

Section parser (best-effort):
  ## Role / ## Background        -> background
  ## How They Think              -> how_they_think
  ## How to Work With Them       -> how_to_work_with
  ## Communication Style         -> communication_style
  ## Core Values / ## Beliefs    -> core_values
  ## Key Insights / ## Guidance  -> key_insights
  ## Regular Syncs / Cadence     -> regular_syncs
  ## Engagement / ## Relationship-> relationship_summary

Env: SUPABASE_DB_POOLER_URL.
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
PEOPLE_DIR = HOME / ".harmoni-state" / "memory" / "people"
COMMS_DIR = HOME / ".harmoni-state" / "comms"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)
H2_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
EMAIL_RE = re.compile(r"[\w\.\-+]+@[\w\.-]+\.\w+")
PHONE_RE = re.compile(r"(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}")

SECTION_MAP = {
    "role": "background",
    "background": "background",
    "about": "background",
    "how they think": "how_they_think",
    "how to work with them": "how_to_work_with",
    "how to work with": "how_to_work_with",
    "working with them": "how_to_work_with",
    "communication style": "communication_style",
    "core values and beliefs": "core_values",
    "core values": "core_values",
    "values and beliefs": "core_values",
    "key insights": "key_insights",
    "key guidance": "key_insights",
    "guidance": "key_insights",
    "advice": "key_insights",
    "regular syncs": "regular_syncs",
    "cadence": "regular_syncs",
    "engagement": "relationship_summary",
    "relationship": "relationship_summary",
    "context": "relationship_summary",
}

KIND_FROM_FRONTMATTER = {
    "internal": "internal",
    "team": "internal",
    "external": "external",
    "vendor": "vendor",
    "advisor": "advisor",
    "mentor": "advisor",
    "customer": "customer",
    "partner": "partner",
    "person": "external",
    "contact": "external",
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


def parse_sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    matches = list(H2_SECTION_RE.finditer(body))
    for i, m in enumerate(matches):
        heading = m.group(1).strip().lower().rstrip(":")
        canonical = SECTION_MAP.get(heading)
        if not canonical:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section = body[start:end].strip()
        if section:
            existing = out.get(canonical, "")
            out[canonical] = (existing + "\n\n" + section).strip() if existing else section
    return out


def name_from_h1(body: str) -> str | None:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def slug_to_name(slug: str) -> str:
    parts = [w for w in slug.split("-") if w]
    return " ".join(p.capitalize() for p in parts)


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
        fm, body = {}, text.strip()

    slug = p.stem.lower()
    name = name_from_h1(body) or slug_to_name(slug)

    fm_role = str(fm.get("role") or "").strip().lower()
    fm_type = str(fm.get("type") or "").strip().lower()
    raw_kind = fm.get("kind") or fm_role or fm_type
    kind = KIND_FROM_FRONTMATTER.get(str(raw_kind).strip().lower(), "external")

    sections = parse_sections(body)

    # Best-effort email/phone extraction from body.
    email = None
    em = EMAIL_RE.search(body)
    if em:
        email = em.group(0)
    phone = None
    pm = PHONE_RE.search(body)
    if pm:
        cand = pm.group(0)
        # Avoid grabbing things like "2026-04-15" or NIST control numbers.
        digits = re.sub(r"\D", "", cand)
        if 10 <= len(digits) <= 11:
            phone = cand

    company = fm.get("company") or fm.get("organization")
    company = str(company).strip() if company else None

    last_contact = fm.get("last-contact") or fm.get("last_contact")
    last_contact_at = None
    if last_contact:
        s = str(last_contact).strip()
        last_contact_at = s if "T" in s or " " in s else f"{s}T12:00:00"

    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    src = vault_relpath(p)
    fp = hashlib.sha256(f"person|{src}|{name}|{body}".encode()).hexdigest()

    return {
        "slug": slug,
        "name": name,
        "kind": kind,
        "role": fm.get("title") or fm.get("position"),
        "company": company,
        "email": email,
        "phone": phone,
        "relationship_summary": sections.get("relationship_summary"),
        "background": sections.get("background"),
        "how_they_think": sections.get("how_they_think"),
        "how_to_work_with": sections.get("how_to_work_with"),
        "communication_style": sections.get("communication_style"),
        "core_values": sections.get("core_values"),
        "key_insights": sections.get("key_insights"),
        "regular_syncs": sections.get("regular_syncs"),
        "last_contact_at": last_contact_at,
        "tags": tags,
        "body": body,
        "source_file": src,
        "content_fingerprint": fp,
        "metadata": {"raw_role": fm_role, "raw_type": fm_type},
    }


def find_files():
    """Yield (path, priority). Higher priority overwrites lower on slug collision."""
    if COMMS_DIR.exists():
        for p in sorted(COMMS_DIR.glob("*.md")):
            if DATE_RE.search(p.stem):
                continue  # event file, not a contact rollup
            if p.stem.startswith("CALL_LOG") or p.stem.startswith("COMMS_INDEX"):
                continue
            yield p, 1  # rollup is lower priority
    if PEOPLE_DIR.exists():
        for p in sorted(PEOPLE_DIR.glob("*.md")):
            yield p, 10  # rich persona wins on collision


UPSERT_SQL = """
insert into people (
  slug, name, kind, role, company, email, phone,
  relationship_summary, background, how_they_think, how_to_work_with,
  communication_style, core_values, key_insights, regular_syncs,
  last_contact_at, tags, body, source_file, content_fingerprint, metadata
) values (
  %(slug)s, %(name)s, %(kind)s, %(role)s, %(company)s, %(email)s, %(phone)s,
  %(relationship_summary)s, %(background)s, %(how_they_think)s, %(how_to_work_with)s,
  %(communication_style)s, %(core_values)s, %(key_insights)s, %(regular_syncs)s,
  %(last_contact_at)s::timestamptz, %(tags)s, %(body)s,
  %(source_file)s, %(content_fingerprint)s, %(metadata)s::jsonb
)
on conflict (slug) do update set
  name = excluded.name,
  kind = excluded.kind,
  role = coalesce(excluded.role, people.role),
  company = coalesce(excluded.company, people.company),
  email = coalesce(excluded.email, people.email),
  phone = coalesce(excluded.phone, people.phone),
  relationship_summary = coalesce(excluded.relationship_summary, people.relationship_summary),
  background = coalesce(excluded.background, people.background),
  how_they_think = coalesce(excluded.how_they_think, people.how_they_think),
  how_to_work_with = coalesce(excluded.how_to_work_with, people.how_to_work_with),
  communication_style = coalesce(excluded.communication_style, people.communication_style),
  core_values = coalesce(excluded.core_values, people.core_values),
  key_insights = coalesce(excluded.key_insights, people.key_insights),
  regular_syncs = coalesce(excluded.regular_syncs, people.regular_syncs),
  last_contact_at = coalesce(excluded.last_contact_at, people.last_contact_at),
  tags = excluded.tags,
  body = excluded.body,
  source_file = excluded.source_file,
  content_fingerprint = excluded.content_fingerprint,
  metadata = people.metadata || excluded.metadata,
  updated_at = now()
returning xmax = 0 as inserted;
"""


def upsert_one(dsn: str, row: dict) -> tuple[str, str]:
    row = dict(row)
    row["metadata"] = json.dumps(row["metadata"])
    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(UPSERT_SQL, row)
                res = cur.fetchone()
        return ("inserted" if res[0] else "updated", row["slug"])
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    # Sort by priority so high-priority rich personas land last and win.
    paths_with_priority = sorted(find_files(), key=lambda x: x[1])
    if args.limit:
        paths_with_priority = paths_with_priority[: args.limit]
    print(f"discovered {len(paths_with_priority)} candidate files")

    rows: list[dict] = []
    skipped = 0
    for p, prio in paths_with_priority:
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
        from collections import Counter
        print("by kind:", dict(Counter(r["kind"] or "?" for r in rows)))
        for r in rows[:5]:
            think = "yes" if r.get("how_they_think") else "no"
            print(f"  {r['slug']:<24} {r['kind']:<10} {(r.get('company') or '-')[:20]:<22} how_they_think={think}")
        return 0

    dsn = os.environ.get("SUPABASE_DB_POOLER_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("error: SUPABASE_DB_POOLER_URL/SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    counts = {"inserted": 0, "updated": 0, "error": 0}
    # NOT parallel — we must apply rollup-first then rich-persona-last to win on collision.
    for r in rows:
        try:
            outcome, _ = upsert_one(dsn, r)
            counts[outcome] += 1
        except Exception as e:
            counts["error"] += 1
            print(f"  ERROR {r['slug']}: {type(e).__name__}: {str(e)[:200]}")

    print(f"done: inserted={counts['inserted']} updated={counts['updated']} error={counts['error']}")
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
