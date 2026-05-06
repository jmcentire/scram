# scram — emergency kill-switch service

scram is the exemplar stack's last-resort termination mechanism. When
something catastrophic is in progress that no slice-5 canary, slice-6
alarm, slice-0.5 degraded-mode, or witness operator intervention can
stop, scram fires. It does not negotiate. It does not retry. It
terminates and rolls back.

Named after nuclear-reactor terminology: SCRAM is the emergency
shutdown control. NASA has the same primitive under different names
(range safety, abort modes, command authority lockout).

See `SPEC.md` for the charter and `ADR-001-extraction.md` for the
sim-vetted architecture lock.

## Status

V1. Service is functional end-to-end (registry, evaluator, FastAPI,
CLI, two-person handshake) but the cross-stack action effects are
**stubs**:

| Component             | V1 status |
|-----------------------|-----------|
| Registry              | real (in-memory + Postgres) |
| Evaluator + budgets   | real (asyncio.wait_for, hand-rolled aegis pattern) |
| API + auth            | real (FastAPI + bearer token) |
| CLI                   | real (`run`, `fire`, `list`, `migrate`) |
| Boot-only env-var override | real (`SCRAM_FORCE_READONLY=1`) |
| `process-exit` dispatcher       | **stub** (V2: Baton adapter control POST) |
| `rollback-to-checkpoint` dispatcher | **stub** (V2: Baton canary control POST) |
| `circuit-break-component` dispatcher | **stub** (V2: Baton component circuit POST) |
| `tenant-quarantine` dispatcher  | **stub** (V2: Baton tenant traffic POST) |
| `global-readonly` dispatcher    | **stub** (V2: stack-shared control plane POST) |
| Witness two-person client       | **stub** (V2: witness HTTP `ask` integration) |
| Reeve predicate registration    | **stub** (Reeve is TS; predicate eval-from-HTTP is V2; ADR-002) |

V1 is intentionally honest about these boundaries. Every dispatcher
logs its intent and writes the descriptor to
`kill_fires.dispatch_result`; a smoke test against a real Postgres
exercises the full lifecycle minus the cross-stack call.

## Deploy

```bash
# Local dev
pip install -e .[dev]
createdb scram_dev
DATABASE_URL=postgres://localhost/scram_dev scram migrate
DATABASE_URL=postgres://localhost/scram_dev SCRAM_API_TOKEN=dev-token scram run

# Health check
curl http://127.0.0.1:8400/v1/health

# Fly
fly secrets set DATABASE_URL=... SCRAM_API_TOKEN=... -a scram
fly deploy
fly ssh console -a scram -C "scram migrate"
```

## Integration points

### From a registrant component (Python)

```python
from scram.registry import Registry
from scram.types import KillAction, KillCondition

registry = Registry()
registry.register(KillCondition(
    id="my-cascading-failure-detector",
    description="three or more components unhealthy in 60s",
    action=KillAction(kind="process-exit", config={"code": 137}),
    auto_fire=True,
    requires_two_person=False,
    live=False,                          # promote to True after observation
    registered_by="baton",
    predicate=cascading_failure_predicate,
))
```

The registrant is responsible for the predicate. scram has no view
into what the predicate inspects; it just calls it on every tick.

### From a TS registrant (Reeve)

Reeve cannot register Python callables directly. V1 ships a
predicate-stub at `src/scram/reeve_conditions.py` that documents the
three conditions Reeve registers. Until ADR-002 lands (HTTP
trigger-URL predicates), Reeve registers metadata via
`POST /v1/conditions` and a separate Python harness deployed alongside
Reeve injects the real predicates. See `docs/kill-condition-catalog.md`.

### Boot-only env-var override

Every component imports this at startup:

```python
from scram.force_readonly import is_force_readonly_enabled

if is_force_readonly_enabled():
    app.read_only = True
```

**This is independent of scram itself.** If scram is down and an
operator pulls the lever via `fly secrets set SCRAM_FORCE_READONLY=1`,
the next boot of every component flips to read-only. Boot-only by
design — see `docs/force-readonly.md` for why.

### Two-person rule

Conditions with `requires_two_person=true` route through witness
before dispatch. V1 stubs the witness call (logs + returns `pending`,
which blocks dispatch safely). See `docs/two-person-policy.md` for the
V2 wiring plan.

## API

| Endpoint                          | Method | Auth | Purpose |
|-----------------------------------|--------|------|---------|
| `/v1/health`                      | GET    | none | Liveness for Baton's adapter polling |
| `/v1/conditions`                  | GET    | none | List all registered conditions |
| `/v1/conditions`                  | POST   | bearer | Register a condition (metadata only; predicates are local) |
| `/v1/conditions/{id}`             | DELETE | bearer | Remove a condition (in memory + DB) |
| `/v1/fire`                        | POST   | bearer | Manually fire a registered condition |
| `/v1/fires`                       | GET    | none | Recent kill_fires for ops dashboards |

Auth is a bearer token via `SCRAM_API_TOKEN`. The token is a
defense-in-depth measure; the primary boundary is the Fly private
network.

## CLI

```bash
scram run                    # start evaluator + API
scram migrate                # apply DB migrations
scram list                   # list registered conditions
scram fire <id> --reason "..." --operator "..."  # manual fire (HTTP)
```

## Schema

Two tables, single migration (`migrations/001_kill_conditions.sql`):

- `kill_conditions` — registered metadata (predicate stays in process
  memory; not persisted)
- `kill_fires` — every fire (auto or manual) with forensic state +
  dispatch result + two-person status

## Tests

```bash
pytest                # unit tests (no DB required)
```

The integration-test path (real Postgres) is NOT yet wired in this
repo's test runner — V1 keeps the test suite hermetic so `pytest` runs
without docker. Production verification today relies on the smoke
tests in this repo plus the live `scram run` against a dev Postgres.

## Open questions / V2 work

- ADR-002: HTTP trigger-URL predicates so TypeScript components can
  register kill conditions. Until then, Reeve uses a Python harness to
  bridge.
- Real Baton dispatcher integration (replace the five stubs in
  `dispatch.py`).
- Real witness integration (replace `StubWitnessClient` in
  `two_person.py`).
- Operator UI for inspecting `kill_fires` (separate concern; reeve
  operator console is the likely host).
- Predicate-eval cost monitoring: emit per-condition tick latency to
  observability so a slow predicate is visible before it stalls
  evaluation.
