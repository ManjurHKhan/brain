---
type: plan
tags: [brain, implementation-plan, ai-memory]
status: draft
created: 2026-05-04
---

> Links: [[project_brain_design]] | [[decision_brain_replaces_hs_go]] | [[DEV-006]]

# brain v1 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a personal cloud "brain" in 2 weeks: OB1-vendored Supabase Free deployment + skills registry + typed issues/todos + MCP integration with Claude Code, Codex, Gemini, and Claude iOS — replacing the **hs Python** CLI's issue/todo ownership for cross-AI cloud access. (hs Go rewrite was paused 2026-04-30 and is now formally abandoned per user decision 2026-05-05 — hs Python remains the local CLI fallback.)

**Architecture:** Fresh `manjurhkhan/brain` repo → vendor (copy-and-adapt) OB1's reusable pieces (bootstrap SQL, MCP edge function template, embed-worker, dedup pattern, search SQL) into it → enhance schema with `skills`, `skill_variants`, `issues`, `issue_comments`, `issue_status_history`, `todos` → mine existing skills + import 114 hs issues → wire 4 AI clients via MCP. Memory layer is a flat `thoughts` table (vendored from OB1), no spatial structure, no KG, no offline mode. No upstream OB1 sync — pure vendor model per Codex's review.

**Tech Stack:** Supabase Free (Postgres + pgvector + Edge Functions in Deno/TS) | OpenRouter `text-embedding-3-small` (1536 dim) | GitHub Actions free tier (cron pings) | Python for one-shot importers | bash for shims.

---

## Locked decisions (resolved 2026-05-05)

| # | Decision | Rationale |
|---|---|---|
| 1 | Supabase region: **`us-east-1`** | User in Texas, region not latency-critical; us-east-1 is the most-used / lowest-risk region. Easy to migrate later (different project + `pg_dump|pg_restore`). |
| 2 | Auth model: **per-AI-client API keys** (Claude Code, Codex, Gemini, mobile, keepalive — 5 keys) | Independent revocation if a laptop or phone is lost. SHA-256 hash stored, raw token only on user's filesystem at `~/.harmoni-state/secrets/brain-keys.env` (chmod 600). |
| 3 | hs cutover style: **parallel-run during testing, then hs Python read-only** | Test brain alongside `hs` (Python). Once verified, demote `hs` to read-only for issues/todos (reversible by toggling slash-command shims back). Comms/sessions/output stay in hs Python permanently — only issues + todos move to cloud canonical. |

---

## Scope locks

| Included | Excluded (push to later) |
|---|---|
| OB1 fork + deploy | Repo rename to `brain` (use `brain` as cloned dir, keep OB1 internals) |
| `thoughts` (OB1 native) for memory | Drawers, wings, rooms |
| `skills` + `skill_variants` | Knowledge graph (entities, edges, temporal) |
| `issues` + `issue_comments` + `issue_status_history` | Comms, sessions, decisions, output as typed entities |
| `todos` | brain Go CLI (revisit in Week 3+) |
| Optimistic concurrency on `issues`/`todos` (`updated_at` check) | Offline queue + sync |
| MCP tools for skills/issues/todos | App-layer encryption |
| hs migration importer (issues + todos + memory dump) | Compat contract / version negotiation |
| GitHub Actions daily ping | Backup tooling beyond `pg_dump` cron |
| Phone (Claude iOS) smoke tests | Multi-user / shared wings |

---

## Architecture decisions (locked)

1. **MCP server = Supabase Edge Function** for v1. Codex flagged this as load-bearing risk; accepted for shipping speed. Replatform to Fly.io if cold-start latency becomes annoying.
2. **OB1 fork = clone-and-diverge**, no upstream sync. Plan acknowledges Codex's correction: vendor-not-fork mentally — we won't pull from upstream, but the directory + history stays as OB1 fork.
3. **Embeddings**: OpenRouter `text-embedding-3-small`. Model + provider stamped on every vector for future re-embedding.
4. **Authoritative source for issues/todos**: cloud (after Week 2 cutover). hs Python becomes read-only for issues/todos (slash commands route to MCP, but `hs` binary still owns comms/sessions/output indefinitely). No archival of hs Python — it stays as the local CLI for everything brain doesn't cover.
5. **Identity for assignees**: plain text field for v1. KG-based identity resolution deferred.
6. **Concurrency**: optimistic concurrency via `updated_at` check on every mutable update. Stale writes rejected with current row returned.

---

## File structure (after Week 2)

```
~/Documents/000-harmoni/brain/             # cloned OB1 fork
├── README.md                              # OB1's, augmented
├── supabase/
│   ├── config.toml
│   ├── migrations/
│   │   ├── <ob1-baseline>.sql             # untouched OB1 migrations
│   │   ├── 20260504000001_skills.sql      # NEW
│   │   ├── 20260511000001_issues.sql      # NEW (Week 2)
│   │   └── 20260511000002_todos.sql       # NEW (Week 2)
│   └── functions/
│       └── mcp/                           # OB1's MCP edge function, extended
│           ├── index.ts                   # MODIFIED: register new tools
│           ├── tools/
│           │   ├── thoughts.ts            # OB1 native
│           │   ├── skills.ts              # NEW (Week 1)
│           │   ├── issues.ts              # NEW (Week 2)
│           │   └── todos.ts               # NEW (Week 2)
│           └── lib/
│               ├── auth.ts                # NEW: per-client key validation
│               └── embed.ts               # OB1 native, reused
├── importers/
│   ├── mine_skills.py                     # NEW (Week 1)
│   ├── dump_hs_memory.py                  # NEW (Week 1)
│   ├── import_hs_issues.py                # NEW (Week 2)
│   └── import_hs_todos.py                 # NEW (Week 2)
├── shims/
│   ├── issue.sh                           # NEW (Week 2): /issue → MCP
│   └── todo.sh                            # NEW (Week 2): /todo → MCP
├── ops/
│   ├── pg_dump_cron.sh                    # NEW (Week 1)
│   ├── github_actions_ping.yml            # NEW (Week 1)
│   └── doctor.sh                          # NEW (Week 1)
└── docs/
    └── superpowers/plans/
        └── 2026-05-04-brain-v1-implementation-plan.md   # this file
```

---

## Week 1: Memory + skills + AI client wiring (~21h)

