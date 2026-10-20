-- The agent's footprint in the dashboard's summary rows: how a handoff came about (forced by code, or chosen by the
-- orchestrator after trying), how the turn ran (agent mode), how many moves the agent made, and whether it gave up.
-- New columns come last so the older reads of turn_facts keep their positions.

ALTER TABLE turn_facts ADD COLUMN escalation_kind TEXT;
ALTER TABLE turn_facts ADD COLUMN agent_mode      TEXT;
ALTER TABLE turn_facts ADD COLUMN agent_moves     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE turn_facts ADD COLUMN agent_fallback  INTEGER NOT NULL DEFAULT 0;
