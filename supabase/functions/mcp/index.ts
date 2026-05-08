import "jsr:@supabase/functions-js/edge-runtime.d.ts";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPTransport } from "@hono/mcp";
import { Hono } from "hono";
import { z } from "zod";
import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const OPENROUTER_API_KEY = Deno.env.get("OPENROUTER_API_KEY")!;
const MCP_ACCESS_KEY = Deno.env.get("MCP_ACCESS_KEY")!;

const OPENROUTER_BASE = "https://openrouter.ai/api/v1";
const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

type ThoughtMatch = {
  id: string;
  content: string;
  metadata: Record<string, unknown>;
  similarity: number;
  created_at: string;
};

type ThoughtRecord = {
  id: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at?: string | null;
};

const CITATION_BASE_URL =
  Deno.env.get("OPEN_BRAIN_CITATION_BASE_URL") || "https://openbrain.local/thoughts";

function thoughtTitle(content: string, createdAt?: string): string {
  const firstLine = content.replace(/\s+/g, " ").trim().slice(0, 80);
  const datePrefix = createdAt ? new Date(createdAt).toLocaleDateString() : "Open Brain";
  return firstLine ? `${datePrefix} - ${firstLine}` : `${datePrefix} thought`;
}

function thoughtUrl(id: string): string {
  return `${CITATION_BASE_URL.replace(/\/$/, "")}/${id}`;
}

async function getEmbedding(text: string): Promise<number[]> {
  const r = await fetch(`${OPENROUTER_BASE}/embeddings`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${OPENROUTER_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "openai/text-embedding-3-small",
      input: text,
    }),
  });
  if (!r.ok) {
    const msg = await r.text().catch(() => "");
    throw new Error(`OpenRouter embeddings failed: ${r.status} ${msg}`);
  }
  const d = await r.json();
  return d.data[0].embedding;
}

async function extractMetadata(text: string): Promise<Record<string, unknown>> {
  const r = await fetch(`${OPENROUTER_BASE}/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${OPENROUTER_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "openai/gpt-4o-mini",
      response_format: { type: "json_object" },
      messages: [
        {
          role: "system",
          content: `Extract metadata from the user's captured thought. Return JSON with:
- "people": array of people mentioned (empty if none)
- "action_items": array of implied to-dos (empty if none)
- "dates_mentioned": array of dates YYYY-MM-DD (empty if none)
- "topics": array of 1-3 short topic tags (always at least one)
- "type": one of "observation", "task", "idea", "reference", "person_note"
Only extract what's explicitly there.`,
        },
        { role: "user", content: text },
      ],
    }),
  });
  const d = await r.json();
  try {
    return JSON.parse(d.choices[0].message.content);
  } catch {
    return { topics: ["uncategorized"], type: "observation" };
  }
}

// --- MCP Server Setup ---

const server = new McpServer({
  name: "open-brain",
  version: "1.0.0",
});

// ChatGPT compatibility: restricted connector surfaces, company knowledge, and deep
// research look for exact read-only `search` and `fetch` tool shapes.
server.registerTool(
  "search",
  {
    title: "Search Open Brain",
    description:
      "Search Open Brain memories by meaning. Use this read-only compatibility tool when ChatGPT needs search/fetch-style access to stored thoughts.",
    annotations: {
      readOnlyHint: true,
    },
    inputSchema: {
      query: z.string().describe("The search query to run against Open Brain thoughts"),
    },
  },
  async ({ query }) => {
    try {
      const qEmb = await getEmbedding(query);
      const { data, error } = await supabase.rpc("match_thoughts", {
        query_embedding: qEmb,
        match_threshold: 0.5,
        match_count: 10,
        filter: {},
      });

      if (error) {
        return {
          content: [{ type: "text" as const, text: `Search error: ${error.message}` }],
          isError: true,
        };
      }

      const results = ((data || []) as ThoughtMatch[]).map((t) => ({
        id: t.id,
        title: thoughtTitle(t.content, t.created_at),
        url: thoughtUrl(t.id),
      }));

      return {
        content: [{ type: "text" as const, text: JSON.stringify({ results }) }],
      };
    } catch (err: unknown) {
      return {
        content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }],
        isError: true,
      };
    }
  }
);