### Task 1: Bootstrap brain repo (vendor OB1 pieces, not fork)

**Files:**
- Use existing repo: `manjurhkhan/brain` (just created, empty with README)
- Create: `~/Documents/000-harmoni/brain/` (clone)
- Create: `~/Documents/000-harmoni/brain/docs/superpowers/plans/2026-05-04-brain-v1-implementation-plan.md`
- Create: initial directory skeleton matching the file structure in the plan header
- Create: `vendor/OB1-snapshot/` (read-only reference clone of OB1, used for cherry-picking)

**Steps:**

- [ ] **Step 1: Clone the empty brain repo**

```bash
cd ~/Documents/000-harmoni
git clone git@github.com:manjurhkhan/brain.git
cd brain
```

- [ ] **Step 2: Pull OB1 as a reference (NOT as upstream)**

```bash
mkdir -p vendor
git clone --depth=1 https://github.com/NateBJones-Projects/OB1.git vendor/OB1-snapshot
# vendor/OB1-snapshot is read-only reference. We copy specific files out, never sync.
echo "vendor/OB1-snapshot/" >> .gitignore   # keep our repo clean; we cherry-pick files OUT of vendor into our tree
```

- [ ] **Step 3: Copy OB1's reusable pieces into our tree**

Inspect `vendor/OB1-snapshot/` and selectively copy:

```bash
# Schema baseline
mkdir -p supabase/migrations
cp vendor/OB1-snapshot/supabase/migrations/*thoughts*.sql supabase/migrations/   # adjust if naming differs
# May need to copy: pgvector extension setup, match_thoughts function, content_fingerprint pattern

# MCP edge function template
mkdir -p supabase/functions/mcp/lib supabase/functions/mcp/tools
cp -r vendor/OB1-snapshot/supabase/functions/mcp/* supabase/functions/mcp/   # adjust paths to match OB1's actual layout

# Embed worker
cp -r vendor/OB1-snapshot/supabase/functions/embed-worker supabase/functions/ 2>/dev/null || true

# Adapt: rename any 'OB1'-specific identifiers to neutral 'brain'
grep -rl "OB1" supabase/ | xargs sed -i '' 's/OB1/brain/g' 2>/dev/null || true
```

If OB1's layout differs from what's assumed above, adjust manually. The point is to bring the **functional pieces** in without inheriting OB1's repo identity.

- [ ] **Step 4: Create directory skeleton**

```bash
mkdir -p docs/superpowers/plans importers shims ops .github/workflows
```

- [ ] **Step 5: Move plan into repo**

```bash
mv ~/.harmoni-state/output/2026-05-04-brain-v1-implementation-plan.md docs/superpowers/plans/
```

- [ ] **Step 6: Replace README**

```bash
cat > README.md <<'EOF'
# brain

Personal cloud "brain" — single source of memory, skills, and typed entities (issues, todos)
accessible from every AI tool I use (Claude Code, Codex, Gemini, Claude iOS) via MCP.

Stack: Supabase (Postgres + pgvector + Edge Functions) + OpenRouter embeddings.

Origin: vendored from [OB1](https://github.com/NateBJones-Projects/OB1) for the bootstrap;
diverged from there per [implementation plan](docs/superpowers/plans/2026-05-04-brain-v1-implementation-plan.md).
EOF
```

- [ ] **Step 7: Initial bootstrap commit**

```bash
git add .
git commit -m "feat: bootstrap brain repo with vendored OB1 pieces and v1 plan"
git push origin main
```

---

### Task 2: Deploy OB1 to Supabase Free

**Files:**
- Modify: `supabase/config.toml` (project ref)

**Steps:**

- [ ] **Step 1: Create Supabase project**

Browser: https://supabase.com/dashboard → New project → `brain` → region per Open Question #1 → wait for provisioning (~2 min). Save project ref + service role key to `~/.harmoni-state/secrets/brain-supabase.env` (chmod 600).

- [ ] **Step 2: Install Supabase CLI**

```bash
brew install supabase/tap/supabase
supabase --version   # >= 1.150
```

- [ ] **Step 3: Link local repo to remote project**

```bash
cd ~/Documents/000-harmoni/brain
source ~/.harmoni-state/secrets/brain-supabase.env   # exposes SUPABASE_PROJECT_REF, SUPABASE_DB_PASSWORD
supabase link --project-ref "$SUPABASE_PROJECT_REF"
```

- [ ] **Step 4: Push OB1 baseline migrations**

```bash
supabase db push
```

Expected: `thoughts` table + `match_thoughts` function created. Verify in dashboard SQL editor:

```sql
SELECT count(*) FROM thoughts;   -- expect 0
\d thoughts                       -- expect: id, body, embedding, source, created_at, content_fingerprint
```

- [ ] **Step 5: Deploy OB1's MCP edge function**

```bash
supabase functions deploy mcp
curl -i "https://${SUPABASE_PROJECT_REF}.supabase.co/functions/v1/mcp" \
  -H "Authorization: Bearer ${SUPABASE_ANON_KEY}"
# Expect 200 + MCP server response
```

- [ ] **Step 6: Commit Supabase config**

```bash
git add supabase/config.toml
git commit -m "config: link Supabase project for brain"
```

---

### Task 3: Add `skills` + `skill_variants` schema

**Files:**
- Create: `supabase/migrations/20260504000001_skills.sql`

**Steps:**

- [ ] **Step 1: Write skills migration**

```sql
-- supabase/migrations/20260504000001_skills.sql

create table if not exists skills (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  description text not null,
  generic_body text not null,
  tags text[] not null default '{}',
  embedding vector(1536),
  embedding_model text,            -- e.g. 'openai/text-embedding-3-small'
  embedding_provider text,         -- e.g. 'openrouter'
  embedding_version int default 1,
  version int not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index skills_embedding_hnsw on skills using hnsw (embedding vector_cosine_ops);
create index skills_tags_gin on skills using gin (tags);

create table if not exists skill_variants (
  id uuid primary key default gen_random_uuid(),
  skill_id uuid not null references skills(id) on delete cascade,
  vendor text not null check (vendor in ('claude','codex','gemini','openclaw','local')),
  body text not null,
  mode text not null default 'replace' check (mode in ('replace','extend')),
  version int not null default 1,
  created_at timestamptz not null default now(),
  unique (skill_id, vendor)
);

create or replace function set_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end;
$$;

create trigger skills_updated_at before update on skills
  for each row execute function set_updated_at();
```

