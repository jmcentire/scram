# scram — Emergency Kill-Switch with Rollback

## Charter

Provide an immediate, unconditional termination path for catastrophic
failure scenarios. When something goes catastrophically wrong — an
agent attempts a destructive operation outside its envelope, a tenant's
data is at imminent risk of cross-contamination, an automated cascade
is in progress that no other component can stop — scram fires. It does
not negotiate. It does not retry. It terminates and rolls back.

The name comes from nuclear-reactor terminology: SCRAM is the emergency
shutdown control. NASA has the same primitive under different names
(range safety, abort modes, command authority lockout). The user's
existing `sentinel` component is taken (production attribution via PACT
keys), so this primitive needs a distinct identifier.

scram is a TRUE last-resort. The decision tree before scram fires:
1. Could a slice-5 canary threshold catch it? → use that.
2. Could a slice-6 alarm response handler catch it? → use that.
3. Could a witness operator-pause catch it? → use that.
4. Could degraded-mode (slice 0.5) catch it? → use that.
5. None of the above, AND damage continues without intervention → scram.

If scram is firing weekly, the architecture upstream is wrong. scram
should be measured in fires per quarter, not per week.

## Interface (proposed)

```typescript
export type KillCondition = {
  id: string;                            // stable, e.g., 'cross-tenant-data-leak'
  description: string;                   // human-readable for runbooks
  predicate: (state: SystemState) => boolean | Promise<boolean>;
  // What scram does when this fires:
  action:
    | { kind: 'process-exit'; code: number }
    | { kind: 'rollback-to-checkpoint'; checkpointId: string }
    | { kind: 'circuit-break-component'; component: string }
    | { kind: 'tenant-quarantine'; tenantId: string }
    | { kind: 'global-readonly' };
  // Required: who to notify and how loud.
  escalation: {
    pagerDutyServiceId?: string;
    operatorEmail?: string;
    witnessOverride?: boolean;
  };
};

export interface Scram {
  registerCondition(c: KillCondition): void;
  // Called periodically by a worker; predicates evaluated on each tick.
  check(): Promise<TriggeredCondition[]>;
  // Manual trigger (operator-initiated).
  fire(conditionId: string, reason: string, operator: string): Promise<void>;
  // Inspection: who's registered what?
  listConditions(): readonly KillCondition[];
}
```

## Composition with the rest of the stack

- **baton** — receives the scram-fired event and routes traffic away
  from affected components.
- **tessera** — every scram fire writes an immutable audit entry with
  the predicate, the state snapshot at fire-time, and the operator
  invocation context.
- **witness** — a manual scram requires operator approval AND is
  reviewable post-hoc by another operator (two-person rule for any
  non-automatic scram action that affects more than one tenant).
- **chronicler** — narrates scram events into the operator timeline
  with full context.

## What scram does NOT do

- Cleanup. After a process-exit scram, the next instance starting up
  reads the shutdown manifest (slice 2.5) and decides what to recover.
- Decide policy. Predicates and actions are declared by the components
  that own the relevant invariants. scram is the runtime — it doesn't
  decide whether cross-tenant data leakage is a kill condition; the
  data-classification component (ledger) does.
- Restart. Restart is a baton concern. scram terminates; baton brings
  things back.

## Two-person rule

Some kill actions affect more than one tenant or have irreversible
effects (global-readonly, mass tenant-quarantine). For those, scram
requires two distinct operator invocations within a short window
before the action fires. The mechanism uses witness for both
invocations and tessera to record both.

For automatic predicates, the architectural review IS the
"two-person." Predicate registration requires PR review by an operator
on the security team (codeowner enforcement at the registration site).

## Stack consumers

scram is invoked-by, not consumed-by, every other component that owns
an invariant catastrophic enough to register a kill condition. Initial
expected registrants:

- **reeve** — cross-tenant lateral movement detected → quarantine
  tenant. Audit-chain integrity break → global-readonly.
- **ledger** — classified field appearing in non-classified path →
  circuit-break the offending component.
- **baton** — cascading-failure detected (multiple components going
  unhealthy in sequence) → process-exit affected hosts.
- **sentinel** — PACT-key forgery detected → quarantine the source.

## Open questions

1. How does scram coexist with degraded-mode (slice 0.5)? Lean:
   degraded-mode is the gradient between healthy and scram; scram is
   the cliff. State machine: `healthy → degraded → scram`.
2. Should scram support a "dry-run" mode where predicates fire but
   actions don't, just to validate the predicate? Lean: yes — every
   condition has a `live: boolean` flag; ops promote conditions from
   dry-run to live after observing them in production for N days.
3. How does scram integrate with Fly's process model? Lean: scram's
   `process-exit` action returns a specific exit code Fly's restart
   policy treats as "do not restart" (preventing a scram loop).

## Initial implementation plan

1. Spec lock: this doc.
2. First conditions live in reeve's source: cross-tenant lateral
   movement, audit-chain integrity break.
3. Implementation at `reeve/src/observability/scram/` until a second
   stack component registers a condition.

## Provenance

Spec'd 2026-05-05 from sim's NASA-bar review. Sim originally proposed
"Sentinel" as the name; the user's stack already uses Sentinel for
PACT-key production attribution, so the name was changed to scram
(reactor terminology, semantically distinct).
