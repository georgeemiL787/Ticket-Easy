-- More of the agent's footprint in the summary rows: why the loop gave up (a category), and how often the reviewer
-- stopped a reply or a write. New columns come last.

ALTER TABLE turn_facts ADD COLUMN agent_fallback_reason TEXT;
ALTER TABLE turn_facts ADD COLUMN reviewer_blocked_replies INTEGER NOT NULL DEFAULT 0;
ALTER TABLE turn_facts ADD COLUMN reviewer_blocked_actions INTEGER NOT NULL DEFAULT 0;
