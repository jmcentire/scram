# `SCRAM_FORCE_READONLY` — out-of-band emergency override

The env var `SCRAM_FORCE_READONLY=1` (set as a Fly secret in
production) is the **out-of-band** emergency brake for the entire
exemplar stack. Every component reads it ONCE at boot. When set,
components flip to read-only mode regardless of scram's runtime state.

This is the "operator pulled the lever physically" case. It does NOT
depend on scram being reachable, healthy, or even running.

## CRITICAL: BOOT-ONLY

**The env var is read once at module import and cached.** Mid-runtime
flip is intentionally NOT supported.

### Why boot-only

Mid-runtime polling would invert the failsafe:
- Polling means the read-only flag depends on **working code in the
  running process** — exactly the failure mode we are trying to protect
  against.
- A wedged or corrupted process won't see the flip.
- A process under attack might be running malicious code that ignores
  the poll.

Boot-only means "the next thing that starts obeys the rule." That is
what an operator actually wants when pulling the emergency brake.

### How to make it propagate fast

1. Set the secret on every Fly app:
   ```
   fly secrets set SCRAM_FORCE_READONLY=1 -a reeve
   fly secrets set SCRAM_FORCE_READONLY=1 -a baton
   fly secrets set SCRAM_FORCE_READONLY=1 -a apprentice
   # ... every component in the stack
   ```
2. Let scram's `process-exit` action restart instances. Each new
   instance picks up the env var on boot.
3. Or, if scram itself is down/unreachable, manually trigger a
   restart on each app:
   ```
   fly apps restart reeve
   ```

## Reference implementation

Every component imports this on startup:

```python
from scram.force_readonly import is_force_readonly_enabled

if is_force_readonly_enabled():
    app.read_only = True
    logger.error("SCRAM_FORCE_READONLY=1 — running read-only")
```

For TypeScript components (Reeve, etc.), the equivalent is:

```typescript
const SCRAM_FORCE_READONLY = process.env.SCRAM_FORCE_READONLY === '1';

if (SCRAM_FORCE_READONLY) {
  app.readOnly = true;
  logger.error('SCRAM_FORCE_READONLY=1 — running read-only');
}
```

The TS check is also boot-only by convention. Do NOT poll
`process.env.SCRAM_FORCE_READONLY` mid-runtime.

## Independence from scram service

The env var is intentionally a **separate authority layer** from the
scram runtime:

| Layer | When it works | Failure mode |
|-------|---------------|--------------|
| `SCRAM_FORCE_READONLY=1` | always (env var, boot-time) | requires restarts to propagate |
| scram service `global-readonly` action | when scram is running and healthy | scram must be reachable; component dispatchers must be implemented |

If scram is down AND a human operator decides "stop everything," they
set the secret and let boots cycle. No coordination with the scram
process is required.

## Do NOT poll

If you find yourself writing code that re-reads `SCRAM_FORCE_READONLY`
mid-runtime, **STOP**. That is the inverse of what this primitive is
for. Use scram's runtime registry instead, or accept the architectural
constraint that emergency-brake takes a restart to propagate.

## Test contract

`scram.force_readonly._recompute_for_tests()` exists for unit tests
only. Production code MUST NOT call it. The presence of that helper is
a deliberate test-vs-prod contract: if a piece of production code wants
to call it, the design is wrong.
