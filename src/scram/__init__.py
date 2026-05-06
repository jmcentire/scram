"""scram — emergency kill-switch service.

Public surface: register kill conditions, manually fire them, and run the
periodic evaluator. Wave 1 dispatchers are STUBS (Baton + witness +
stack-shared control plane integrations are V2). The boot-only
SCRAM_FORCE_READONLY env-var override is independent of this service —
see :mod:`scram.force_readonly` and ``docs/force-readonly.md``.

Critical architectural decisions (sim-vetted, ADR-001):

- Predicates are LOCAL CALLABLES registered via ``Registry.register``;
  there is no remote-HTTP predicate-eval path in V1.
- ``SCRAM_FORCE_READONLY=1`` is BOOT-ONLY at every component. Mid-runtime
  flip would invert the failsafe — never poll for it.
- scram has NO privileged authority. Components opt in by registering
  predicates; dispatch calls each component's existing internal API.
"""

from .force_readonly import is_force_readonly_enabled
from .registry import Registry
from .types import (
    ActionKind,
    KillAction,
    KillCondition,
    KillFire,
    TriggerResult,
)

__all__ = [
    "ActionKind",
    "KillAction",
    "KillCondition",
    "KillFire",
    "Registry",
    "TriggerResult",
    "is_force_readonly_enabled",
]
