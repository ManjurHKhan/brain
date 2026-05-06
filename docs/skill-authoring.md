# Skill Authoring Guide

Guidance for writing brain skills that work across every AI client (Claude Code, Codex, Gemini, Claude iOS).

## The Two-Layer Model

| Layer | Table.column | Content |
|---|---|---|
| **Generic** | `skills.generic_body` | Pure intent. Prerequisites. Outcome. Examples. **No tool names. No platform assumptions.** |
| **Variant (per vendor)** | `skill_variants.body` | Vendor-specific guidance — what to do *in this client*: which tool to call, where files live, how subtasks are tracked. |

The generic body is what every client sees by default. Variants are loaded by `skill_get(slug, vendor)` and either `extend` (appended) or `replace` (substitute).

## Vendors

The schema enforces these vendor identifiers:

| Vendor | Covers |
|---|---|
| `claude` | Claude Code (desktop) **and** Claude iOS. Same model, similar capabilities — only platform diffs (iOS can't write files, has no Skill tool). If you need to differentiate, write the variant for Code and add explicit "on iOS, …" notes inside it. |
| `codex` | OpenAI Codex CLI. No subagents, no `Skill` tool, no `TodoWrite`. Has skills loaded from plugin packs. Local execution. |
| `gemini` | Gemini CLI. MCP support varies; verify per-version. |
| `openclaw` | Reserved. |
| `local` | Local-only execution paths (e.g., a CLI that doesn't go through any LLM). |

## Mode Choice

```
extend  → variant body is appended to generic_body (use for "and also, in this client...")
replace → variant body fully replaces generic_body (use when the workflow is fundamentally different)
```

**Default to `extend`.** It keeps the generic intent intact and layers vendor specifics on top. Use `replace` only when the entire procedure differs (e.g., "in iOS, you can't, so instead suggest steps to the user").

## What Belongs in Generic vs. Variant

| Belongs in `generic_body` | Belongs in a `variant` |
|---|---|
| What the skill is for. Why. | "Invoke via the Skill tool" |
| Inputs, outputs, success criteria. | "Track sub-steps with TodoWrite" |
| Decision logic. | "Run the command via the Bash tool" |
| Examples (described abstractly: "verify the build, e.g. `npm test`"). | File-system paths specific to a client (`~/.claude/...`, `~/.codex/...`) |
| The "iron law" — non-negotiable rules. | "Restart the dev server in a browser before claiming UI complete" |
| | Subagent / Agent tool guidance |

**Test:** If you replaced the AI client name with a generic "the assistant", would the generic body still make sense? If yes, it's truly generic. If you had to invent a tool name, it belongs in a variant.

## Worked Example

`verification-before-completion` started life as a Claude Code skill — references "Bash tool", "VCS diff", "Agent tool". After refactor:

**generic_body** (unchanged from original — already vendor-neutral enough):
> Iron law: no completion claims without fresh verification evidence. Identify the command that proves the claim, run it, read the output, then claim. (Full text in DB.)

**variants[claude]** (`extend` mode):
> ### Claude Code addendum
> 1. Run the verification command via the Bash tool, don't just reason about it
> 2. Quote the relevant output back in your response so the user sees evidence
> 3. If no test framework exists, say so explicitly rather than asserting success
> 4. For UI changes, start the dev server and exercise the feature in a browser before reporting done

`skill_get(slug='verification-before-completion', vendor='claude')` returns generic + addendum, `source: "variant:claude:extend"`.
`skill_get(slug='verification-before-completion', vendor='codex')` returns just the generic, `source: "generic"`.

## Writing Variants — MCP Tools

```
skill_upsert(slug, name, description, generic_body, tags?)        — write/update generic
skill_variant_upsert(slug, vendor, body, mode='extend'|'replace') — write/update a variant
skill_get(slug, vendor)                                           — resolve (variant if exists, else generic)
```

The skill must exist before adding variants — `skill_variant_upsert` will reject with "Skill not found" otherwise.

## Refactor Checklist

When converting a Claude-Code-flavored skill into the two-layer form:

1. Read the existing body. Highlight every CC-specific reference (tool names, file paths, "in Claude Code", subagent invocations).
2. **Generic body** = the original with those references rewritten or removed. Use abstract language: "verify via your test command", "track sub-steps", "delegate parallel work".
3. **Claude variant** = a short `extend` block listing what those references became in Claude Code specifically.
4. **Codex/Gemini variants** = optional. Only add if the workflow genuinely differs (Codex has no subagents → say "for parallel work, run multiple times manually" if relevant).
5. `skill_upsert` the new generic body, then `skill_variant_upsert` each variant.
6. Verify with `skill_get(slug, vendor='claude')` and `skill_get(slug, vendor='codex')` — read both bodies, confirm the right thing is in the right place.

## Slash Commands as Skills

Slash commands (`~/.claude/commands/*.md`) are mined into brain with slug prefix `cmd:` and tag `kind:command`. They're discoverable via `skills_list(tag='kind:command')`. Bodies still call local tooling (`hs todo add`, `hs dashboard`, etc.) — once Tasks 17–18 reroute to brain MCP tools, these become genuinely cross-client.

For now: cloud-only clients (iOS, Gemini Cloud) can read the workflow but can't execute it. Local clients (Claude Code, Codex) can both read and execute.

## Don't

- **Don't** write tool names into `generic_body`. (`Skill`, `TodoWrite`, `Bash`, `EnterPlanMode`, `Agent`/subagents are all Claude Code only.)
- **Don't** assume `~/.claude/` exists from Codex/Gemini.
- **Don't** add a variant unless it changes behavior. If the variant just restates the generic, drop it.
- **Don't** write a `replace` variant that drops the generic's iron-law content. Use `extend`.
