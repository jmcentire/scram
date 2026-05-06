-- scram migration 001: kill_conditions + kill_fires
--
-- Per ADR-001 (sim-vetted). Conditions register what scram should watch
-- for; fires record each instance of a condition tripping (auto or
-- manual) along with forensic state and dispatch outcome.
--
-- gen_random_uuid() requires pgcrypto.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS kill_conditions (
  id              text PRIMARY KEY,
  description     text NOT NULL,
  -- Action shape: one of 'process-exit' | 'rollback-to-checkpoint' |
  -- 'circuit-break-component' | 'tenant-quarantine' | 'global-readonly'
  action_kind     text NOT NULL,
  action_config   jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Whether this condition fires automatically (predicate-based) or
  -- requires manual scram fire() invocation.
  auto_fire       boolean NOT NULL DEFAULT false,
  -- Two-person required? Set per condition. For non-tenant-scoped
  -- destructive actions (global-readonly, multi-tenant rollback), TRUE.
  requires_two_person boolean NOT NULL DEFAULT false,
  -- Live or dry-run. dry-run mode logs the trigger but doesn't dispatch.
  live            boolean NOT NULL DEFAULT false,
  registered_at   timestamptz NOT NULL DEFAULT now(),
  registered_by   text NOT NULL,
  CHECK (action_kind IN (
    'process-exit',
    'rollback-to-checkpoint',
    'circuit-break-component',
    'tenant-quarantine',
    'global-readonly'
  ))
);

CREATE TABLE IF NOT EXISTS kill_fires (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  condition_id    text NOT NULL REFERENCES kill_conditions(id),
  fired_at        timestamptz NOT NULL DEFAULT now(),
  fired_by        text NOT NULL,        -- 'auto' or operator id
  reason          text NOT NULL,
  state_snapshot  jsonb NOT NULL DEFAULT '{}'::jsonb,
  second_operator text,                 -- for two-person rule
  second_at       timestamptz,
  dispatched      boolean NOT NULL DEFAULT false,
  dispatch_result text,                 -- output of action dispatch
  -- Tracks two-person state explicitly (clearer than nullable second_*).
  two_person_status text NOT NULL DEFAULT 'not-required',
  CHECK (two_person_status IN ('not-required', 'pending', 'approved', 'rejected', 'timed-out'))
);

CREATE INDEX IF NOT EXISTS kill_fires_condition_id_idx ON kill_fires (condition_id);
CREATE INDEX IF NOT EXISTS kill_fires_fired_at_idx ON kill_fires (fired_at DESC);
CREATE INDEX IF NOT EXISTS kill_conditions_live_auto_idx
  ON kill_conditions (live, auto_fire) WHERE live = true AND auto_fire = true;
