-- Codex review (2026-05-07) P1 + P2 follow-ups.
-- See ~/.harmoni-state/output/2026-05-07-codex-review-brain-week2-RESPONSE.md.

-- ============================================================================
-- P1.1: issue identity. Make `code` globally unique with a project-prefix check
-- so all read tools can safely look up by `code` alone.
-- ============================================================================

alter table issues drop constraint if exists issues_project_slug_code_key;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'issues_code_key' and conrelid = 'issues'::regclass
  ) then
    alter table issues add constraint issues_code_key unique (code);
  end if;
end $$;

alter table issues drop constraint if exists issues_code_project_prefix_chk;
alter table issues add constraint issues_code_project_prefix_chk
  check (code like project_slug || '-%');

-- ============================================================================
-- P1.2: importer idempotency for todos + outputs.
-- Add source_file + content_fingerprint with partial unique indexes so re-running
-- importers updates the matching row instead of duplicating.
-- ============================================================================

alter table todos add column if not exists source_file text;
alter table todos add column if not exists content_fingerprint text;
create unique index if not exists todos_fingerprint_idx
  on todos (content_fingerprint) where content_fingerprint is not null;
create unique index if not exists todos_source_file_idx
  on todos (source_file) where source_file is not null;

alter table outputs add column if not exists content_fingerprint text;
create unique index if not exists outputs_source_file_idx
  on outputs (source_file) where source_file is not null;
create unique index if not exists outputs_fingerprint_idx
  on outputs (content_fingerprint) where content_fingerprint is not null;

-- ============================================================================
-- P2.3: files (related_entity_type, related_entity_id) pair consistency.
-- ============================================================================

alter table files drop constraint if exists files_related_pair_chk;
alter table files add constraint files_related_pair_chk
  check ((related_entity_type is null) = (related_entity_id is null));

-- ============================================================================
-- P2.4: drop HNSW indexes on body_embedding columns. Embeddings aren't populated
-- by any write path yet; rebuild the indexes in the future backfill migration.
-- ============================================================================

drop index if exists issues_embedding_hnsw;
drop index if exists comms_embedding_hnsw;
drop index if exists sessions_embedding_hnsw;

-- ============================================================================
-- P1.4: upsert_comm RPC. Mirrors upsert_thought's ON CONFLICT pattern so the
-- partial unique index works as the conflict target. Atomic — no read+write race.
-- ============================================================================

create or replace function upsert_comm(p_row jsonb)
returns jsonb
language plpgsql
as $$
declare
  v_row comms;
  v_id uuid;
  v_was_update boolean := false;
begin
  insert into comms (
    occurred_at, comm_type, platform, duration_min, summary, body,
    key_decisions, technical_insights, contacts, source_file, content_fingerprint, metadata
  )
  values (
    (p_row->>'occurred_at')::timestamptz,
    p_row->>'comm_type',
    p_row->>'platform',
    nullif(p_row->>'duration_min','')::int,
    p_row->>'summary',
    p_row->>'body',
    p_row->>'key_decisions',
    p_row->>'technical_insights',
    case
      when p_row->'contacts' is null or jsonb_typeof(p_row->'contacts') <> 'array' then array[]::text[]
      else array(select jsonb_array_elements_text(p_row->'contacts'))
    end,
    p_row->>'source_file',
    p_row->>'content_fingerprint',
    coalesce(p_row->'metadata', '{}'::jsonb)
  )
  on conflict (content_fingerprint) where content_fingerprint is not null
  do update set
    occurred_at = excluded.occurred_at,
    comm_type = excluded.comm_type,
    platform = excluded.platform,
    duration_min = excluded.duration_min,
    summary = excluded.summary,
    body = excluded.body,
    key_decisions = excluded.key_decisions,
    technical_insights = excluded.technical_insights,
    contacts = excluded.contacts,
    source_file = excluded.source_file,
    metadata = comms.metadata || excluded.metadata,
    updated_at = now()
  returning * into v_row;

  v_id := v_row.id;
  v_was_update := (v_row.created_at <> v_row.updated_at);

  return jsonb_build_object(
    'id', v_id,
    'occurred_at', v_row.occurred_at,
    'comm_type', v_row.comm_type,
    'deduped', v_was_update
  );
end;
$$;

grant execute on function upsert_comm(jsonb) to service_role, authenticated;

-- ============================================================================
-- P1.6: session_start RPC. Preserves existing `started_at` (coalesce), so a
-- retry without `started_at` does not rewrite history.
-- ============================================================================

create or replace function session_start(
  p_session_id text,
  p_title text default null,
  p_project text default null,
  p_claude_uuid text default null,
  p_started_at timestamptz default null
)
returns jsonb
language plpgsql
as $$
declare
  v_row sessions;
begin
  insert into sessions (session_id, title, project, claude_uuid, started_at, status)
  values (p_session_id, p_title, p_project, p_claude_uuid, coalesce(p_started_at, now()), 'active')
  on conflict (session_id) do update set
    title       = coalesce(excluded.title, sessions.title),
    project     = coalesce(excluded.project, sessions.project),
    claude_uuid = coalesce(excluded.claude_uuid, sessions.claude_uuid),
    -- KEY: preserve original started_at unless caller explicitly provided one
    started_at  = coalesce(sessions.started_at, excluded.started_at),
    updated_at  = now()
  returning * into v_row;

  return jsonb_build_object(
    'id', v_row.id,
    'session_id', v_row.session_id,
    'status', v_row.status,
    'started_at', v_row.started_at
  );
end;
$$;

grant execute on function session_start(text, text, text, text, timestamptz)
  to service_role, authenticated;

notify pgrst, 'reload schema';
