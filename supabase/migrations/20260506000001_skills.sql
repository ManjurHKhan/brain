-- skills + skill_variants — vendor-neutral skill registry
-- Generic body in `skills`, optional per-vendor overrides in `skill_variants`.
-- Resolution: skill_get(slug, vendor) returns variant if present, else generic.

create table if not exists skills (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  description text not null,
  generic_body text not null,
  tags text[] not null default '{}',
  embedding vector(1536),
  embedding_model text,
  embedding_provider text,
  embedding_version int default 1,
  version int not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists skills_embedding_hnsw
  on skills using hnsw (embedding vector_cosine_ops);
create index if not exists skills_tags_gin
  on skills using gin (tags);

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

-- Reuse existing update_updated_at() trigger function (defined in 20260505000001_thoughts.sql)
create trigger skills_updated_at before update on skills
  for each row execute function update_updated_at();

notify pgrst, 'reload schema';
