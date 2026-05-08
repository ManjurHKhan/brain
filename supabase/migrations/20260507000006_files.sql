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
