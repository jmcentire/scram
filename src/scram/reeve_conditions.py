"""Reeve's registered kill conditions.

Returns a list of :class:`KillCondition` instances Reeve registers with
scram at startup. The actual predicate callables are STUBS at this
layer because Reeve is TypeScript and cannot register Python callables
directly. The intended Wave 2 / ADR-002 plan:

1. Add an HTTP register endpoint to scram that accepts a "trigger URL"
   instead of a Python callable. The evaluator POSTs to that URL and
   treats the response as the boolean.
2. Reeve hosts the trigger URLs as small internal endpoints
   (``/internal/scram/trigger/<id>``) bound to existing Reeve
   detectors (cross-tenant lateral movement, audit-chain integrity,
   pg-down).
3. scram uses Fly's private network for the call; auth via mTLS or a
   shared bearer token (TBD in ADR-002).

V1 STATUS: this module documents the three condition shapes Reeve
intends to register; the predicates are no-op stubs that always return
False. A separate Python harness (running alongside Reeve in the same
deploy) can import these conditions and inject real predicates wired
to Reeve's HTTP endpoints — that's the bridge until ADR-002 lands.

Why ship these stubs now: Reeve's TS entrypoint needs SOMETHING to
register so the kill_conditions table has rows for ops dashboards. The
predicates being stubs is an honest representation of the V1 gap.
"""

from __future__ import annotations

from .types import KillAction, KillCondition


async def _stub_predicate_always_false() -> bool:
    """V1 stub. Real predicate lives in Reeve (TS); see ADR-002.

    Always returning False means the condition is registered for ops
    visibility but will never auto-fire from this Python harness. Manual
    fire via ``POST /v1/fire`` still works.
    """
    return False


def reeve_kill_conditions(*, registered_by: str = "reeve") -> list[KillCondition]:
    """Return the three conditions Reeve registers at startup.

    Conditions:

    - ``cross-tenant-lateral-movement`` — auto-fire, tenant-quarantine
      action, two-person FALSE (tenant-scoped).
    - ``audit-chain-integrity-break`` — auto-fire, global-readonly,
      two-person TRUE (cluster-wide destructive).
    - ``emergency-readonly-on-pg-down`` — auto-fire, global-readonly,
      two-person TRUE.

    All three are returned with ``live=False`` so they default to
    dry-run; ops promotes them to ``live=True`` after observing them
    behave correctly in production.
    """
    return [
        KillCondition(
            id="cross-tenant-lateral-movement",
            description=(
                "Detected a query in tenant A's session that touched "
                "tenant B's data. Quarantine A pending forensic review."
            ),
            action=KillAction(kind="tenant-quarantine", config={}),
            auto_fire=True,
            requires_two_person=False,
            live=False,
            registered_by=registered_by,
            predicate=_stub_predicate_always_false,
        ),
        KillCondition(
            id="audit-chain-integrity-break",
            description=(
                "tessera audit-chain hash chain broke. Refuse all writes "
                "until forensic review confirms append-only invariant holds."
            ),
            action=KillAction(kind="global-readonly", config={}),
            auto_fire=True,
            requires_two_person=True,
            live=False,
            registered_by=registered_by,
            predicate=_stub_predicate_always_false,
        ),
        KillCondition(
            id="emergency-readonly-on-pg-down",
            description=(
                "Postgres primary unreachable for > N seconds. Flip stack "
                "to read-only until manual recovery."
            ),
            action=KillAction(kind="global-readonly", config={}),
            auto_fire=True,
            requires_two_person=True,
            live=False,
            registered_by=registered_by,
            predicate=_stub_predicate_always_false,
        ),
    ]


__all__ = ["reeve_kill_conditions"]