- [ ] **Step 2: Push migration**

```bash
supabase db push
```

- [ ] **Step 3: Verify in dashboard**

```sql
\d skills
\d skill_variants
SELECT count(*) FROM skills;    -- 0
```

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260504000001_skills.sql
git commit -m "schema: add skills and skill_variants tables"
```

---

### Task 4: Add MCP tools `skills_list` + `skill_get` + `skill_upsert`

**Files:**
- Create: `supabase/functions/mcp/tools/skills.ts`
- Modify: `supabase/functions/mcp/index.ts` (register tools)

**Steps:**

- [ ] **Step 1: Write `skills.ts` tool implementations**

```typescript
// supabase/functions/mcp/tools/skills.ts
import { createClient } from "jsr:@supabase/supabase-js@2";

const sb = () => createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

export async function skillsList(args: { vendor?: string; tag?: string }) {
  let q = sb().from("skills").select("slug, name, description, tags");
  if (args.tag) q = q.contains("tags", [args.tag]);
  const { data, error } = await q.order("slug");
  if (error) throw error;
  if (args.vendor) {
    const { data: variants } = await sb()
      .from("skill_variants").select("skill_id").eq("vendor", args.vendor);
    const variantSet = new Set((variants ?? []).map(v => v.skill_id));
    return data.map(s => ({ ...s, has_variant: variantSet.has(s.id) }));
  }
  return data;
}

export async function skillGet(args: { slug: string; vendor?: string }) {
  const vendor = args.vendor ?? "auto";
  const { data: skill, error } = await sb()
    .from("skills").select("*").eq("slug", args.slug).single();
  if (error) throw error;

  if (vendor === "generic") return { body: skill.generic_body, source: "generic" };

  const { data: variant } = await sb()
    .from("skill_variants").select("*")
    .eq("skill_id", skill.id).eq("vendor", vendor).maybeSingle();

  if (!variant) return { body: skill.generic_body, source: "generic" };
  if (variant.mode === "replace") return { body: variant.body, source: `variant:${vendor}:replace` };
  return { body: skill.generic_body + "\n\n" + variant.body, source: `variant:${vendor}:extend` };
}

export async function skillUpsert(args: {
  slug: string; name: string; description: string;
  generic_body: string; tags?: string[];
}) {
  const { data, error } = await sb().from("skills")
    .upsert({ slug: args.slug, name: args.name, description: args.description,
             generic_body: args.generic_body, tags: args.tags ?? [] },
            { onConflict: "slug" })
    .select().single();
  if (error) throw error;
  return data;
}
```

- [ ] **Step 2: Register tools in `index.ts`**

Read OB1's `supabase/functions/mcp/index.ts` to find the tool registry (likely a `tools: { ... }` map or `server.tool(...)` calls). Add:

```typescript
import { skillsList, skillGet, skillUpsert } from "./tools/skills.ts";

server.tool("skills_list", {
  vendor: z.string().optional(),
  tag: z.string().optional(),
}, skillsList);

server.tool("skill_get", {
  slug: z.string(),
  vendor: z.string().optional(),
}, skillGet);

server.tool("skill_upsert", {
  slug: z.string(),
  name: z.string(),
  description: z.string(),
  generic_body: z.string(),
  tags: z.array(z.string()).optional(),
}, skillUpsert);
```

- [ ] **Step 3: Deploy**

```bash
supabase functions deploy mcp
```

- [ ] **Step 4: Smoke-test**

```bash
curl -X POST "https://${SUPABASE_PROJECT_REF}.supabase.co/functions/v1/mcp" \
  -H "Authorization: Bearer ${SUPABASE_ANON_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"method":"tools/call","params":{"name":"skills_list","arguments":{}}}'
# Expect: {"result": []}
```

- [ ] **Step 5: Commit**

```bash
git add supabase/functions/mcp/tools/skills.ts supabase/functions/mcp/index.ts
git commit -m "mcp: add skills_list, skill_get, skill_upsert tools"
```

---

### Task 5: Skill mining importer

**Files:**
- Create: `importers/mine_skills.py`

**Steps:**

- [ ] **Step 1: Write the importer**

```python
# importers/mine_skills.py
"""
Mine local skills from ~/.claude/skills, ~/.harmoni-state/skills, plugin caches.
Upsert into Supabase skills table via skill_upsert MCP tool.
"""
import os, re, json, sys
from pathlib import Path
import requests

MCP_URL = os.environ["BRAIN_MCP_URL"]   # https://<ref>.supabase.co/functions/v1/mcp
ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

ROOTS = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".harmoni-state" / "skills",
    Path.home() / ".claude" / "plugins" / "cache",
]

def parse_skill_md(path: Path):
    text = path.read_text()
    fm = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if not fm:
        return None
    front = {}
    for line in fm.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            front[k.strip()] = v.strip()
    body = fm.group(2).strip()
    return {
        "slug": front.get("name") or path.parent.name,
        "name": front.get("name") or path.parent.name,
        "description": front.get("description", ""),
        "generic_body": body,
        "tags": [t.strip() for t in front.get("tags","").split(",") if t.strip()],
    }

def mcp_call(tool, args):
    r = requests.post(MCP_URL,
        headers={"Authorization": f"Bearer {ANON_KEY}", "Content-Type": "application/json"},
        json={"method":"tools/call","params":{"name":tool,"arguments":args}})
    r.raise_for_status()
    return r.json()

def main():
    seen = set()
    for root in ROOTS:
        if not root.exists(): continue
        for p in root.rglob("SKILL.md"):
            skill = parse_skill_md(p)
            if not skill: continue
            if skill["slug"] in seen: continue
            seen.add(skill["slug"])
            print(f"upsert {skill['slug']}")
            mcp_call("skill_upsert", skill)
    print(f"done: {len(seen)} skills")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run dry-run (just print, no upload)**

Add `--dry-run` flag, run:
```bash
python importers/mine_skills.py --dry-run | head -50
# Manually verify the skills look right
```

- [ ] **Step 3: Run for real**

```bash
source ~/.harmoni-state/secrets/brain-supabase.env
python importers/mine_skills.py
```

