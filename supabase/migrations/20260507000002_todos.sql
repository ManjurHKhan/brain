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
