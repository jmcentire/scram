"""Boot-only check for the ``SCRAM_FORCE_READONLY`` env-var override.

The env-var is read ONCE at module import and cached; mid-runtime flip
is intentionally NOT supported. Mid-runtime polling would mean the
read-only flag depends on working code in a process that may already be
wedged or corrupted — exactly the failure mode we are trying to protect
against. Boot-only means "the next thing that starts obeys the rule,"
which is what an operator actually wants when pulling the emergency
brake.

Every component in the stack (Reeve, Baton, Apprentice, Chronicler,
etc.) imports this at startup::

    from scram.force_readonly import is_force_readonly_enabled

    if is_force_readonly_enabled():
        app.read_only = True
        logger.error("SCRAM_FORCE_READONLY=1 — running read-only")

For faster propagation across a running fleet, use scram's
``process-exit`` action: it forces a restart, which picks up the env-var
on boot.

This module is intentionally tiny and dependency-free so importing it
adds zero risk to component boot paths.
"""

from __future__ import annotations

import os

# Cached at import. Tests use ``_recompute_for_tests`` to flip the cache
# so they can verify the env-var contract without re-importing the
# module across test cases.
_FORCE_READONLY: bool = os.environ.get("SCRAM_FORCE_READONLY") == "1"


def is_force_readonly_enabled() -> bool:
    """Return True iff ``SCRAM_FORCE_READONLY=1`` was set at process boot.

    The result is cached at import time. Setting the env-var after this
    module loads has NO effect on the running process, by design.
    """
    return _FORCE_READONLY


def _recompute_for_tests() -> bool:
    """Test-only: re-read the env-var and update the cache.

    Production code MUST NOT call this. It exists solely so unit tests
    can verify the boot-only contract by simulating different env states
    without re-importing the module across test cases.
    """
    global _FORCE_READONLY
    _FORCE_READONLY = os.environ.get("SCRAM_FORCE_READONLY") == "1"
    return _FORCE_READONLY


__all__ = ["is_force_readonly_enabled"]