- [ ] **Step 4: Verify in DB**

```sql
SELECT count(*) FROM skills;    -- expect ~50-100
SELECT slug, length(generic_body) FROM skills ORDER BY slug LIMIT 20;
```

- [ ] **Step 5: Trigger embedding backfill**

OB1 has an embed-worker pattern. Adapt to also embed skills.description. Or run a one-shot:

```bash
psql "$SUPABASE_DB_URL" -c "
  UPDATE skills SET embedding_model = NULL WHERE embedding IS NULL;
"
# then trigger the embed-worker edge function
supabase functions invoke embed-worker --no-verify-jwt
```

- [ ] **Step 6: Commit**

```bash
git add importers/mine_skills.py
git commit -m "importers: skill mining from local dirs"
```

---

### Task 6: Wire Claude Code MCP

**Files:**
- Modify: `~/.claude/CLAUDE.md` (bootstrap line)
- Modify: `~/.claude.json` or equivalent (MCP server registration)

**Steps:**

- [ ] **Step 1: Determine Claude Code MCP config path**

```bash
ls ~/.claude.json ~/Library/Application\ Support/Claude/claude.json 2>/dev/null
# pick the one that exists
```

- [ ] **Step 2: Register brain MCP server**

Edit the JSON to add (under `mcpServers`):

```json
"brain": {
  "type": "http",
  "url": "https://${SUPABASE_PROJECT_REF}.supabase.co/functions/v1/mcp",
  "headers": {
    "Authorization": "Bearer ${BRAIN_CLAUDE_KEY}"
  }
}
```

