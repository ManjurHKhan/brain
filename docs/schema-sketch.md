---
type: design-doc
tags: [brain, schema, week-2]
created: 2026-05-07
---

# Schema sketch — issues, todos, comms, sessions, outputs, files

> Locked-in design for Week 2 entities. Drives migrations 20260507000001+.

## Goals

1. Replace **all** hs Python data ownership (issues + todos + comms + sessions + outputs) with cloud canonical, plus add files.
2. Device-agnostic: every entity reachable from desktop / phone / work laptop via MCP.
3. Reuse existing patterns from `thoughts` + `skills`: UUID PKs, content_fingerprint dedup, embedding columns, `update_updated_at()` trigger, `metadata jsonb` escape hatch.
4. Loose coupling between entities: cross-table references via string match (issue codes, session ids) or polymorphic `(entity_type, entity_id)`. No hard FKs across entity tables — keeps importers flexible and avoids cascading deletes during migration churn.

## Cross-cutting decisions

| Concern | Decision |
|---|---|
| Primary keys | `uuid` with `gen_random_uuid()` everywhere |
| Updated-at | `update_updated_at()` trigger (defined in `20260505000001_thoughts.sql`) |
| Dedup | `content_fingerprint text unique` on tables that import markdown |
| Audit trail | `source_file text` on every imported entity |
| Forward compat | `metadata jsonb default '{}'::jsonb` on every table |
| Embeddings | `body_embedding vector(1536)` + `embedding_model/provider/version` on entities with rich text bodies |
| Cross-entity refs | Issue codes (`'INFRA-058'`) and session ids (`'c-20260506-...'`) are text. No FKs. |
| File attachment | Polymorphic: `files.related_entity_type` + `files.related_entity_id` |
| Optimistic concurrency | `expected_updated_at` token on every mutating MCP tool |
| `created_by` | Stamped from auth context (which API key called the tool), not user-supplied |

## Entity-by-entity

### `issues` (+ `issue_comments` + `issue_status_history`)

Builds on Task 12 plan with extras for hs parity.

```sql
create table issues (
  id uuid primary key default gen_random_uuid(),
  project_slug text not null,                    -- 'INFRA' | 'GRC' | 'DEV' | 'PERS' | 'MANJUR'
  code text not null,                            -- 'INFRA-058' (full code, project_slug-prefixed)
  title text not null,
  body text,
  type text not null default 'task'              -- 'task' | 'bug' | 'epic' | 'spike' | 'research'
    check (type in ('task','bug','epic','spike','research','chore')),
  status text not null default 'backlog'
    check (status in ('backlog','in-progress','review','blocked','done','wontfix')),
  priority text not null default 'medium'
    check (priority in ('low','medium','high','critical')),
  assignee text,                                 -- free text, conventionally 'manjur'/'claude-code'/...
  effort_hours numeric,                          -- nullable; from hs
  due_date date,
  parent_code text,                              -- self-ref by code, no FK
  controls text[] default '{}',                  -- FedRAMP control codes ('SC-13', 'AC-2'…)
  tags text[] default '{}',
  jira_key text,
  body_embedding vector(1536),
  embedding_model text, embedding_provider text, embedding_version int default 1,
  metadata jsonb default '{}',
  source_file text,
  content_fingerprint text,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (project_slug, code)
);
create unique index issues_fingerprint_idx
  on issues (content_fingerprint) where content_fingerprint is not null;
create index issues_status_idx on issues(status);
create index issues_assignee_idx on issues(assignee);
create index issues_project_idx on issues(project_slug);
create index issues_tags_gin on issues using gin(tags);
create index issues_controls_gin on issues using gin(controls);
create index issues_embedding_hnsw on issues using hnsw(body_embedding vector_cosine_ops);
```

`issue_comments`: `id, issue_id, body, author, created_at` (Task 12 spec, unchanged).

`issue_status_history`: `id, issue_id, from_status, to_status, changed_by, changed_at` (unchanged).

