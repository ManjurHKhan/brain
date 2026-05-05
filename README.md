# brain

Personal cloud "brain" — single source of memory, skills, and typed entities (issues, todos)
accessible from every AI tool I use (Claude Code, Codex, Gemini, Claude iOS) via MCP.

## Stack

- **Cloud DB & runtime**: Supabase (Postgres + pgvector + Edge Functions on Deno)
- **MCP server**: Single Edge Function at `supabase/functions/mcp/`
- **Embeddings**: OpenRouter `text-embedding-3-small` (1536 dim)
- **Local CLI** (Week 3+): planned but not in v1

## Where things live

| Place | Owns |
|---|---|
| GitHub `manjurhkhan/brain` | All source: SQL migrations, Edge Function code, importers, shims, GH Actions |
| Supabase project | Running app: Postgres tables + Edge Functions |
| Local laptop | Repo clone, heavy artifacts, secrets, `pg_dump` backups |
| AI clients (Claude Code / Codex / Gemini / Claude iOS) | MCP config pointing at the Edge Function URL |

`supabase db push` deploys migrations; `supabase functions deploy mcp` deploys the Edge Function.

## Origin

Vendored from [OB1](https://github.com/NateBJones-Projects/OB1) (read-only snapshot in `vendor/OB1-snapshot/`,
gitignored). No upstream sync — diverged per Codex review of the original spec.

## Plan

Implementation plan: [docs/superpowers/plans/2026-05-04-brain-v1-implementation-plan.md](docs/superpowers/plans/2026-05-04-brain-v1-implementation-plan.md)

## Spec (frozen reference)

`~/.harmoni-state/projects/dev/research/2026-04-30-brain-design.md` — original full design;
v1 is a deliberately reduced subset (no drawers, no KG, no offline mode).