(`BRAIN_CLAUDE_KEY` is the per-client key from Open Question #2 — generated in Task 11.)

- [ ] **Step 3: Add bootstrap line to CLAUDE.md**

Append:
```markdown
## brain MCP

On startup, the `brain` MCP server is available. Use `skills_list()` to discover available skills, `skill_get(slug)` to load. Use `issues_*` and `todos_*` for issue/todo CRUD (after Week 2 cutover). Memory: `thoughts_search(query)` for recall, `thought_capture(body)` for capture.
```

- [ ] **Step 4: Restart Claude Code, verify**

```
> List the brain skills available
```

Expect Claude to call `skills_list` and return the list.

- [ ] **Step 5: Commit (CLAUDE.md only — claude.json is local config, do not commit secrets)**

```bash
git -C ~/.claude add CLAUDE.md  # if .claude is its own git repo
# Otherwise just leave it, document the change in brain repo
```

---

### Task 7: Wire Codex CLI MCP

**Files:**
- Modify: Codex's MCP config (path TBD by `codex --version` + docs)
- Modify: `~/.codex/AGENTS.md` (bootstrap)

**Steps:**

- [ ] **Step 1: Locate Codex config**

```bash
codex config show 2>/dev/null || ls ~/.codex/
```

- [ ] **Step 2: Register MCP server**

Per Codex docs, add to `~/.codex/config.toml` (or equivalent):

```toml
[mcp_servers.brain]
type = "http"
url = "https://${SUPABASE_PROJECT_REF}.supabase.co/functions/v1/mcp"
headers = { Authorization = "Bearer ${BRAIN_CODEX_KEY}" }
```

- [ ] **Step 3: Add bootstrap to AGENTS.md**

Same pattern as Claude Code's CLAUDE.md from Task 6.

- [ ] **Step 4: Smoke test**

```bash
codex "list the brain skills available"
```

Expect a tool-call to `skills_list`.

- [ ] **Step 5: Document in repo (no commit of secrets)**

Add a note in `docs/integrations/codex.md`.

```bash
git add docs/integrations/codex.md
git commit -m "docs: codex MCP integration setup"
```

---

### Task 8: Wire Gemini CLI MCP

**Files:**
- Modify: Gemini CLI MCP config (path per Gemini docs)
- Create: `docs/integrations/gemini.md`

Same shape as Task 7. Gemini CLI MCP support varies by version — verify `gemini mcp list` works.

- [ ] **Step 1: Verify Gemini CLI version supports MCP**

```bash
gemini --version
gemini mcp --help 2>/dev/null
```

- [ ] **Step 2: Register MCP server per Gemini docs (typically `~/.gemini/config.json`)**

```json
"mcpServers": {
  "brain": {
    "type": "http",
    "url": "https://${SUPABASE_PROJECT_REF}.supabase.co/functions/v1/mcp",
    "headers": {"Authorization": "Bearer ${BRAIN_GEMINI_KEY}"}
  }
}
```

- [ ] **Step 3: Bootstrap in Gemini's GEMINI.md or equivalent**

Same content as Claude Code.

- [ ] **Step 4: Smoke test + document + commit doc**

---

### Task 9: hs memory dump → `thoughts`

**Files:**
- Create: `importers/dump_hs_memory.py`

**Steps:**

- [ ] **Step 1: Write the dump importer**

```python
# importers/dump_hs_memory.py
"""
One-shot: walk ~/.harmoni-state/memory/, ~/.harmoni-state/projects/*/research/,
~/.harmoni-state/comms/ — convert each .md file's body to a thoughts row via OB1's
existing thought_capture or direct upsert with content_fingerprint dedup.
"""
import os, hashlib, re
from pathlib import Path
import requests

MCP_URL = os.environ["BRAIN_MCP_URL"]
ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

ROOTS = [
    Path.home() / ".harmoni-state" / "memory",
    Path.home() / ".harmoni-state" / "projects",
    Path.home() / ".harmoni-state" / "comms",
]

def fingerprint(text): return hashlib.sha256(text.encode()).hexdigest()

def strip_frontmatter(text):
    m = re.match(r"^---\n.*?\n---\n(.*)", text, re.DOTALL)
    return m.group(1) if m else text

def main():
    count = 0
    for root in ROOTS:
        if not root.exists(): continue
        for p in root.rglob("*.md"):
            body = strip_frontmatter(p.read_text())
            if len(body.strip()) < 50: continue
            fp = fingerprint(body)
            r = requests.post(MCP_URL,
                headers={"Authorization": f"Bearer {ANON_KEY}", "Content-Type":"application/json"},
                json={"method":"tools/call","params":{"name":"thought_capture","arguments":{
                    "body": body[:8000],   # truncate; full content stays local
                    "source": str(p.relative_to(Path.home())),
                    "metadata": {"content_fingerprint": fp, "local_path": str(p), "size": len(body)}
                }}})
            r.raise_for_status()
            count += 1
            if count % 50 == 0: print(f"... {count}")
    print(f"done: {count} thoughts captured")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run dry-run, then real**

Validate count matches expected (~500-1500 .md files in harmoni state).

- [ ] **Step 3: Verify**

```sql
SELECT count(*) FROM thoughts;
SELECT count(*) FROM thoughts WHERE embedding IS NOT NULL;
```

- [ ] **Step 4: Trigger embed-worker for any unembedded rows**

```bash
supabase functions invoke embed-worker
```

- [ ] **Step 5: Test semantic search**

```bash
curl -X POST "$BRAIN_MCP_URL" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{"method":"tools/call","params":{"name":"thoughts_search","arguments":{"query":"DC RFI questionnaire vendors"}}}'
# Expect relevant comms summaries / project notes
```

- [ ] **Step 6: Commit**

```bash
git add importers/dump_hs_memory.py
git commit -m "importers: hs memory dump to thoughts"
```

---

### Task 10: Phone (Claude iOS) smoke test

**Files:**
- Create: `docs/integrations/claude-ios.md`

**Steps:**

- [ ] **Step 1: Configure Claude iOS app**

In Claude iOS settings → MCP servers → add `brain` with the same URL + per-client key (`BRAIN_MOBILE_KEY`).

- [ ] **Step 2: From phone, ask a question that requires brain recall**

> "What did I capture about DC RFI vendors?"

- [ ] **Step 3: Verify the response includes relevant content from `thoughts`**

- [ ] **Step 4: Document any iOS-specific quirks in `docs/integrations/claude-ios.md`**

- [ ] **Step 5: Commit doc**

---

### Task 11: pg_dump cron + GitHub Actions ping + per-client API keys

**Files:**
- Create: `ops/pg_dump_cron.sh`
- Create: `.github/workflows/keepalive.yml`
- Create: `ops/issue_keys.sh` (one-shot to mint keys)

**Steps:**

- [ ] **Step 1: Mint per-client API keys**

For each AI client, generate a long random token, store on the Supabase side as a row in a new `api_keys` table:

```sql
create table if not exists api_keys (
  id uuid primary key default gen_random_uuid(),
  client text not null,                    -- 'claude-code', 'codex', 'gemini', 'mobile'
  key_hash text not null unique,           -- sha256 of the token
  scope text not null default 'all',
  created_at timestamptz default now(),
  last_used_at timestamptz,
  revoked_at timestamptz
);
```

```bash
# ops/issue_keys.sh — generates 4 tokens, stores hashes in api_keys, prints raw to stdout
for client in claude-code codex gemini mobile; do
  TOKEN=$(openssl rand -base64 32 | tr -d '=+/' | head -c 40)
  HASH=$(printf '%s' "$TOKEN" | sha256sum | cut -d' ' -f1)
  psql "$SUPABASE_DB_URL" -c "INSERT INTO api_keys (client, key_hash) VALUES ('$client', '$HASH');"
  echo "$client: $TOKEN"
done
```

Run once. Save raw tokens to `~/.harmoni-state/secrets/brain-keys.env` (chmod 600).

- [ ] **Step 2: Add auth check to MCP edge function**

Edit `supabase/functions/mcp/lib/auth.ts`:

```typescript
import { createClient } from "jsr:@supabase/supabase-js@2";
import { encodeHex } from "jsr:@std/encoding/hex";

export async function authenticate(req: Request): Promise<{client: string} | null> {
  const auth = req.headers.get("Authorization");
  if (!auth?.startsWith("Bearer ")) return null;
  const token = auth.slice(7);
  const hash = encodeHex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token)));
  const sb = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const { data } = await sb.from("api_keys")
    .select("client").eq("key_hash", hash).is("revoked_at", null).maybeSingle();
  if (!data) return null;
  await sb.from("api_keys").update({ last_used_at: new Date().toISOString() })
    .eq("key_hash", hash);
  return { client: data.client };
}
```

Wire into `index.ts`'s request handler — return 401 if `authenticate` returns null.

- [ ] **Step 3: Update all 4 AI client configs with new keys**

Replace the placeholder bearer tokens in Claude Code, Codex, Gemini, Mobile configs with the real tokens from `brain-keys.env`.

- [ ] **Step 4: Write pg_dump cron**

```bash
# ops/pg_dump_cron.sh
#!/bin/bash
set -euo pipefail
source ~/.harmoni-state/secrets/brain-supabase.env
DEST=~/.harmoni-state/backups/brain
mkdir -p "$DEST"
DATE=$(date +%Y-%m-%d)
pg_dump "$SUPABASE_DB_URL" | gzip > "$DEST/brain-$DATE.sql.gz"
# Keep last 30 days
find "$DEST" -name "brain-*.sql.gz" -mtime +30 -delete

# Size watch (Codex blocker mitigation)
SIZE_MB=$(du -m "$DEST/brain-$DATE.sql.gz" | cut -f1)
if [ "$SIZE_MB" -gt 250 ]; then
  echo "WARN: brain DB approaching Free-tier limit ($SIZE_MB MB compressed). Plan Neon migration."
fi
```

Add to crontab (`crontab -e`):
```
0 3 * * * /Users/manjur/Documents/000-harmoni/brain/ops/pg_dump_cron.sh >> ~/.harmoni-state/logs/brain-backup.log 2>&1
```

- [ ] **Step 5: Write GitHub Actions keepalive**

```yaml
# .github/workflows/keepalive.yml
name: brain keepalive
on:
  schedule: [{cron: '0 12 * * *'}]   # daily noon UTC
  workflow_dispatch:
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -fsS -X POST "${{ secrets.BRAIN_MCP_URL }}" \
            -H "Authorization: Bearer ${{ secrets.BRAIN_KEEPALIVE_KEY }}" \
            -H "Content-Type: application/json" \
            -d '{"method":"tools/call","params":{"name":"skills_list","arguments":{}}}' \
            > /dev/null
