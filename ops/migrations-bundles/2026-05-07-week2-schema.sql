-- ============================================================================
-- brain — Week 2 schema bundle (issues, todos, comms, sessions, outputs, files)
-- Paste into Supabase dashboard → SQL editor → Run.
-- Migrations 20260507000001 through 20260507000006.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- supabase/migrations/20260507000001_issues.sql
-- ----------------------------------------------------------------------------

-- issues + issue_comments + issue_status_history
-- Cloud canonical for what hs Python's `issues` table held, plus FedRAMP control codes,
-- tags, parent linkage, and embedding for semantic search.

create table if not exists issues (
  id uuid primary key default gen_random_uuid(),
  project_slug text not null,
  code text not null,
  title text not null,
  body text,
  type text not null default 'task'
    check (type in ('task','bug','epic','spike','research','chore')),
  status text not null default 'backlog'
    check (status in ('backlog','in-progress','review','blocked','done','wontfix')),
  priority text not null default 'medium'
    check (priority in ('low','medium','high','critical')),
  assignee text,
  effort_hours numeric,
  due_date date,
  parent_code text,
  controls text[] not null default '{}',
  tags text[] not null default '{}',
  jira_key text,
  body_embedding vector(1536),
  embedding_model text,
  embedding_provider text,
  embedding_version int default 1,
  metadata jsonb not null default '{}'::jsonb,
  source_file text,
  content_fingerprint text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_slug, code)
);

create unique index if not exists issues_fingerprint_idx
  on issues (content_fingerprint) where content_fingerprint is not null;
create index if not exists issues_status_idx on issues(status);
create index if not exists issues_assignee_idx on issues(assignee) where assignee is not null;
create index if not exists issues_project_idx on issues(project_slug);
create index if not exists issues_jira_idx on issues(jira_key) where jira_key is not null;
create index if not exists issues_tags_gin on issues using gin(tags);
create index if not exists issues_controls_gin on issues using gin(controls);
create index if not exists issues_embedding_hnsw
  on issues using hnsw (body_embedding vector_cosine_ops);

create trigger issues_updated_at before update on issues
  for each row execute function update_updated_at();

create table if not exists issue_comments (
  id uuid primary key default gen_random_uuid(),
  issue_id uuid not null references issues(id) on delete cascade,
  body text not null,
  author text,
  created_at timestamptz not null default now()
);
create index if not exists issue_comments_issue_idx on issue_comments(issue_id);
create index if not exists issue_comments_created_idx on issue_comments(created_at desc);

create table if not exists issue_status_history (
  id uuid primary key default gen_random_uuid(),
  issue_id uuid not null references issues(id) on delete cascade,
  from_status text,
  to_status text not null,
  changed_by text,
  changed_at timestamptz not null default now()
);
create index if not exists issue_status_history_issue_idx
  on issue_status_history(issue_id);

notify pgrst, 'reload schema';

-- ----------------------------------------------------------------------------
-- supabase/migrations/20260507000002_todos.sql
-- ----------------------------------------------------------------------------

-- todos — replaces hs Python's todos. Cross-LLM handoff via `assignee`.
-- `comm_id` is forward-declared here; FK to comms(id) added in 20260507000003_comms.sql.

