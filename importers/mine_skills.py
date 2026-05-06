#!/usr/bin/env python3
"""
Mine local skills into the brain `skills` table via the skill_upsert MCP tool.

Sources walked:
  ~/.claude/skills/<slug>/SKILL.md           (user-installed)
  ~/.harmoni-state/skills/<slug>/SKILL.md    (harmoni-managed)
  ~/.claude/plugins/cache/.../skills/<slug>/SKILL.md   (plugin-bundled)

Slug = parent directory name. First-seen wins on collision (log dupes).
Tag with origin: 'user' | 'harmoni' | 'plugin:<plugin-name>'.

Env required (source ~/.harmoni-state/secrets/brain-supabase.env):
  SUPABASE_URL, MCP_ACCESS_KEY

Usage:
  python importers/mine_skills.py --dry-run   # print what would be upserted
  python importers/mine_skills.py             # actually upsert
"""
import os
import re
import sys
import json
import argparse
from pathlib import Path

import requests

MCP_URL = os.environ["SUPABASE_URL"].rstrip("/") + "/functions/v1/mcp"
ACCESS_KEY = os.environ["MCP_ACCESS_KEY"]

ROOTS = [
    (Path.home() / ".claude" / "skills", "user"),
    (Path.home() / ".harmoni-state" / "skills", "harmoni"),
    (Path.home() / ".claude" / "plugins" / "cache", "plugin"),
]

# Slash command roots. Slugs get `cmd:` prefix to avoid collisions with skills
# of the same name (e.g. /grc-weekly command + grc-weekly SKILL.md). Tagged
# `kind:command` so callers can filter via skills_list(tag='kind:command').
COMMAND_ROOTS = [
    (Path.home() / ".claude" / "commands", "user"),
]

# Plugin sources whose skills are off-topic for this user (FedRAMP/infra/security).
# Override with --include-excluded if you ever want them back.
EXCLUDED_TAG_PREFIXES = (
    "plugin:knowledge-work-plugins",
)

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)

def parse_frontmatter(text: str):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    front_block, body = m.group(1), m.group(2).lstrip("\n")
    front = {}
    current_key = None
    for line in front_block.splitlines():
        if not line.strip():
            continue
        if line.startswith(("  ", "\t")) and current_key:
            front[current_key] = (front.get(current_key, "") + " " + line.strip()).strip()
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            current_key = key.strip()
            front[current_key] = value.strip().strip("'").strip('"')
    return front, body

def origin_tag(path: Path, origin_label: str) -> str:
    if origin_label != "plugin":
        return origin_label
    parts = path.parts
    try:
        idx = parts.index("cache")
        if idx + 1 < len(parts):
            return f"plugin:{parts[idx + 1]}"
    except ValueError:
        pass
    return "plugin"

def find_skill_files():
    """Yield (slug, path, origin_tag) tuples for each SKILL.md found."""
    for root, label in ROOTS:
        if not root.exists():
            continue
        for skill_md in root.rglob("SKILL.md"):
            slug = skill_md.parent.name
            yield slug, skill_md, origin_tag(skill_md, label)

def find_command_files():
    """Yield (slug, path, origin_tag) for each slash command markdown file."""
    for root, label in COMMAND_ROOTS:
        if not root.exists():
            continue
        for cmd_md in root.glob("*.md"):
            slug = f"cmd:{cmd_md.stem}"
            yield slug, cmd_md, label

def mcp_upsert(payload, dry_run=False):
    if dry_run:
        return {"dry_run": True, "slug": payload["slug"], "tags": payload["tags"]}
    r = requests.post(
        MCP_URL,
        headers={
            "x-brain-key": ACCESS_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "skill_upsert", "arguments": payload},
        },
        timeout=30,
    )
    r.raise_for_status()
    # Response is SSE-format: "event: message\ndata: {...}\n"
    for line in r.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    return r.text

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-excluded", action="store_true",
                    help="Bypass EXCLUDED_TAG_PREFIXES filter (off-topic plugin skills).")
    args = ap.parse_args()

    seen = {}
    skipped = []

    sources = [
        (find_skill_files(), "skill", False),
        (find_command_files(), "command", True),
    ]

    for source_iter, kind, is_command in sources:
        for slug, path, tag in source_iter:
            if slug in seen:
                skipped.append((slug, path, "dupe"))
                continue
            if not args.include_excluded and any(tag.startswith(p) for p in EXCLUDED_TAG_PREFIXES):
                skipped.append((slug, path, f"excluded:{tag}"))
                continue
            try:
                text = path.read_text()
            except Exception as e:
                skipped.append((slug, path, f"read-error: {e}"))
                continue
            front, body = parse_frontmatter(text)

            if is_command:
                # Slash commands are flat .md files without frontmatter.
                # Description = first non-empty line; name humanizes the slug.
                desc_line = next(
                    (ln.strip() for ln in body.splitlines() if ln.strip()),
                    f"slash command /{path.stem}",
                )
                name = path.stem
                description = desc_line.lstrip("# ").strip()
                tags = [tag, "kind:command"]
            else:
                name = front.get("name") or slug
                description = front.get("description") or front.get("summary") or f"{slug} skill"
                existing_tags = []
                if "tags" in front:
                    tag_str = front["tags"].strip("[]")
                    existing_tags = [t.strip().strip("'\"") for t in tag_str.split(",") if t.strip()]
                tags = list({tag, *existing_tags})

            if not body.strip():
                skipped.append((slug, path, "empty-body"))
                continue
            seen[slug] = (path, tag)
            payload = {
                "slug": slug,
                "name": name[:200],
                "description": description[:1000],
                "generic_body": body,
                "tags": tags,
            }
            mcp_upsert(payload, dry_run=args.dry_run)
            marker = "DRY" if args.dry_run else "UPSERT"
            kind_label = "cmd" if is_command else "skl"
            print(f"[{marker}:{kind_label}] {slug:40s} ({tag:20s}) [{len(body):>5}b] tags={tags}")

    print()
    print(f"Imported: {len(seen)}")
    print(f"Skipped:  {len(skipped)}")
    if skipped:
        print("\nSkipped detail:")
        for slug, path, reason in skipped[:20]:
            print(f"  {slug:30s} {reason:20s} {path}")

if __name__ == "__main__":
    main()
