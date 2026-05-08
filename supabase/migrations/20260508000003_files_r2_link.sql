-- Wire up R2 file ↔ thought (RAG companion) and update bucket default.
-- See decision_brain_files_cloudflare_r2_2026_05_06.md and the 2026-05-08
-- evening setup of the harmoni-brain R2 bucket.

-- 1) Bucket default — track the actual bucket name.
alter table files alter column r2_bucket set default 'harmoni-brain';

-- 2) Companion thought_id (RAG: text extracted into thoughts; bytes in R2).
alter table files add column if not exists thought_id uuid;
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'files_thought_id_fk'
  ) then
    alter table files
      add constraint files_thought_id_fk
      foreign key (thought_id) references thoughts(id) on delete set null;
  end if;
end $$;
create index if not exists files_thought_idx
  on files(thought_id) where thought_id is not null;

-- 3) Partial unique on r2_key so the importer can upsert by path on re-run.
create unique index if not exists files_r2_key_idx
  on files(r2_key) where r2_key is not null;

notify pgrst, 'reload schema';
