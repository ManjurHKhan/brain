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
