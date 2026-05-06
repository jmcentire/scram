"""Dispatch tests — every action kind has a working V1 stub."""

from __future__ import annotations

import pytest

from scram.dispatch import (
    DispatchRouter,
    dispatch_circuit_break,
    dispatch_global_readonly,
    dispatch_process_exit,
    dispatch_rollback_to_checkpoint,
    dispatch_tenant_quarantine,
    is_action_kind_valid,
    valid_action_kinds,
)
from scram.types import KillAction, KillCondition


def _cond(kind: str, config: dict | None = None) -> KillCondition:
    return KillCondition(
        id=f"c-{kind}",
        description="test",
        action=KillAction(kind=kind, config=config or {}),
        auto_fire=False,
        live=False,
        requires_two_person=False,
        registered_by="test",
        predicate=None,
    )


async def test_dispatch_process_exit_returns_descriptor() -> None:
    cond = _cond("process-exit", {"code": 137})
    desc = await dispatch_process_exit(cond, "test reason")
    assert "stub" in desc
    assert "137" in desc


async def test_dispatch_rollback_returns_descriptor() -> None:
    cond = _cond("rollback-to-checkpoint", {"checkpoint_id": "ck-42"})
    desc = await dispatch_rollback_to_checkpoint(cond, "test reason")
    assert "ck-42" in desc


async def test_dispatch_circuit_break_returns_descriptor() -> None:
    cond = _cond("circuit-break-component", {"component": "reeve-adapter"})
    desc = await dispatch_circuit_break(cond, "test reason")
    assert "reeve-adapter" in desc


async def test_dispatch_tenant_quarantine_returns_descriptor() -> None:
    cond = _cond("tenant-quarantine", {"tenant_id": "t-9000"})
    desc = await dispatch_tenant_quarantine(cond, "test reason")
    assert "t-9000" in desc


async def test_dispatch_global_readonly_returns_descriptor() -> None:
    cond = _cond("global-readonly")
    desc = await dispatch_global_readonly(cond, "test reason")
    assert "global-readonly" in desc


# ----------------------------------------------------------------------
# Router-level tests
# ----------------------------------------------------------------------


async def test_router_dispatches_to_correct_handler() -> None:
    router = DispatchRouter()
    cond = _cond("tenant-quarantine", {"tenant_id": "t1"})
    result = await router.dispatch(cond, "test")
    assert result.dispatched is True
    assert result.dispatch_descriptor is not None
    assert "tenant-quarantine" in result.dispatch_descriptor


async def test_router_override_replaces_handler() -> None:
    captured: list[str] = []

    async def custom(condition, reason: str) -> str:
        captured.append(condition.id)
        return f"custom:{condition.id}"

    router = DispatchRouter()
    router.override("tenant-quarantine", custom)
    cond = _cond("tenant-quarantine", {"tenant_id": "t1"})
    result = await router.dispatch(cond, "test")
    assert captured == [cond.id]
    assert result.dispatch_descriptor == f"custom:{cond.id}"


def test_router_override_rejects_unknown_kind() -> None:
    router = DispatchRouter()

    async def noop(condition, reason: str) -> str:
        return "ok"

    with pytest.raises(ValueError, match="unknown action kind"):
        router.override("not-a-real-kind", noop)


async def test_router_handler_exception_returned_as_error() -> None:
    async def bad(condition, reason: str) -> str:
        raise RuntimeError("kaboom")

    router = DispatchRouter()
    router.override("global-readonly", bad)
    cond = _cond("global-readonly")
    result = await router.dispatch(cond, "test")
    assert result.dispatched is False
    assert "kaboom" in (result.error or "")


def test_is_action_kind_valid() -> None:
    assert is_action_kind_valid("tenant-quarantine") is True
    assert is_action_kind_valid("global-readonly") is True
    assert is_action_kind_valid("nope") is False


def test_valid_action_kinds_returns_all_five() -> None:
    kinds = valid_action_kinds()
    assert set(kinds) == {
        "process-exit",
        "rollback-to-checkpoint",
        "circuit-break-component",
        "tenant-quarantine",
        "global-readonly",
    }