### `todos`

```sql
create table todos (
  id uuid primary key default gen_random_uuid(),
  content text not null,
  status text not null default 'open'
    check (status in ('open','in-progress','waiting','done','cancelled')),
  assignee text,                -- 'manjur'|'claude-code'|'codex'|'gemini'|'mobile'|NULL
  created_by text,              -- stamped from auth context
  priority text not null default 'medium'
    check (priority in ('low','medium','high','critical')),
  context text,                 -- free-text grouping (project/area/section)
  tags text[] default '{}',
  due_at timestamptz,
  completed_at timestamptz,
  -- Forward ref to comms (action items from a comm). FK added in comms migration.
  comm_id uuid,
  metadata jsonb default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index todos_status_idx on todos(status);
create index todos_assignee_idx on todos(assignee) where assignee is not null;
create index todos_context_idx on todos(context);
create index todos_due_idx on todos(due_at) where due_at is not null;
create index todos_comm_idx on todos(comm_id) where comm_id is not null;
create index todos_tags_gin on todos using gin(tags);
```

### `comms`

Denormalized contacts (`text[]`) instead of join table — identity resolution deferred per scope locks. GIN index lets queries like `where 'David' = any(contacts)` stay fast.

```sql
create table comms (
  id uuid primary key default gen_random_uuid(),
  occurred_at timestamptz not null,
  comm_type text not null
    check (comm_type in ('call','email','slack','meeting','dm','text','other')),
  platform text,                -- 'slack' | 'gmail' | 'imessage' | 'phone' | 'teams' | …
  duration_min int,
  summary text not null,
  body text,                    -- full content (transcript or message)
  key_decisions text,
  technical_insights text,
  contacts text[] default '{}', -- denormalized people list
  body_embedding vector(1536),
  embedding_model text, embedding_provider text, embedding_version int default 1,
  metadata jsonb default '{}',
  source_file text,
  content_fingerprint text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create unique index comms_fingerprint_idx
  on comms (content_fingerprint) where content_fingerprint is not null;
create index comms_occurred_idx on comms(occurred_at desc);
create index comms_type_idx on comms(comm_type);
create index comms_contacts_gin on comms using gin(contacts);
create index comms_embedding_hnsw on comms using hnsw(body_embedding vector_cosine_ops);

-- Wire up todos.comm_id now that comms exists
alter table todos
  add constraint todos_comm_id_fk foreign key (comm_id) references comms(id) on delete set null;
```

### `sessions`

```sql
create table sessions (
  id uuid primary key default gen_random_uuid(),
  session_id text unique,            -- wrapper id 'c-YYYYMMDD-HHMMSS-XX'
  title text,
  project text,                      -- 'GRC'|'INFRA'|'DEV'|'personal'|NULL
  status text not null default 'active'
    check (status in ('active','ended','archived')),
  started_at timestamptz not null,
  ended_at timestamptz,
  claude_uuid text,
  body text,                         -- markdown session log
  -- Structured slices for queries
  accomplishments text[] default '{}',
  decisions text[] default '{}',
  open_items text[] default '{}',
  next_steps text[] default '{}',
  files_modified text[] default '{}',
  body_embedding vector(1536),
  embedding_model text, embedding_provider text, embedding_version int default 1,
  metadata jsonb default '{}',
  source_file text,
  content_fingerprint text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create unique index sessions_fingerprint_idx
  on sessions (content_fingerprint) where content_fingerprint is not null;
create index sessions_started_idx on sessions(started_at desc);
create index sessions_project_idx on sessions(project);
create index sessions_status_idx on sessions(status);
create index sessions_embedding_hnsw on sessions using hnsw(body_embedding vector_cosine_ops);
```

### `outputs`

Typed index on top of deliverables. Body of a markdown deliverable goes to `thoughts` for semantic search; `outputs` is the typed lookup ("show me runbooks for INFRA" / "outputs delivered to Tanner").

