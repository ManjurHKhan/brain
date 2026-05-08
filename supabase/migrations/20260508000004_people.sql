-- people — unified personality matrix + contact directory.
-- Replaces hs comms/contacts and consolidates two markdown sources:
--   memory/people/*.md  (18 rich personas: David/Brian/Tanner/etc.)
--   comms/<name>.md     (105 vendor/contact rollup files)
-- Persona-refresh skill rewrites to write through `person_upsert` MCP tool.

create table if not exists people (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  kind text not null default 'external'
    check (kind in ('internal','external','vendor','advisor','customer','partner','other')),
  role text,
  company text,
  email text,
  phone text,
  contact_handles jsonb not null default '{}'::jsonb,
  relationship_summary text,
  background text,
  how_they_think text,
  how_to_work_with text,
  communication_style text,
  core_values text,
  key_insights text,
  regular_syncs text,
  last_contact_at timestamptz,
  tags text[] not null default '{}',
  body text,
  body_embedding vector(1536),
  embedding_model text,
  embedding_provider text,
  embedding_version int default 1,
  source_file text,
  content_fingerprint text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists people_fingerprint_idx
  on people (content_fingerprint) where content_fingerprint is not null;
create index if not exists people_kind_idx on people(kind);
create index if not exists people_company_idx on people(company) where company is not null;
create index if not exists people_tags_gin on people using gin(tags);
create index if not exists people_last_contact_idx on people(last_contact_at desc) where last_contact_at is not null;

create trigger people_updated_at before update on people
  for each row execute function update_updated_at();

-- Optional polymorphic linkage from comms.contacts text[] entries to people.slug
-- (no FK; resolved at query time via slug match).

notify pgrst, 'reload schema';