```

Set repo secrets in GitHub: `BRAIN_MCP_URL`, `BRAIN_KEEPALIVE_KEY` (use a 5th key minted in Step 1, scoped narrowly if `scope` enforcement added later).

- [ ] **Step 6: Verify cron runs**

Run manually:
```bash
bash ops/pg_dump_cron.sh
ls ~/.harmoni-state/backups/brain/
```

Trigger GH Actions manually via dashboard, verify success.

- [ ] **Step 7: Commit**

```bash
git add ops/pg_dump_cron.sh .github/workflows/keepalive.yml supabase/functions/mcp/lib/auth.ts
git commit -m "ops: pg_dump backup, keepalive cron, per-client API key auth"
```

---

### Week 1 checkpoint

- [ ] All 4 AI clients return `skills_list` results successfully
- [ ] `thoughts_search` returns relevant results for "DC RFI" query from phone
- [ ] `pg_dump` runs nightly and produces a non-empty file
- [ ] GitHub Actions ping job is green
- [ ] DB size < 100 MB
- [ ] All work committed and pushed to `main`

If green → proceed to Week 2. If not → fix before Week 2.

---

## Week 2: Typed issues + todos + cutover (~20h)

### Task 12: `issues` schema + supporting tables

**Files:**
- Create: `supabase/migrations/20260511000001_issues.sql`

**Steps:**

- [ ] **Step 1: Write migration**

```sql
-- supabase/migrations/20260511000001_issues.sql

create table if not exists issues (
  id uuid primary key default gen_random_uuid(),
  project_slug text not null,                       -- 'INFRA' | 'GRC' | 'DEV' | 'PERS'
  code text not null,                                -- 'INFRA-058'
  title text not null,
  body text,
  status text not null default 'backlog'
    check (status in ('backlog','in-progress','review','blocked','done')),
  priority text not null default 'medium'
    check (priority in ('low','medium','high','critical')),
  assignee text,                                     -- plain text per scope lock
  jira_key text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_slug, code)
);

create index issues_status_idx on issues(status);
create index issues_assignee_idx on issues(assignee);

create trigger issues_updated_at before update on issues
  for each row execute function set_updated_at();

create table if not exists issue_comments (
  id uuid primary key default gen_random_uuid(),
  issue_id uuid not null references issues(id) on delete cascade,
  body text not null,
  author text,
  created_at timestamptz not null default now()
);

create index issue_comments_issue_idx on issue_comments(issue_id);

create table if not exists issue_status_history (
  id uuid primary key default gen_random_uuid(),
  issue_id uuid not null references issues(id) on delete cascade,
  from_status text,
  to_status text not null,
  changed_at timestamptz not null default now(),
  changed_by text
);

create index issue_status_history_issue_idx on issue_status_history(issue_id);
```

- [ ] **Step 2: Push + verify + commit**

```bash
supabase db push
psql "$SUPABASE_DB_URL" -c "\d issues"
git add supabase/migrations/20260511000001_issues.sql
git commit -m "schema: issues, issue_comments, issue_status_history"
```

---

### Task 13: `todos` schema

**Files:**
- Create: `supabase/migrations/20260511000002_todos.sql`

```sql
-- supabase/migrations/20260511000002_todos.sql