create table if not exists todos (
  id uuid primary key default gen_random_uuid(),
  content text not null,
  status text not null default 'open'
    check (status in ('open','in-progress','waiting','done','cancelled')),
  assignee text,
  created_by text,
  priority text not null default 'medium'
    check (priority in ('low','medium','high','critical')),
  context text,
  tags text[] not null default '{}',
  due_at timestamptz,
  completed_at timestamptz,
  comm_id uuid,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists todos_status_idx on todos(status);
create index if not exists todos_assignee_idx on todos(assignee) where assignee is not null;
create index if not exists todos_context_idx on todos(context) where context is not null;
create index if not exists todos_due_idx on todos(due_at) where due_at is not null;
create index if not exists todos_comm_idx on todos(comm_id) where comm_id is not null;
create index if not exists todos_tags_gin on todos using gin(tags);

create trigger todos_updated_at before update on todos
  for each row execute function update_updated_at();

notify pgrst, 'reload schema';

-- ----------------------------------------------------------------------------
-- supabase/migrations/20260507000003_comms.sql
-- ----------------------------------------------------------------------------

-- comms — replaces hs Python's communications + comm_contacts. Contacts denormalized
-- as text[] (identity resolution deferred). Action items moved to todos via comm_id FK.

create table if not exists comms (
  id uuid primary key default gen_random_uuid(),
  occurred_at timestamptz not null,
  comm_type text not null
    check (comm_type in ('call','email','slack','meeting','dm','text','other')),
  platform text,
  duration_min int,
  summary text not null,
  body text,
  key_decisions text,
  technical_insights text,
  contacts text[] not null default '{}',
  body_embedding vector(1536),
  embedding_model text,
  embedding_provider text,
  embedding_version int default 1,
  metadata jsonb not null default '{}'::jsonb,
  source_file text,
  content_fingerprint text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists comms_fingerprint_idx
  on comms (content_fingerprint) where content_fingerprint is not null;
create index if not exists comms_occurred_idx on comms(occurred_at desc);
create index if not exists comms_type_idx on comms(comm_type);
create index if not exists comms_contacts_gin on comms using gin(contacts);
create index if not exists comms_embedding_hnsw
  on comms using hnsw (body_embedding vector_cosine_ops);

create trigger comms_updated_at before update on comms
  for each row execute function update_updated_at();

-- Wire up the forward-declared FK from todos.comm_id
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'todos_comm_id_fk'
  ) then
    alter table todos
      add constraint todos_comm_id_fk
      foreign key (comm_id) references comms(id) on delete set null;
  end if;
end $$;

notify pgrst, 'reload schema';

-- ----------------------------------------------------------------------------
-- supabase/migrations/20260507000004_sessions.sql
-- ----------------------------------------------------------------------------

-- sessions — replaces hs Python's session_registry + memory/sessions/*.md markdown.
-- Structured slices (accomplishments/decisions/open_items/next_steps) split from body
-- so queries like "all decisions across last 10 sessions" don't need text scraping.

create table if not exists sessions (
  id uuid primary key default gen_random_uuid(),
  session_id text unique,
  title text,
  project text,
  status text not null default 'active'
    check (status in ('active','ended','archived')),
  started_at timestamptz not null,
  ended_at timestamptz,
  claude_uuid text,
  body text,
  accomplishments text[] not null default '{}',
  decisions text[] not null default '{}',
  open_items text[] not null default '{}',
  next_steps text[] not null default '{}',
  files_modified text[] not null default '{}',
  body_embedding vector(1536),
  embedding_model text,
  embedding_provider text,
  embedding_version int default 1,
  metadata jsonb not null default '{}'::jsonb,
  source_file text,
  content_fingerprint text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists sessions_fingerprint_idx
  on sessions (content_fingerprint) where content_fingerprint is not null;
create index if not exists sessions_started_idx on sessions(started_at desc);
create index if not exists sessions_project_idx on sessions(project) where project is not null;
create index if not exists sessions_status_idx on sessions(status);
create index if not exists sessions_session_id_idx
  on sessions(session_id) where session_id is not null;
create index if not exists sessions_embedding_hnsw
  on sessions using hnsw (body_embedding vector_cosine_ops);

create trigger sessions_updated_at before update on sessions
  for each row execute function update_updated_at();

notify pgrst, 'reload schema';

-- ----------------------------------------------------------------------------
-- supabase/migrations/20260507000005_outputs.sql
-- ----------------------------------------------------------------------------

-- outputs — typed index on top of deliverables. Body of markdown deliverables lives
-- in `thoughts` for semantic search; this table is the lookup ("runbooks for INFRA",
-- "outputs delivered to Tanner"). `file_id` is forward-declared; FK added in files migration.

create table if not exists outputs (
  id uuid primary key default gen_random_uuid(),
  filename text not null,
  title text,
  kind text
    check (kind in ('runbook','report','rfi','rfp','plan','memo','analysis','quote','other')),
  project text,
  related_issue_code text,
  recipient text,
  description text not null,
  source_file text,
  file_id uuid,
  registered_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists outputs_project_idx on outputs(project) where project is not null;
create index if not exists outputs_kind_idx on outputs(kind) where kind is not null;
create index if not exists outputs_issue_idx
  on outputs(related_issue_code) where related_issue_code is not null;
create index if not exists outputs_registered_idx on outputs(registered_at desc);
create index if not exists outputs_recipient_idx
  on outputs(recipient) where recipient is not null;

create trigger outputs_updated_at before update on outputs
  for each row execute function update_updated_at();

notify pgrst, 'reload schema';

-- ----------------------------------------------------------------------------
-- supabase/migrations/20260507000006_files.sql
-- ----------------------------------------------------------------------------

-- files — Cloudflare R2-backed binary storage. Brain holds metadata only; bytes
-- never traverse the edge function (client uses presigned PUT URLs).
-- See decision_brain_files_cloudflare_r2_2026_05_06.md.
-- Polymorphic relation: (related_entity_type, related_entity_id) instead of cross-table FKs.

create table if not exists files (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  kind text
    check (kind in ('docx','xlsx','pptx','pdf','png','jpg','jpeg','gif','svg',
                    'mp4','mov','mp3','wav','zip','tar','txt','md','csv','json','other')),
  content_type text,
  size bigint,
  sha256 text,
  r2_key text not null,
  r2_bucket text not null default 'brain-files',
  status text not null default 'pending'
    check (status in ('pending','ready','failed','deleted')),
  related_entity_type text
    check (related_entity_type in ('issue','comm','output','session','todo','thought','skill')),
  related_entity_id uuid,
  uploaded_by text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists files_status_idx on files(status);
create index if not exists files_related_idx
  on files(related_entity_type, related_entity_id)
  where related_entity_type is not null;
create index if not exists files_kind_idx on files(kind) where kind is not null;
create index if not exists files_sha256_idx on files(sha256) where sha256 is not null;

create trigger files_updated_at before update on files
  for each row execute function update_updated_at();

-- Wire up the forward-declared FK from outputs.file_id
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'outputs_file_id_fk'
  ) then
    alter table outputs
      add constraint outputs_file_id_fk
      foreign key (file_id) references files(id) on delete set null;
  end if;
end $$;

notify pgrst, 'reload schema';