server.registerTool(
  "fetch",
  {
    title: "Fetch Open Brain Thought",
    description:
      "Fetch one Open Brain thought by ID after using search. Use this read-only compatibility tool to retrieve the full text and metadata for citation.",
    annotations: {
      readOnlyHint: true,
    },
    inputSchema: {
      id: z.string().describe("The Open Brain thought ID returned by the search tool"),
    },
  },
  async ({ id }) => {
    try {
      const { data, error } = await supabase
        .from("thoughts")
        .select("id, content, metadata, created_at, updated_at")
        .eq("id", id)
        .single();

      if (error) {
        return {
          content: [{ type: "text" as const, text: `Fetch error: ${error.message}` }],
          isError: true,
        };
      }

      const thought = data as ThoughtRecord;
      const document = {
        id: thought.id,
        title: thoughtTitle(thought.content, thought.created_at),
        text: thought.content,
        url: thoughtUrl(thought.id),
        metadata: {
          ...thought.metadata,
          created_at: thought.created_at,
          updated_at: thought.updated_at,
        },
      };

      return {
        content: [{ type: "text" as const, text: JSON.stringify(document) }],
      };
    } catch (err: unknown) {
      return {
        content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }],
        isError: true,
      };
    }
  }
);

// Tool 1: Semantic Search
server.registerTool(
  "search_thoughts",
  {
    title: "Search Thoughts",
    description:
      "Search captured thoughts by meaning. Use this when the user asks about a topic, person, or idea they've previously captured.",
    annotations: {
      readOnlyHint: true,
    },
    inputSchema: {
      query: z.string().describe("What to search for"),
      limit: z.number().optional().default(10),
      threshold: z.number().optional().default(0.5),
    },
  },
  async ({ query, limit, threshold }) => {
    try {
      const qEmb = await getEmbedding(query);
      const { data, error } = await supabase.rpc("match_thoughts", {
        query_embedding: qEmb,
        match_threshold: threshold,
        match_count: limit,
        filter: {},
      });

      if (error) {
        return {
          content: [{ type: "text" as const, text: `Search error: ${error.message}` }],
          isError: true,
        };
      }

      if (!data || data.length === 0) {
        return {
          content: [{ type: "text" as const, text: `No thoughts found matching "${query}".` }],
        };
      }

      const results = data.map(
        (
          t: ThoughtMatch,
          i: number
        ) => {
          const m = t.metadata || {};
          const parts = [
            `--- Result ${i + 1} (${(t.similarity * 100).toFixed(1)}% match) ---`,
            `Captured: ${new Date(t.created_at).toLocaleDateString()}`,
            `Type: ${m.type || "unknown"}`,
          ];
          if (Array.isArray(m.topics) && m.topics.length)
            parts.push(`Topics: ${(m.topics as string[]).join(", ")}`);
          if (Array.isArray(m.people) && m.people.length)
            parts.push(`People: ${(m.people as string[]).join(", ")}`);
          if (Array.isArray(m.action_items) && m.action_items.length)
            parts.push(`Actions: ${(m.action_items as string[]).join("; ")}`);
          parts.push(`\n${t.content}`);
          return parts.join("\n");
        }
      );

      return {
        content: [
          {
            type: "text" as const,
            text: `Found ${data.length} thought(s):\n\n${results.join("\n\n")}`,
          },
        ],
      };
    } catch (err: unknown) {
      return {
        content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }],
        isError: true,
      };
    }
  }
);