create table if not exists todos (
  id uuid primary key default gen_random_uuid(),
  content text not null,
  status text not null default 'open'
    check (status in ('open','in-progress','waiting','done')),
  context text,                              -- free-text grouping (project/area)
  due_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index todos_status_idx on todos(status);
create index todos_context_idx on todos(context);

create trigger todos_updated_at before update on todos
  for each row execute function set_updated_at();
```

Push, verify, commit.

---

### Task 14: MCP tools for issues with optimistic concurrency

**Files:**
- Create: `supabase/functions/mcp/tools/issues.ts`
- Modify: `supabase/functions/mcp/index.ts`

**Steps:**

- [ ] **Step 1: Implement tools**

```typescript
// supabase/functions/mcp/tools/issues.ts
import { createClient } from "jsr:@supabase/supabase-js@2";
const sb = () => createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

export async function issueCreate(args: {
  project_slug: string; code: string; title: string;
  body?: string; status?: string; priority?: string; assignee?: string;
}) {
  const { data, error } = await sb().from("issues").insert(args).select().single();
  if (error) throw error;
  await sb().from("issue_status_history").insert({
    issue_id: data.id, from_status: null, to_status: data.status, changed_by: "system"
  });
  return data;
}

export async function issueUpdate(args: {
  code: string; expected_updated_at: string;     // optimistic concurrency token
  title?: string; body?: string; status?: string; priority?: string; assignee?: string;
}) {
  const { code, expected_updated_at, ...patch } = args;
  // Conditional update — only succeed if updated_at matches
  const { data: current } = await sb().from("issues").select("*").eq("code", code).single();
  if (!current) throw new Error("not_found");
  if (current.updated_at !== expected_updated_at) {
    return { error: "stale", current };
  }
  const { data, error } = await sb().from("issues")
    .update(patch).eq("id", current.id).eq("updated_at", expected_updated_at)
    .select().single();
  if (error) throw error;
  if (patch.status && patch.status !== current.status) {
    await sb().from("issue_status_history").insert({
      issue_id: current.id, from_status: current.status, to_status: patch.status, changed_by: "mcp"
    });
  }
  return data;
}

export async function issueGet(args: { code: string }) {
  const { data, error } = await sb().from("issues").select(`
    *, issue_comments (*), issue_status_history (*)
  `).eq("code", args.code).single();
  if (error) throw error;
  return data;
}

export async function issueList(args: {
  project_slug?: string; status?: string; assignee?: string; limit?: number;
}) {
  let q = sb().from("issues").select("code, title, status, priority, assignee, updated_at");
  if (args.project_slug) q = q.eq("project_slug", args.project_slug);
  if (args.status) q = q.eq("status", args.status);
  if (args.assignee) q = q.eq("assignee", args.assignee);
  q = q.order("updated_at", { ascending: false }).limit(args.limit ?? 50);
  const { data, error } = await q;
  if (error) throw error;
  return data;
}

export async function issueComment(args: { code: string; body: string; author?: string }) {
  const { data: issue } = await sb().from("issues").select("id").eq("code", args.code).single();
  if (!issue) throw new Error("not_found");
  const { data, error } = await sb().from("issue_comments")
    .insert({ issue_id: issue.id, body: args.body, author: args.author }).select().single();
  if (error) throw error;
  return data;
}

export async function issueTransition(args: {
  code: string; to_status: string; expected_updated_at: string;
}) {
  return issueUpdate({ code: args.code, status: args.to_status, expected_updated_at: args.expected_updated_at });
}
```

- [ ] **Step 2: Register in index.ts**

```typescript
import * as issues from "./tools/issues.ts";
server.tool("issue_create", { project_slug: z.string(), code: z.string(), title: z.string(),
  body: z.string().optional(), status: z.string().optional(), priority: z.string().optional(),
  assignee: z.string().optional() }, issues.issueCreate);
server.tool("issue_update", { code: z.string(), expected_updated_at: z.string(),
  title: z.string().optional(), body: z.string().optional(), status: z.string().optional(),
  priority: z.string().optional(), assignee: z.string().optional() }, issues.issueUpdate);
server.tool("issue_get", { code: z.string() }, issues.issueGet);
server.tool("issue_list", { project_slug: z.string().optional(), status: z.string().optional(),
  assignee: z.string().optional(), limit: z.number().optional() }, issues.issueList);
server.tool("issue_comment", { code: z.string(), body: z.string(),
  author: z.string().optional() }, issues.issueComment);
server.tool("issue_transition", { code: z.string(), to_status: z.string(),
  expected_updated_at: z.string() }, issues.issueTransition);
```

- [ ] **Step 3: Deploy + smoke test**

```bash
supabase functions deploy mcp
# create a test issue
curl -X POST "$BRAIN_MCP_URL" -H "Authorization: Bearer $BRAIN_CLAUDE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"method":"tools/call","params":{"name":"issue_create","arguments":{"project_slug":"DEV","code":"DEV-999","title":"smoke test"}}}'
# update it with stale updated_at — expect {"error":"stale", "current":...}
```

- [ ] **Step 4: Commit**

---

### Task 15: MCP tools for todos with optimistic concurrency

Same pattern as Task 14 — `todoCreate`, `todoUpdate` (with `expected_updated_at`), `todoList`, `todoDone`, `todoWorking`. Register, deploy, smoke test, commit.

---

### Task 16: hs migration importer — issues + todos

**Files:**
- Create: `importers/import_hs_issues.py`
- Create: `importers/import_hs_todos.py`

**Steps:**

- [ ] **Step 1: Issues importer**

```python
# importers/import_hs_issues.py
"""
Read all hs issues from BOTH SQLite (~/.harmoni-state/db.nosync/state.db) and
markdown files (~/.harmoni-state/projects/*/issues/*.md) — union by code.
Push to brain via issue_create. Preserve created_at via raw INSERT (post-create UPDATE).
"""
import os, sqlite3, re, json
from pathlib import Path
from datetime import datetime
import requests

MCP_URL = os.environ["BRAIN_MCP_URL"]
KEY = os.environ["BRAIN_IMPORT_KEY"]

DB = Path.home() / ".harmoni-state" / "db.nosync" / "state.db"

def load_sqlite_issues():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM issues").fetchall()
    return {r["code"]: dict(r) for r in rows}

def load_markdown_issues():
    out = {}
    base = Path.home() / ".harmoni-state" / "projects"
    for issue_md in base.rglob("issues/*.md"):
        if "memory" in issue_md.parts: continue
        text = issue_md.read_text()
        fm = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not fm: continue
        front = {}
        for line in fm.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1); front[k.strip()] = v.strip().strip("'").strip('"')
        code = front.get("id") or issue_md.stem
        out[code] = {
            "code": code, "title": front.get("title", code),
            "status": front.get("status", "backlog"),
            "priority": front.get("priority", "medium"),
            "assignee": front.get("assignee"),
            "body": fm.group(2).strip(),
            "project_slug": code.split("-")[0],
            "created_at": front.get("created"),
        }
    return out

def mcp(tool, args):
    r = requests.post(MCP_URL,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type":"application/json"},
        json={"method":"tools/call","params":{"name":tool,"arguments":args}})
    r.raise_for_status()
    return r.json().get("result")

