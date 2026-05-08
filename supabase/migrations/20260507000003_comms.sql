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
