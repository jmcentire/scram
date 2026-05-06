# Kill condition catalog

Index of conditions registered (or planned) across the exemplar stack.
Each entry lists the registrant component, the action kind, two-person
required (yes/no), and the V1 status (stub vs real predicate).

This document is human-edited — it is NOT generated from
`kill_conditions` rows. The catalog tracks intent; the table tracks
runtime state.

## Reeve

| id                                  | action                  | two-person | V1 status |
|-------------------------------------|-------------------------|------------|-----------|
| `cross-tenant-lateral-movement`     | tenant-quarantine       | no         | predicate stub (TS-side detector exists; ADR-002 needed for Python wiring) |
| `audit-chain-integrity-break`       | global-readonly         | yes        | predicate stub (same as above) |
| `emergency-readonly-on-pg-down`     | global-readonly         | yes        | predicate stub (same as above) |

See `src/scram/reeve_conditions.py` for the in-code definitions Reeve
registers at startup. All three default to `live=false` (dry-run) until
ops promotes them.

## Baton (planned, not yet shipped)

| id                                  | action                  | two-person | V1 status |
|-------------------------------------|-------------------------|------------|-----------|
| `cascading-failure-detected`        | process-exit            | no         | not registered |

## Ledger (planned)

| id                                                    | action                    | two-person | V1 status |
|-------------------------------------------------------|---------------------------|------------|-----------|
| `classified-field-in-non-classified-path`             | circuit-break-component   | no         | not registered |

## Sentinel (PACT keys; planned)

| id                              | action                | two-person | V1 status |
|---------------------------------|-----------------------|------------|-----------|
| `pact-key-forgery-detected`     | tenant-quarantine     | no         | not registered |

## Adding a new condition

1. Open a PR adding the entry to this catalog and a row to the
   appropriate component's startup registration code.
2. Codeowner review enforces the architectural "two-person" for
   automatic predicates: predicate registration MUST go through PR
   review by the security-codeowner team for the registering
   component.
3. Default `live=false` on first deploy. Promote to `live=true` only
   after observing the condition fire correctly in production for at
   least N days (component team's call; document in the PR).

## Action kind reference

| kind                       | tenant-scoped | typically two-person? | dispatcher V1 status |
|----------------------------|---------------|-----------------------|----------------------|
| `process-exit`             | host          | no                    | stub |
| `rollback-to-checkpoint`   | depends       | depends               | stub |
| `circuit-break-component`  | component     | no                    | stub |
| `tenant-quarantine`        | tenant        | no                    | stub |
| `global-readonly`          | cluster       | YES                   | stub |
