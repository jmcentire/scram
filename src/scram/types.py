"""scram core types.

The shape of a kill condition, the action it dispatches, and the outcome
of evaluation. Pydantic-validated where they cross trust boundaries
(e.g., the API), plain dataclasses where they don't.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

ActionKind = Literal[
    "process-exit",
    "rollback-to-checkpoint",
    "circuit-break-component",
    "tenant-quarantine",
    "global-readonly",
]

# A predicate is any sync-or-async callable returning bool. The evaluator
# wraps each call in a per-condition timeout (aegis-style budget).
Predicate = Callable[[], bool] | Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class KillAction:
    """What scram does when a condition fires.

    ``kind`` selects the dispatcher; ``config`` carries kind-specific
    arguments (e.g., ``{"code": 137}`` for process-exit, ``{"tenant_id":
    "..."}`` for tenant-quarantine). Validated on registration.
    """

    kind: ActionKind
    config: dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {"kind": self.kind, "config": self.config}


@dataclass
class KillCondition:
    """A registered kill condition.

    ``predicate`` is a local Python callable; sim REJECTED remote-HTTP
    predicate eval for V1 (see ADR-001). It MAY be ``None`` for
    manual-only conditions (predicate evaluation is skipped; only the
    ``POST /v1/fire`` path triggers them).
    """

    id: str
    description: str
    action: KillAction
    auto_fire: bool = False
    requires_two_person: bool = False
    live: bool = False
    registered_by: str = "unknown"
    predicate: Predicate | None = None
    registered_at: datetime | None = None

    def to_row(self) -> dict[str, Any]:
        """Project to row shape for kill_conditions persistence."""
        return {
            "id": self.id,
            "description": self.description,
            "action_kind": self.action.kind,
            "action_config": self.action.config,
            "auto_fire": self.auto_fire,
            "requires_two_person": self.requires_two_person,
            "live": self.live,
            "registered_by": self.registered_by,
        }


@dataclass(frozen=True)
class KillFire:
    """A recorded firing of a kill condition.

    ``two_person_status`` tracks the witness handshake explicitly:
    ``not-required`` for tenant-scoped or non-destructive actions;
    ``pending``/``approved``/``rejected``/``timed-out`` for those that
    require two-person.
    """

    id: UUID
    condition_id: str
    fired_at: datetime
    fired_by: str  # "auto" or operator id
    reason: str
    state_snapshot: dict[str, Any]
    dispatched: bool
    dispatch_result: str | None
    two_person_status: Literal["not-required", "pending", "approved", "rejected", "timed-out"]
    second_operator: str | None = None
    second_at: datetime | None = None


@dataclass(frozen=True)
class TriggerResult:
    """Outcome of one evaluation pass for a condition.

    ``triggered`` says the predicate returned True; ``fire_id`` is the
    UUID of the recorded ``kill_fires`` row when one was written;
    ``dispatched`` is whether the action actually ran (False if blocked
    on two-person or in dry-run); ``dispatch_descriptor`` carries the
    short string a dispatcher returned (e.g.,
    ``"stub:tenant-quarantine tenant=t_42"``); ``error`` is set only on
    failures (predicate raise, timeout, dispatcher exception).
    """

    condition_id: str
    triggered: bool
    fire_id: UUID | None
    dispatched: bool
    dispatch_descriptor: str | None = None
    error: str | None = None


__all__ = [
    "ActionKind",
    "KillAction",
    "KillCondition",
    "KillFire",
    "Predicate",
    "TriggerResult",
]
