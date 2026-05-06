#!/usr/bin/env python3
"""
Dump local ~/.harmoni-state markdown files into brain `thoughts` via capture_thought.

Each file:
  - Frontmatter stripped
  - First 8000 chars kept (truncate marker if longer)
  - Source path prefixed for retrieval context
  - Sent through capture_thought MCP tool (does embedding + metadata extraction)
  - content_fingerprint dedup means re-runs are idempotent (but waste API calls)

Roots walked:
  ~/.harmoni-state/memory, projects, comms, output, research

Env required (source ~/.harmoni-state/secrets/brain-supabase.env):
  SUPABASE_URL, MCP_ACCESS_KEY

Usage:
  python3 importers/dump_hs_memory.py --dry-run            # count + preview
  python3 importers/dump_hs_memory.py --limit 5            # tiny live test
  python3 importers/dump_hs_memory.py --workers 6          # full run, 6 concurrent
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

MCP_URL = os.environ["SUPABASE_URL"].rstrip("/") + "/functions/v1/mcp"
ACCESS_KEY = os.environ["MCP_ACCESS_KEY"]
HOME = Path.home()

ROOTS = [
    HOME / ".harmoni-state" / "memory",
    HOME / ".harmoni-state" / "projects",
    HOME / ".harmoni-state" / "comms",
    HOME / ".harmoni-state" / "output",
    HOME / ".harmoni-state" / "research",
]

# Skip transient / vendored / archived paths.
EXCLUDE_FRAGS = (
    "/vendor/",
    "/node_modules/",
    "/.obsidian/",
    "/.git/",
    "/archive/",
    "/.trash/",
)

MIN_CHARS = 50
MAX_CHARS = 8000  # text-embedding-3-small caps at 8191 tokens; this fits comfortably

FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n(.*)", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    m = FRONTMATTER_RE.match(text)
    return m.group(1) if m else text


def find_files():
    for root in ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*.md"):
            sp = str(p)
            if any(frag in sp for frag in EXCLUDE_FRAGS):
                continue
            yield p


def relpath(p: Path) -> str:
    try:
        return f"~/{p.relative_to(HOME)}"
    except ValueError:
        return str(p)


def build_content(body: str, source_path: str) -> str:
    truncated = body[:MAX_CHARS]
    out = f"[Source: {source_path}]\n\n{truncated}"
    if len(body) > MAX_CHARS:
        out += f"\n\n[Truncated; original_size={len(body)}]"
    return out


def capture(content: str) -> dict:
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
            "params": {"name": "capture_thought", "arguments": {"content": content}},
        },
        timeout=180,
    )
    r.raise_for_status()
    for line in r.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    return {"raw": r.text}


def process_file(p: Path, dry_run: bool) -> tuple:
    """Returns (path, status, detail, content_len)."""
    try:
        text = p.read_text(errors="replace")
    except Exception as e:
        return (p, "read-error", str(e)[:120], 0)
    body = strip_frontmatter(text).strip()
    if len(body) < MIN_CHARS:
        return (p, "too-small", f"{len(body)}b", 0)
    content = build_content(body, relpath(p))
    if dry_run:
        return (p, "dry", "ok", len(content))
    try:
        result = capture(content)
        if "error" in str(result).lower() and "isError" in str(result):
            return (p, "mcp-error", str(result)[:200], len(content))
        return (p, "captured", "ok", len(content))
    except requests.exceptions.RequestException as e:
        return (p, "http-error", str(e)[:200], len(content))
    except Exception as e:
        return (p, "error", f"{type(e).__name__}: {str(e)[:160]}", len(content))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Count files + preview, no API calls")
    ap.add_argument("--limit", type=int,
                    help="Process only the first N files (for testing)")
    ap.add_argument("--workers", type=int, default=4,
                    help="Concurrent capture_thought calls (default 4)")
    args = ap.parse_args()

    files = list(find_files())
    if args.limit:
        files = files[: args.limit]

    print(f"Found {len(files)} markdown files. workers={args.workers} dry_run={args.dry_run}")
    print(f"Min={MIN_CHARS}b  Max={MAX_CHARS}b (truncate beyond)")
    print()

    started = time.time()
    counts: dict = {}
    errors: list = []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_file, p, args.dry_run): p for p in files}
        for i, fut in enumerate(as_completed(futures), 1):
            p, status, detail, size = fut.result()
            counts[status] = counts.get(status, 0) + 1
            if status in ("read-error", "http-error", "mcp-error", "error"):
                errors.append((p, status, detail))
                print(f"[{i:>4}/{len(files)}] {status:<11s} {relpath(p)}  -- {detail}")
            elif i % 25 == 0 or i == len(files):
                elapsed = time.time() - started
                rate = i / elapsed if elapsed else 0
                eta = (len(files) - i) / rate if rate else 0
                print(f"[{i:>4}/{len(files)}] {rate:.1f}/s  eta={eta:>5.0f}s  last={status} {relpath(p)}")

    print()
    print("=" * 60)
    elapsed = time.time() - started
    print(f"Total: {len(files)}  elapsed={elapsed:.1f}s  rate={len(files)/elapsed:.2f}/s")
    for k in sorted(counts):
        print(f"  {k:<12s} {counts[k]}")
    if errors:
        print(f"\nErrors: {len(errors)} (first 10)")
        for p, status, detail in errors[:10]:
            print(f"  {status:<11s} {relpath(p)}  -- {detail}")


if __name__ == "__main__":
    main()
