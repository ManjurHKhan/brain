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