def main():
    sql_issues = load_sqlite_issues()
    md_issues = load_markdown_issues()
    all_codes = sorted(set(sql_issues) | set(md_issues))
    print(f"sqlite: {len(sql_issues)}, markdown: {len(md_issues)}, union: {len(all_codes)}")
    for code in all_codes:
        merged = {**sql_issues.get(code, {}), **md_issues.get(code, {})}
        merged["code"] = code
        merged["project_slug"] = code.split("-")[0]
        # whitelist fields to issue_create
        payload = {k: merged[k] for k in
                   ("project_slug","code","title","body","status","priority","assignee")
                   if k in merged and merged[k] is not None}
        print(f"importing {code}...")
        try:
            mcp("issue_create", payload)
        except Exception as e:
            print(f"  skip {code}: {e}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Todos importer (similar shape, simpler — read from SQLite todos table or GLOBAL_TODO.md)**

- [ ] **Step 3: Run dry-run, then real**

```bash
source ~/.harmoni-state/secrets/brain-keys.env  # exposes BRAIN_IMPORT_KEY
python importers/import_hs_issues.py
python importers/import_hs_todos.py
```

- [ ] **Step 4: Verification pass — diff cloud vs source**

```bash
# Counts
psql "$SUPABASE_DB_URL" -c "SELECT project_slug, count(*) FROM issues GROUP BY project_slug ORDER BY 1;"
sqlite3 ~/.harmoni-state/db.nosync/state.db "SELECT substr(code,1,instr(code,'-')-1) AS p, count(*) FROM issues GROUP BY 1;"
# Counts must match (or document deltas).
```

```bash
# Spot-check 5 issues end-to-end (title, status, body length parity)
for code in INFRA-058 GRC-15 INFRA-076 GRC-83 INFRA-069; do
  echo "=== $code ==="
  curl -s -X POST "$BRAIN_MCP_URL" -H "Authorization: Bearer $BRAIN_IMPORT_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"method\":\"tools/call\",\"params\":{\"name\":\"issue_get\",\"arguments\":{\"code\":\"$code\"}}}" \
    | jq '.result | {code, title, status, body_len: (.body|length)}'
done
```

- [ ] **Step 5: Commit**

---

### Task 17: `/issue` slash command shim → MCP

**Files:**
- Create: `~/.claude/commands/issue.sh` (or wherever harmoni stores slash command shims)
- Modify: `~/.claude/CLAUDE.md` (commands table)

**Steps:**

- [ ] **Step 1: Write shim**

```bash
#!/bin/bash
# /issue command — replaces hs issue routing
set -euo pipefail
source ~/.harmoni-state/secrets/brain-keys.env
SUB=$1; shift
case "$SUB" in
  list)
    curl -s -X POST "$BRAIN_MCP_URL" -H "Authorization: Bearer $BRAIN_CLAUDE_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"method\":\"tools/call\",\"params\":{\"name\":\"issue_list\",\"arguments\":{$*}}}" | jq
    ;;
  show)
    CODE=$1
    curl -s -X POST "$BRAIN_MCP_URL" -H "Authorization: Bearer $BRAIN_CLAUDE_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"method\":\"tools/call\",\"params\":{\"name\":\"issue_get\",\"arguments\":{\"code\":\"$CODE\"}}}" | jq
    ;;
  create|update|comment|transition)
    # ... CLI args parsed and passed through
    ;;
esac
```

- [ ] **Step 2: Test parity with hs**

```bash
hs issue list --status in-progress > /tmp/hs.txt
brain-shim issue list status=in-progress > /tmp/brain.txt
diff <(sort /tmp/hs.txt) <(sort /tmp/brain.txt)
```

Should match (modulo formatting).

- [ ] **Step 3: Update CLAUDE.md commands table**

Replace `hs issue` references with `brain` MCP tool calls.

- [ ] **Step 4: Commit**

---

### Task 18: `/todo` slash command shim

Same pattern as Task 17.

---

### Task 19: Phone (Claude iOS) issue/todo CRUD test

**Steps:**

- [ ] **Step 1: From phone, create a todo via Claude iOS**

> "Add a todo: 'review brain v1 metrics next week'"

Verify Claude calls `todo_create` and returns confirmation.

- [ ] **Step 2: List todos, verify it appears**

- [ ] **Step 3: Update an issue from phone**

> "Move issue DEV-999 to status done"

Verify `issue_transition` runs successfully (with optimistic concurrency token fetched from prior `issue_get`).

- [ ] **Step 4: Document any iOS quirks**

---

### Task 20: Integrity verification + cutover

**Files:**
- Create: `ops/cutover_checklist.md`

**Steps:**

- [ ] **Step 1: Run final integrity verification**

```bash
# Count parity
echo "Issues: $(psql "$SUPABASE_DB_URL" -t -c 'select count(*) from issues') vs $(sqlite3 ~/.harmoni-state/db.nosync/state.db 'select count(*) from issues')"
echo "Todos: $(psql "$SUPABASE_DB_URL" -t -c 'select count(*) from todos') vs $(sqlite3 ~/.harmoni-state/db.nosync/state.db 'select count(*) from todos')"
echo "Status history: $(psql "$SUPABASE_DB_URL" -t -c 'select count(*) from issue_status_history')"
echo "Skills: $(psql "$SUPABASE_DB_URL" -t -c 'select count(*) from skills')"
echo "Thoughts: $(psql "$SUPABASE_DB_URL" -t -c 'select count(*) from thoughts')"
```

Document parity (or document accepted delta with reason).

- [ ] **Step 2: Cutover decision per Open Question #3**

If hard cutover:
- Disable hs's `issue` + `todo` commands (rename binary or guard with env check).
- Document rollback path: re-enable hs binary, brain reads stay live, no data loss.

If parallel-run:
- Both `hs` and `brain` shim respond. hs writes locally; cron syncs hs→cloud nightly. AI tools write to cloud only. Plan a hard cutover at end of week 3.

- [ ] **Step 3: Update MEMORY.md to reflect new state**

Add: `[brain v1 shipped 2026-05-XX](project_brain_v1_shipped.md) — issues+todos canonical in cloud, hs read-only mirror.`

- [ ] **Step 4: Document hs Python's reduced scope**

`hs` (Python) keeps owning comms, sessions, output, hooks. Issues + todos move to brain cloud. No archival — hs Python stays as the local CLI indefinitely.

- [ ] **Step 5: Final commit**

```bash
git add ops/cutover_checklist.md
git commit -m "ops: brain v1 cutover checklist and verification"
git tag v1.0.0
git push origin main --tags
```

---

### Week 2 checkpoint

- [ ] All 114 hs issues and todos round-tripped to cloud, parity verified
- [ ] `/issue` and `/todo` slash commands route to MCP
- [ ] Phone can read + write issues/todos
- [ ] Optimistic concurrency rejects stale writes (verified by manual test)
- [ ] Backups running, keepalive ping green, DB size < 200 MB
- [ ] hs Python's `/issue` and `/todo` paths demoted to read-only (slash commands route to MCP)

---

## Risk register & mitigations

| Risk | Mitigation |
|---|---|
| MCP edge function cold start (1-3s) becomes annoying | Acceptable for v1; if blocks daily flow, replatform to Fly.io (~1 day work) |
| Importer drops data on a malformed markdown | Verification pass in Task 16 + 20 catches before cutover; re-run idempotently |
| API key leak via accidental commit | Secrets only in `~/.harmoni-state/secrets/`, never in repo; revoke + rotate via `api_keys.revoked_at` |
| Supabase free DB hits 250 MB ahead of schedule | `pg_dump` cron warns; switch to Neon (1 day) |
| OpenRouter outage breaks new captures | Retry queue in importers; user-facing brain captures degrade gracefully (insert without embedding, embed-worker retries) |
| hs and cloud diverge during parallel-run | If chosen: nightly hs→cloud sync; cloud is ground truth for assignee/status from any AI write |

---

## Done definition

- [ ] All Week 1 + Week 2 checkpoints passed
- [ ] `git tag v1.0.0` pushed
- [ ] User can: ask phone for issue status; AI tool creates an issue from desktop, phone sees it within seconds; skill loaded by Claude Code via MCP
- [ ] `pg_dump` backup runs nightly
- [ ] hs Python's issues/todos slash commands route to MCP (everything else in hs Python continues working unchanged)
- [ ] Plan moved to `~/.harmoni-state/memory/sessions/2026-05-XX-brain-v1-shipped.md` after final commit
