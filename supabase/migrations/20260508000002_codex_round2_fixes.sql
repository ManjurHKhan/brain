-- Codex round-2 review (2026-05-08): fix the unique-source_file bug for todos.
-- A single markdown file (e.g. GLOBAL_TODO.md) holds many todos; uniqueness must
-- include line/locator info, not source_file alone.
--
-- See ~/.harmoni-state/output/2026-05-08-codex-review-brain-week2-round2-RESPONSE.md.

-- Drop the broken unique index.
drop index if exists todos_source_file_idx;

-- Add a per-line locator. Importers populate this; manual creates leave it null.
alter table todos add column if not exists source_line int;

-- Non-unique composite index for fast lookup. Idempotency relies on
-- content_fingerprint (already unique) plus the per-importer convention of
-- delete-from-source_file then re-insert.
create index if not exists todos_source_file_line_idx
  on todos (source_file, source_line)
  where source_file is not null;

notify pgrst, 'reload schema';