// Tool 2: List Recent
server.registerTool(
  "list_thoughts",
  {
    title: "List Recent Thoughts",
    description:
      "List recently captured thoughts with optional filters by type, topic, person, or time range.",
    annotations: {
      readOnlyHint: true,
    },
    inputSchema: {
      limit: z.number().optional().default(10),
      type: z.string().optional().describe("Filter by type: observation, task, idea, reference, person_note"),
      topic: z.string().optional().describe("Filter by topic tag"),
      person: z.string().optional().describe("Filter by person mentioned"),
      days: z.number().optional().describe("Only thoughts from the last N days"),
    },
  },
  async ({ limit, type, topic, person, days }) => {
    try {
      let q = supabase
        .from("thoughts")
        .select("content, metadata, created_at")
        .order("created_at", { ascending: false })
        .limit(limit);

      if (type) q = q.contains("metadata", { type });
      if (topic) q = q.contains("metadata", { topics: [topic] });
      if (person) q = q.contains("metadata", { people: [person] });
      if (days) {
        const since = new Date();
        since.setDate(since.getDate() - days);
        q = q.gte("created_at", since.toISOString());
      }

      const { data, error } = await q;

      if (error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${error.message}` }],
          isError: true,
        };
      }

      if (!data || !data.length) {
        return { content: [{ type: "text" as const, text: "No thoughts found." }] };
      }

      const results = data.map(
        (
          t: { content: string; metadata: Record<string, unknown>; created_at: string },
          i: number
        ) => {
          const m = t.metadata || {};
          const tags = Array.isArray(m.topics) ? (m.topics as string[]).join(", ") : "";
          return `${i + 1}. [${new Date(t.created_at).toLocaleDateString()}] (${m.type || "??"}${tags ? " - " + tags : ""})\n   ${t.content}`;
        }
      );

      return {
        content: [
          {
            type: "text" as const,
            text: `${data.length} recent thought(s):\n\n${results.join("\n\n")}`,
          },
        ],
      };
    } catch (err: unknown) {
      return {
        content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }],
        isError: true,
      };
    }
  }
);

// Tool 3: Stats
server.registerTool(
  "thought_stats",
  {
    title: "Thought Statistics",
    description: "Get a summary of all captured thoughts: totals, types, top topics, and people.",
    annotations: {
      readOnlyHint: true,
    },
    inputSchema: {},
  },
  async () => {
    try {
      const { count } = await supabase
        .from("thoughts")
        .select("*", { count: "exact", head: true });

      const { data } = await supabase
        .from("thoughts")
        .select("metadata, created_at")
        .order("created_at", { ascending: false });

      const types: Record<string, number> = {};
      const topics: Record<string, number> = {};
      const people: Record<string, number> = {};

      for (const r of data || []) {
        const m = (r.metadata || {}) as Record<string, unknown>;
        if (m.type) types[m.type as string] = (types[m.type as string] || 0) + 1;
        if (Array.isArray(m.topics))
          for (const t of m.topics) topics[t as string] = (topics[t as string] || 0) + 1;
        if (Array.isArray(m.people))
          for (const p of m.people) people[p as string] = (people[p as string] || 0) + 1;
      }

      const sort = (o: Record<string, number>): [string, number][] =>
        Object.entries(o)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 10);

      const lines: string[] = [
        `Total thoughts: ${count}`,
        `Date range: ${
          data?.length
            ? new Date(data[data.length - 1].created_at).toLocaleDateString() +
              " → " +
              new Date(data[0].created_at).toLocaleDateString()
            : "N/A"
        }`,
        "",
        "Types:",
        ...sort(types).map(([k, v]) => `  ${k}: ${v}`),
      ];

      if (Object.keys(topics).length) {
        lines.push("", "Top topics:");
        for (const [k, v] of sort(topics)) lines.push(`  ${k}: ${v}`);
      }

      if (Object.keys(people).length) {
        lines.push("", "People mentioned:");
        for (const [k, v] of sort(people)) lines.push(`  ${k}: ${v}`);
      }

      return { content: [{ type: "text" as const, text: lines.join("\n") }] };
    } catch (err: unknown) {
      return {
        content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }],
        isError: true,
      };
    }
  }
);

// Tool 4: Capture Thought
server.registerTool(
  "capture_thought",
  {
    title: "Capture Thought",
    description:
      "Save a new thought to the Open Brain. Generates an embedding and extracts metadata automatically. Use this when the user wants to save something to their brain directly from any AI client — notes, insights, decisions, or migrated content from other systems.",
    annotations: {
      readOnlyHint: false,
      openWorldHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
    inputSchema: {
      content: z.string().describe("The thought to capture — a clear, standalone statement that will make sense when retrieved later by any AI"),
    },
  },
  async ({ content }) => {
    try {
      const [embedding, metadata] = await Promise.all([
        getEmbedding(content),
        extractMetadata(content),
      ]);

      const { data: upsertResult, error: upsertError } = await supabase.rpc("upsert_thought", {
        p_content: content,
        p_payload: { metadata: { ...metadata, source: "mcp" } },
      });

      if (upsertError) {
        return {
          content: [{ type: "text" as const, text: `Failed to capture: ${upsertError.message}` }],
          isError: true,
        };
      }

      const thoughtId = upsertResult?.id;
      const { error: embError } = await supabase
        .from("thoughts")
        .update({ embedding })
        .eq("id", thoughtId);

      if (embError) {
        return {
          content: [{ type: "text" as const, text: `Failed to save embedding: ${embError.message}` }],
          isError: true,
        };
      }

      const meta = metadata as Record<string, unknown>;
      let confirmation = `Captured as ${meta.type || "thought"}`;
      if (Array.isArray(meta.topics) && meta.topics.length)
        confirmation += ` — ${(meta.topics as string[]).join(", ")}`;
      if (Array.isArray(meta.people) && meta.people.length)
        confirmation += ` | People: ${(meta.people as string[]).join(", ")}`;
      if (Array.isArray(meta.action_items) && meta.action_items.length)
        confirmation += ` | Actions: ${(meta.action_items as string[]).join("; ")}`;

      return {
        content: [{ type: "text" as const, text: confirmation }],
      };
    } catch (err: unknown) {
      return {
        content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }],
        isError: true,
      };
    }
  }
);

// --- Skills tools (added 2026-05-06) ---

server.registerTool(
  "skills_list",
  {
    title: "List Skills",
    description:
      "List skills available in the brain. Optional filter by vendor (returns has_variant flag) or by tag.",
    annotations: { readOnlyHint: true },
    inputSchema: {
      vendor: z.string().optional().describe("'claude' | 'codex' | 'gemini' | 'openclaw' | 'local'"),
      tag: z.string().optional(),
    },
  },
  async ({ vendor, tag }) => {
    try {
      let q = supabase.from("skills").select("id, slug, name, description, tags");
      if (tag) q = q.contains("tags", [tag]);
      const { data, error } = await q.order("slug");
      if (error) {
        return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      }
      let skills = data ?? [];
      if (vendor) {
        const { data: variants } = await supabase
          .from("skill_variants")
          .select("skill_id")
          .eq("vendor", vendor);
        const variantSet = new Set((variants ?? []).map((v: { skill_id: string }) => v.skill_id));
        skills = skills.map((s: { id: string }) => ({ ...s, has_variant: variantSet.has(s.id) }));
      }
      return { content: [{ type: "text" as const, text: JSON.stringify(skills, null, 2) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "skill_get",
  {
    title: "Get Skill Body",
    description:
      "Resolve a skill's body for the calling vendor. If a variant exists in mode='replace', returns the variant body; if 'extend', returns generic + variant; if no variant, returns generic.",
    annotations: { readOnlyHint: true },
    inputSchema: {
      slug: z.string(),
      vendor: z.string().optional().describe("Defaults to 'claude'. Use 'generic' to force generic body."),
    },
  },
  async ({ slug, vendor }) => {
    try {
      const v = vendor ?? "claude";
      const { data: skill, error } = await supabase
        .from("skills").select("id, generic_body, name, description, tags").eq("slug", slug).maybeSingle();
      if (error) {
        return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      }
      if (!skill) {
        return { content: [{ type: "text" as const, text: `Skill not found: ${slug}` }], isError: true };
      }
      if (v === "generic") {
        return { content: [{ type: "text" as const, text: JSON.stringify({ slug, name: skill.name, source: "generic", body: skill.generic_body }) }] };
      }
      const { data: variant } = await supabase
        .from("skill_variants").select("body, mode").eq("skill_id", skill.id).eq("vendor", v).maybeSingle();
      let body = skill.generic_body;
      let source = "generic";
      if (variant) {
        if (variant.mode === "replace") { body = variant.body; source = `variant:${v}:replace`; }
        else if (variant.mode === "extend") { body = `${skill.generic_body}\n\n${variant.body}`; source = `variant:${v}:extend`; }
      }
      return { content: [{ type: "text" as const, text: JSON.stringify({ slug, name: skill.name, source, body }) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "skill_upsert",
  {
    title: "Upsert Skill",
    description:
      "Create or update a skill by slug. Pass generic_body. Tags optional.",
    inputSchema: {
      slug: z.string(),
      name: z.string(),
      description: z.string(),
      generic_body: z.string(),
      tags: z.array(z.string()).optional(),
    },
  },
  async ({ slug, name, description, generic_body, tags }) => {
    try {
      const { data, error } = await supabase
        .from("skills")
        .upsert({ slug, name, description, generic_body, tags: tags ?? [] }, { onConflict: "slug" })
        .select("id, slug, version, updated_at")
        .single();
      if (error) {
        return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      }
      return { content: [{ type: "text" as const, text: JSON.stringify(data) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "skill_variant_upsert",
  {
    title: "Upsert Skill Variant",
    description:
      "Create or update a vendor-specific variant of a skill. Use this to layer LLM-specific guidance (Claude Code, Codex, Gemini, mobile) on top of a vendor-agnostic generic_body. Mode 'extend' appends variant body to generic_body; mode 'replace' substitutes it entirely. The skill must already exist (call skill_upsert first).",
    inputSchema: {
      slug: z.string().describe("The parent skill slug (must already exist in skills table)"),
      vendor: z.string().describe("'claude' | 'codex' | 'gemini' | 'openclaw' | 'local'"),
      body: z.string().describe("The vendor-specific guidance"),
      mode: z.enum(["replace", "extend"]).optional().describe("Default 'extend' — variant text appended to generic_body. Use 'replace' for fully separate vendor implementations."),
    },
  },
  async ({ slug, vendor, body, mode }) => {
    try {
      const { data: skill, error: skillErr } = await supabase
        .from("skills").select("id").eq("slug", slug).maybeSingle();
      if (skillErr) {
        return { content: [{ type: "text" as const, text: `Error: ${skillErr.message}` }], isError: true };
      }
      if (!skill) {
        return { content: [{ type: "text" as const, text: `Skill not found: ${slug}. Call skill_upsert first.` }], isError: true };
      }
      const { data, error } = await supabase
        .from("skill_variants")
        .upsert(
          { skill_id: skill.id, vendor, body, mode: mode ?? "extend" },
          { onConflict: "skill_id,vendor" }
        )
        .select("id, vendor, mode, version, created_at")
        .single();
      if (error) {
        return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      }
      return { content: [{ type: "text" as const, text: JSON.stringify(data) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

// --- Issues tools (added 2026-05-07) ---

const ISSUE_STATUSES = ["backlog", "in-progress", "review", "blocked", "done", "wontfix"] as const;
const ISSUE_TYPES = ["task", "bug", "epic", "spike", "research", "chore"] as const;
const PRIORITIES = ["low", "medium", "high", "critical"] as const;

server.registerTool(
  "issue_create",
  {
    title: "Create Issue",
    description:
      "Create an issue. Codes are user-supplied (e.g., 'INFRA-058'); uniqueness enforced on (project_slug, code).",
    inputSchema: {
      project_slug: z.string().describe("'INFRA' | 'GRC' | 'DEV' | 'PERS' | 'MANJUR'"),
      code: z.string().describe("Full issue code, e.g. 'INFRA-058'"),
      title: z.string(),
      body: z.string().optional(),
      type: z.enum(ISSUE_TYPES).optional(),
      status: z.enum(ISSUE_STATUSES).optional(),
      priority: z.enum(PRIORITIES).optional(),
      assignee: z.string().optional(),
      effort_hours: z.number().optional(),
      due_date: z.string().optional().describe("YYYY-MM-DD"),
      parent_code: z.string().optional(),
      controls: z.array(z.string()).optional(),
      tags: z.array(z.string()).optional(),
      jira_key: z.string().optional(),
    },
  },
  async (args) => {
    try {
      const insert: Record<string, unknown> = { ...args };
      if (!insert.controls) insert.controls = [];
      if (!insert.tags) insert.tags = [];
      const { data, error } = await supabase.from("issues").insert(insert).select().single();
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      // Seed status history
      await supabase.from("issue_status_history").insert({
        issue_id: data.id, from_status: null, to_status: data.status, changed_by: "mcp",
      });
      return { content: [{ type: "text" as const, text: JSON.stringify({ id: data.id, code: data.code, status: data.status, updated_at: data.updated_at }) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "issue_update",
  {
    title: "Update Issue",
    description:
      "Patch an issue. Pass `expected_updated_at` from the row you read — if it doesn't match the current value, the call returns {error:'stale', current:<row>} and you should re-read and retry.",
    inputSchema: {
      code: z.string(),
      expected_updated_at: z.string().describe("ISO timestamp; optimistic concurrency token"),
      title: z.string().optional(),
      body: z.string().optional(),
      type: z.enum(ISSUE_TYPES).optional(),
      status: z.enum(ISSUE_STATUSES).optional(),
      priority: z.enum(PRIORITIES).optional(),
      assignee: z.string().nullable().optional(),
      effort_hours: z.number().nullable().optional(),
      due_date: z.string().nullable().optional(),
      parent_code: z.string().nullable().optional(),
      controls: z.array(z.string()).optional(),
      tags: z.array(z.string()).optional(),
      jira_key: z.string().nullable().optional(),
    },
  },
  async (args) => {
    try {
      const { code, expected_updated_at, ...patch } = args as Record<string, unknown> & { code: string; expected_updated_at: string };
      const { data: current, error: readErr } = await supabase
        .from("issues").select("*").eq("code", code).maybeSingle();
      if (readErr) return { content: [{ type: "text" as const, text: `Error: ${readErr.message}` }], isError: true };
      if (!current) return { content: [{ type: "text" as const, text: `Issue not found: ${code}` }], isError: true };
      // Compare ISO timestamps as strings; Postgres returns microsecond precision so normalize.
      if (new Date(current.updated_at).toISOString() !== new Date(expected_updated_at).toISOString()) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "stale", current }) }] };
      }
      const { data, error } = await supabase.from("issues")
        .update(patch).eq("id", current.id).eq("updated_at", current.updated_at)
        .select().single();
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      // Status transition history
      const newStatus = patch.status as string | undefined;
      if (newStatus && newStatus !== current.status) {
        await supabase.from("issue_status_history").insert({
          issue_id: current.id, from_status: current.status, to_status: newStatus, changed_by: "mcp",
        });
      }
      return { content: [{ type: "text" as const, text: JSON.stringify({ id: data.id, code: data.code, status: data.status, updated_at: data.updated_at }) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "issue_get",
  {
    title: "Get Issue",
    description: "Fetch a single issue by code, including all comments and status history.",
    annotations: { readOnlyHint: true },
    inputSchema: { code: z.string() },
  },
  async ({ code }) => {
    try {
      const { data, error } = await supabase
        .from("issues")
        .select("*, issue_comments (id, body, author, created_at), issue_status_history (id, from_status, to_status, changed_by, changed_at)")
        .eq("code", code).maybeSingle();
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      if (!data) return { content: [{ type: "text" as const, text: `Issue not found: ${code}` }], isError: true };
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "issue_list",
  {
    title: "List Issues",
    description: "List issues with optional filters. Returns code/title/status/priority/assignee/updated_at — call issue_get for full detail.",
    annotations: { readOnlyHint: true },
    inputSchema: {
      project_slug: z.string().optional(),
      status: z.enum(ISSUE_STATUSES).optional(),
      assignee: z.string().optional(),
      tag: z.string().optional().describe("Single tag to filter by"),
      limit: z.number().optional().default(50),
    },
  },
  async ({ project_slug, status, assignee, tag, limit }) => {
    try {
      let q = supabase.from("issues").select("code, project_slug, title, type, status, priority, assignee, due_date, tags, updated_at");
      if (project_slug) q = q.eq("project_slug", project_slug);
      if (status) q = q.eq("status", status);
      if (assignee) q = q.eq("assignee", assignee);
      if (tag) q = q.contains("tags", [tag]);
      q = q.order("updated_at", { ascending: false }).limit(limit);
      const { data, error } = await q;
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      return { content: [{ type: "text" as const, text: JSON.stringify(data ?? [], null, 2) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "issue_comment",
  {
    title: "Comment on Issue",
    description: "Add a comment to an issue.",
    inputSchema: {
      code: z.string(),
      body: z.string(),
      author: z.string().optional(),
    },
  },
  async ({ code, body, author }) => {
    try {
      const { data: issue, error: lookupErr } = await supabase
        .from("issues").select("id").eq("code", code).maybeSingle();
      if (lookupErr) return { content: [{ type: "text" as const, text: `Error: ${lookupErr.message}` }], isError: true };
      if (!issue) return { content: [{ type: "text" as const, text: `Issue not found: ${code}` }], isError: true };
      const { data, error } = await supabase
        .from("issue_comments").insert({ issue_id: issue.id, body, author }).select().single();
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      return { content: [{ type: "text" as const, text: JSON.stringify(data) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "issue_transition",
  {
    title: "Transition Issue Status",
    description: "Move an issue to a new status. Convenience wrapper over issue_update; requires expected_updated_at for optimistic concurrency.",
    inputSchema: {
      code: z.string(),
      to_status: z.enum(ISSUE_STATUSES),
      expected_updated_at: z.string(),
    },
  },
  async ({ code, to_status, expected_updated_at }) => {
    try {
      const { data: current, error: readErr } = await supabase
        .from("issues").select("*").eq("code", code).maybeSingle();
      if (readErr) return { content: [{ type: "text" as const, text: `Error: ${readErr.message}` }], isError: true };
      if (!current) return { content: [{ type: "text" as const, text: `Issue not found: ${code}` }], isError: true };
      if (new Date(current.updated_at).toISOString() !== new Date(expected_updated_at).toISOString()) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "stale", current }) }] };
      }
      const { data, error } = await supabase.from("issues")
        .update({ status: to_status }).eq("id", current.id).eq("updated_at", current.updated_at)
        .select().single();
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      if (to_status !== current.status) {
        await supabase.from("issue_status_history").insert({
          issue_id: current.id, from_status: current.status, to_status, changed_by: "mcp",
        });
      }
      return { content: [{ type: "text" as const, text: JSON.stringify({ code: data.code, status: data.status, updated_at: data.updated_at }) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

// --- Todos tools (added 2026-05-07) ---

const TODO_STATUSES = ["open", "in-progress", "waiting", "done", "cancelled"] as const;

server.registerTool(
  "todo_create",
  {
    title: "Create Todo",
    description:
      "Create a todo. `assignee` is the conventional vendor identifier ('manjur' | 'claude-code' | 'codex' | 'gemini' | 'mobile') or NULL for unassigned. `created_by` is stamped automatically; do not supply.",
    inputSchema: {
      content: z.string(),
      assignee: z.string().optional(),
      priority: z.enum(PRIORITIES).optional(),
      context: z.string().optional().describe("Free-text grouping like project/area"),
      tags: z.array(z.string()).optional(),
      due_at: z.string().optional().describe("ISO timestamp"),
      comm_id: z.string().uuid().optional().describe("Link to a comm if this todo is an action item"),
    },
  },
  async (args) => {
    try {
      // For now the shared MCP key doesn't tell us which vendor called; left blank until per-client keys land.
      const insert: Record<string, unknown> = { ...args, tags: args.tags ?? [] };
      const { data, error } = await supabase.from("todos").insert(insert).select().single();
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      return { content: [{ type: "text" as const, text: JSON.stringify({ id: data.id, status: data.status, assignee: data.assignee, updated_at: data.updated_at }) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "todo_update",
  {
    title: "Update Todo",
    description:
      "Patch a todo with optimistic concurrency. Returns {error:'stale', current:<row>} if expected_updated_at is wrong.",
    inputSchema: {
      id: z.string().uuid(),
      expected_updated_at: z.string(),
      content: z.string().optional(),
      status: z.enum(TODO_STATUSES).optional(),
      assignee: z.string().nullable().optional(),
      priority: z.enum(PRIORITIES).optional(),
      context: z.string().nullable().optional(),
      tags: z.array(z.string()).optional(),
      due_at: z.string().nullable().optional(),
      completed_at: z.string().nullable().optional(),
    },
  },
  async (args) => {
    try {
      const { id, expected_updated_at, ...patch } = args as Record<string, unknown> & { id: string; expected_updated_at: string };
      const { data: current, error: readErr } = await supabase
        .from("todos").select("*").eq("id", id).maybeSingle();
      if (readErr) return { content: [{ type: "text" as const, text: `Error: ${readErr.message}` }], isError: true };
      if (!current) return { content: [{ type: "text" as const, text: `Todo not found: ${id}` }], isError: true };
      if (new Date(current.updated_at).toISOString() !== new Date(expected_updated_at).toISOString()) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "stale", current }) }] };
      }
      // Auto-stamp completed_at when transitioning to done
      if (patch.status === "done" && !patch.completed_at && !current.completed_at) {
        patch.completed_at = new Date().toISOString();
      }
      const { data, error } = await supabase.from("todos")
        .update(patch).eq("id", id).eq("updated_at", current.updated_at)
        .select().single();
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      return { content: [{ type: "text" as const, text: JSON.stringify(data) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "todo_list",
  {
    title: "List Todos",
    description:
      "List todos with optional filters. Use `assignee` to pick up work targeted at a specific vendor (codex, claude-code, etc.).",
    annotations: { readOnlyHint: true },
    inputSchema: {
      status: z.enum(TODO_STATUSES).optional(),
      assignee: z.string().optional().describe("'manjur'|'claude-code'|'codex'|'gemini'|'mobile' or any string. Use 'unassigned' to filter for NULL."),
      context: z.string().optional(),
      tag: z.string().optional(),
      limit: z.number().optional().default(50),
    },
  },
  async ({ status, assignee, context, tag, limit }) => {
    try {
      let q = supabase.from("todos").select("id, content, status, assignee, priority, context, tags, due_at, completed_at, created_at, updated_at");
      if (status) q = q.eq("status", status);
      if (assignee === "unassigned") q = q.is("assignee", null);
      else if (assignee) q = q.eq("assignee", assignee);
      if (context) q = q.eq("context", context);
      if (tag) q = q.contains("tags", [tag]);
      q = q.order("created_at", { ascending: false }).limit(limit);
      const { data, error } = await q;
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      return { content: [{ type: "text" as const, text: JSON.stringify(data ?? [], null, 2) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "todo_done",
  {
    title: "Mark Todo Done",
    description: "Convenience wrapper: set status='done' and stamp completed_at=now(). Requires expected_updated_at.",
    inputSchema: {
      id: z.string().uuid(),
      expected_updated_at: z.string(),
    },
  },
  async ({ id, expected_updated_at }) => {
    try {
      const { data: current, error: readErr } = await supabase
        .from("todos").select("*").eq("id", id).maybeSingle();
      if (readErr) return { content: [{ type: "text" as const, text: `Error: ${readErr.message}` }], isError: true };
      if (!current) return { content: [{ type: "text" as const, text: `Todo not found: ${id}` }], isError: true };
      if (new Date(current.updated_at).toISOString() !== new Date(expected_updated_at).toISOString()) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "stale", current }) }] };
      }
      const { data, error } = await supabase.from("todos")
        .update({ status: "done", completed_at: new Date().toISOString() })
        .eq("id", id).eq("updated_at", current.updated_at)
        .select().single();
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      return { content: [{ type: "text" as const, text: JSON.stringify(data) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

server.registerTool(
  "todo_assign",
  {
    title: "Assign Todo to a Vendor",
    description:
      "Hand off a todo to a different vendor (or to `manjur` for human pickup). Conventionally 'manjur'|'claude-code'|'codex'|'gemini'|'mobile'. Pass null to unassign.",
    inputSchema: {
      id: z.string().uuid(),
      assignee: z.string().nullable(),
      expected_updated_at: z.string(),
    },
  },
  async ({ id, assignee, expected_updated_at }) => {
    try {
      const { data: current, error: readErr } = await supabase
        .from("todos").select("*").eq("id", id).maybeSingle();
      if (readErr) return { content: [{ type: "text" as const, text: `Error: ${readErr.message}` }], isError: true };
      if (!current) return { content: [{ type: "text" as const, text: `Todo not found: ${id}` }], isError: true };
      if (new Date(current.updated_at).toISOString() !== new Date(expected_updated_at).toISOString()) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "stale", current }) }] };
      }
      const { data, error } = await supabase.from("todos")
        .update({ assignee }).eq("id", id).eq("updated_at", current.updated_at)
        .select().single();
      if (error) return { content: [{ type: "text" as const, text: `Error: ${error.message}` }], isError: true };
      return { content: [{ type: "text" as const, text: JSON.stringify(data) }] };
    } catch (err: unknown) {
      return { content: [{ type: "text" as const, text: `Error: ${(err as Error).message}` }], isError: true };
    }
  }
);

// --- Hono App with Auth + CORS ---

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-brain-key, accept, mcp-session-id, mcp-protocol-version, last-event-id",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS, DELETE",
};

const app = new Hono();

// CORS preflight — required for browser/Electron-based clients (Claude Desktop, claude.ai)
app.options("*", (c) => {
  return c.text("ok", 200, corsHeaders);
});

app.all("*", async (c) => {
  // Accept access key via header OR URL query parameter
  const provided = c.req.header("x-brain-key") || new URL(c.req.url).searchParams.get("key");
  if (!provided || provided !== MCP_ACCESS_KEY) {
    return c.json({ error: "Invalid or missing access key" }, 401, corsHeaders);
  }

  // Fix: Claude Desktop connectors don't send the Accept header that
  // StreamableHTTPTransport requires. Build a patched request if missing.
  // See: https://github.com/NateBJones-Projects/OB1/issues/33
  if (!c.req.header("accept")?.includes("text/event-stream")) {
    const headers = new Headers(c.req.raw.headers);
    headers.set("Accept", "application/json, text/event-stream");
    const patched = new Request(c.req.raw.url, {
      method: c.req.raw.method,
      headers,
      body: c.req.raw.body,
      // @ts-ignore -- duplex required for streaming body in Deno
      duplex: "half",
    });
    Object.defineProperty(c.req, "raw", { value: patched, writable: true });
  }

  const transport = new StreamableHTTPTransport();
  await server.connect(transport);
  return transport.handleRequest(c);
});

Deno.serve(app.fetch);