```sql
create table outputs (
  id uuid primary key default gen_random_uuid(),
  filename text not null,
  title text,
  kind text                          -- 'runbook' | 'report' | 'rfi' | 'plan' | 'memo' | 'analysis' | 'other'
    check (kind in ('runbook','report','rfi','rfp','plan','memo','analysis','quote','other')),
  project text,
  related_issue_code text,           -- 'INFRA-058' (string match, no FK)
  recipient text,
  description text not null,
  source_file text,                  -- local FS path (transitional during migration)
  file_id uuid,                      -- FK to files.id, added in files migration
  registered_at timestamptz default now(),
  metadata jsonb default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index outputs_project_idx on outputs(project);
create index outputs_kind_idx on outputs(kind);
create index outputs_issue_idx on outputs(related_issue_code);
create index outputs_registered_idx on outputs(registered_at desc);
create index outputs_recipient_idx on outputs(recipient);
```

### `files`

R2-backed binary storage. Bytes never traverse the edge function — clients get a presigned PUT URL, upload direct to R2, then call `file_finalize` to flip `status`. See `decision_brain_files_cloudflare_r2_2026_05_06.md` memory.

```sql
create table files (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  kind text,                         -- 'docx'|'xlsx'|'pdf'|'png'|'jpg'|'gif'|'mp4'|'zip'|'txt'|'other'
  content_type text,                 -- MIME
  size bigint,
  sha256 text,
  r2_key text not null,              -- bucket path, e.g. 'issues/<uuid>/<filename>'
  r2_bucket text not null default 'brain-files',
  status text not null default 'pending'
    check (status in ('pending','ready','failed','deleted')),
  related_entity_type text,          -- 'issue'|'comm'|'output'|'session'|'todo'|NULL
  related_entity_id uuid,
  uploaded_by text,                  -- auth context
  metadata jsonb default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index files_status_idx on files(status);
create index files_related_idx on files(related_entity_type, related_entity_id);
create index files_kind_idx on files(kind);
create index files_sha256_idx on files(sha256) where sha256 is not null;

-- Wire up outputs.file_id now that files exists
alter table outputs
  add constraint outputs_file_id_fk foreign key (file_id) references files(id) on delete set null;
```

## Migration order

| # | File | Notes |
|---|---|---|
| 1 | `20260507000001_issues.sql` | issues + issue_comments + issue_status_history |
| 2 | `20260507000002_todos.sql` | todos with `comm_id uuid` (no FK yet) |
| 3 | `20260507000003_comms.sql` | comms + ALTER todos add FK |
| 4 | `20260507000004_sessions.sql` | sessions |
| 5 | `20260507000005_outputs.sql` | outputs with `file_id uuid` (no FK yet) |
| 6 | `20260507000006_files.sql` | files + ALTER outputs add FK |

Each file ends with `notify pgrst, 'reload schema';` so PostgREST picks up the new tables without a restart.

## Open questions deferred

- Per-tenant RLS — single-user system, skip for v1.
- `embedding` for `outputs` body — defer; `outputs` is an index, body lives in `thoughts`.
- `comments` on todos — todos use `metadata.notes` for v1; add `todo_comments` only if usage demands.
- Soft-delete vs hard-delete — hard-delete for v1 across the board; if regret hits, add `deleted_at` later.

## What this unlocks

- **Tasks 14+15** (issue/todo MCP tools with optimistic concurrency)
- **Comms/sessions/outputs MCP tools** (CRUD + semantic search variants)
- **Files MCP tools** (`file_upload_url` / `file_finalize` / `file_download_url` / `file_list` / `file_delete`)
- **Importers** (`import_hs_issues.py`, `import_hs_todos.py`, `import_hs_comms.py`, `import_hs_sessions.py`, `import_hs_outputs.py`)
- **Slash command shims** routing to MCP for `/issue`, `/todo`, `/log`, `/log-call`, `/log-email`, `/output`, `/remember session`
