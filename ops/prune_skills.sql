-- Prune off-topic skills mined from plugin:knowledge-work-plugins.
-- These are sales/finance/legal/marketing skills bundled with Anthropic's
-- knowledge-work plugin pack. None map to FedRAMP / infra / security work.
-- skill_variants cascades on skill delete (FK on delete cascade).
--
-- Run order:
--   1. Preview count + sample
--   2. Delete
--   3. Verify final count

-- ── Preview ────────────────────────────────────────────────────────────────
SELECT count(*) AS to_delete FROM skills
WHERE 'plugin:knowledge-work-plugins' = ANY(tags);

SELECT slug FROM skills
WHERE 'plugin:knowledge-work-plugins' = ANY(tags)
ORDER BY slug
LIMIT 10;

-- ── Delete ─────────────────────────────────────────────────────────────────
DELETE FROM skills
WHERE 'plugin:knowledge-work-plugins' = ANY(tags);

-- ── Verify ─────────────────────────────────────────────────────────────────
SELECT count(*) AS remaining_skills FROM skills;

-- Tag breakdown of what's left
SELECT unnest(tags) AS tag, count(*) AS n
FROM skills
GROUP BY 1
ORDER BY 2 DESC;
