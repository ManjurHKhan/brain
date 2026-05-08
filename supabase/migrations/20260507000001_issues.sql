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
