"""Action dispatchers — V1 stubs with explicit V2 integration plan.

Per ADR-001, every dispatcher in V1 logs and records to
``kill_fires.dispatch_result``; the actual cross-stack effects (Baton
adapter control, stack-shared global-readonly control plane) land in
V2. This file is intentionally honest about that boundary: each
dispatcher's docstring names the V2 endpoint it will eventually call.

Trust model (sim-vetted): scram has NO privileged authority. When V2
lands, every dispatcher will POST to the target component's existing
internal API (idempotent, validated, logged) over the Fly private
network. There are no RPC tokens; the security boundary is network
topology. A compromised scram has only the reach of any other internal
service.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from .types import KillCondition, TriggerResult

logger = logging.getLogger("scram.dispatch")

# Dispatchers return a short string describing the outcome; the caller
# stores it in ``kill_fires.dispatch_result``. The caller also flips
# ``dispatched=true`` in the DB on a non-exception return.
Dispatcher = Callable[[KillCondition, str], Awaitable[str]]


# ----------------------------------------------------------------------
# Stub implementations (V1)
# ----------------------------------------------------------------------


async def dispatch_process_exit(condition: KillCondition, reason: str) -> str:
    """STUB. V2: POST to Baton's adapter control to drain+restart the
    target adapter. Reeve's process-exit code follows slice 2.5's
    graceful-shutdown protocol on receiving SIGTERM.

    Stub behavior: log + return descriptor. No process is actually
    exited; that would kill the scram service itself.
    """
    code = condition.action.config.get("code", 137)
    logger.warning(
        "dispatch[stub] process-exit: condition=%s reason=%s exit_code=%s",
        condition.id,
        reason,
        code,
    )
    return f"stub:process-exit code={code}"


async def dispatch_rollback_to_checkpoint(
    condition: KillCondition, reason: str
) -> str:
    """STUB. V2: POST to Baton's canary control to roll back current
    canary and pin to last-known-good. Reeve's slice-5 thresholds are
    advisory; scram's rollback is definitive."""
    checkpoint_id = condition.action.config.get("checkpoint_id")
    logger.warning(
        "dispatch[stub] rollback: condition=%s reason=%s checkpoint=%s",
        condition.id,
        reason,
        checkpoint_id,
    )
    return f"stub:rollback checkpoint_id={checkpoint_id}"


async def dispatch_circuit_break(condition: KillCondition, reason: str) -> str:
    """STUB. V2: POST to Baton "all adapters for component X return 503
    until manual unbreak." """
    component = condition.action.config.get("component")
    logger.warning(
        "dispatch[stub] circuit-break: condition=%s reason=%s component=%s",
        condition.id,
        reason,
        component,
    )
    return f"stub:circuit-break component={component}"


async def dispatch_tenant_quarantine(
    condition: KillCondition, reason: str
) -> str:
    """STUB. V2: POST to Baton "all traffic for tenant X returns 503
    until manual unquarantine." Tenant-scoped — does NOT require
    two-person."""
    tenant_id = condition.action.config.get("tenant_id")
    logger.warning(
        "dispatch[stub] tenant-quarantine: condition=%s reason=%s tenant=%s",
        condition.id,
        reason,
        tenant_id,
    )
    return f"stub:tenant-quarantine tenant={tenant_id}"


async def dispatch_global_readonly(condition: KillCondition, reason: str) -> str:
    """STUB. V2: POST to a stack-shared control plane that flips all
    components to read-only. Requires two-person rule (enforced by the
    caller; this dispatcher is reached only after approval)."""
    logger.error(
        "dispatch[stub] global-readonly: condition=%s reason=%s",
        condition.id,
        reason,
    )
    return "stub:global-readonly"


# ----------------------------------------------------------------------
# Registry of dispatchers by action kind
# ----------------------------------------------------------------------


_DEFAULT_DISPATCHERS: dict[str, Dispatcher] = {
    "process-exit": dispatch_process_exit,
    "rollback-to-checkpoint": dispatch_rollback_to_checkpoint,
    "circuit-break-component": dispatch_circuit_break,
    "tenant-quarantine": dispatch_tenant_quarantine,
    "global-readonly": dispatch_global_readonly,
}


class DispatchRouter:
    """Maps ``KillAction.kind`` to a dispatcher callable.

    Tests inject custom dispatchers via ``override``; production wiring
    happens in :func:`scram.api.create_app`. Override is per-instance,
    so two routers can coexist (e.g., test fixture vs. live service).
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Dispatcher] = dict(_DEFAULT_DISPATCHERS)

    def override(self, kind: str, handler: Dispatcher) -> None:
        if kind not in _DEFAULT_DISPATCHERS:
            raise ValueError(f"unknown action kind: {kind}")
        self._handlers[kind] = handler

    async def dispatch(
        self, condition: KillCondition, reason: str
    ) -> TriggerResult:
        """Call the matching dispatcher and return a TriggerResult.

        Note: this method does NOT write to ``kill_fires`` — that's the
        evaluator's job. The router is purely the action effector.
        """
        handler = self._handlers.get(condition.action.kind)
        if handler is None:
            return TriggerResult(
                condition_id=condition.id,
                triggered=False,
                fire_id=None,
                dispatched=False,
                error=f"no dispatcher for action kind {condition.action.kind!r}",
            )
        try:
            descriptor = await handler(condition, reason)
        except Exception as e:
            return TriggerResult(
                condition_id=condition.id,
                triggered=True,
                fire_id=None,
                dispatched=False,
                error=f"dispatch error: {e}",
            )
        return TriggerResult(
            condition_id=condition.id,
            triggered=True,
            fire_id=None,
            dispatched=True,
            dispatch_descriptor=descriptor,
            error=None,
        )


def is_action_kind_valid(kind: str) -> bool:
    return kind in _DEFAULT_DISPATCHERS


__all__ = [
    "DispatchRouter",
    "Dispatcher",
    "dispatch_circuit_break",
    "dispatch_global_readonly",
    "dispatch_process_exit",
    "dispatch_rollback_to_checkpoint",
    "dispatch_tenant_quarantine",
    "is_action_kind_valid",
]


# Mirror of valid kinds for callers that don't want to import ActionKind.
def valid_action_kinds() -> list[str]:
    return list(_DEFAULT_DISPATCHERS.keys())
