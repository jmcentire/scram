"""Evaluator tests — predicate eval, firing, two-person flow, budgets."""

from __future__ import annotations

import asyncio

import pytest

from scram.dispatch import DispatchRouter
from scram.evaluator import (
    Evaluator,
    _call_predicate_with_budget,
    eval_interval_seconds,
)
from scram.registry import Registry
from scram.two_person import StubWitnessClient
from scram.types import KillAction, KillCondition

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _condition(
    cid: str,
    *,
    predicate=None,
    requires_two_person: bool = False,
    action_kind: str = "tenant-quarantine",
    action_config: dict | None = None,
) -> KillCondition:
    return KillCondition(
        id=cid,
        description=f"desc {cid}",
        action=KillAction(kind=action_kind, config=action_config or {}),
        auto_fire=True,
        live=True,
        requires_two_person=requires_two_person,
        registered_by="test",
        predicate=predicate,
    )


# ----------------------------------------------------------------------
# Budget / predicate-call tests
# ----------------------------------------------------------------------


async def test_call_predicate_async_true() -> None:
    async def pred() -> bool:
        return True

    assert await _call_predicate_with_budget(pred, 1.0) is True


async def test_call_predicate_sync_returns_truthy() -> None:
    def pred() -> bool:
        return True

    assert await _call_predicate_with_budget(pred, 1.0) is True


async def test_call_predicate_async_false() -> None:
    async def pred() -> bool:
        return False

    assert await _call_predicate_with_budget(pred, 1.0) is False


async def test_call_predicate_timeout_returns_false() -> None:
    async def hung() -> bool:
        await asyncio.sleep(5.0)
        return True

    # Tight timeout; hung predicate must not return True.
    assert await _call_predicate_with_budget(hung, 0.05) is False


def test_eval_interval_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCRAM_EVAL_INTERVAL_S", raising=False)
    assert eval_interval_seconds() == 5.0


def test_eval_interval_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAM_EVAL_INTERVAL_S", "1.5")
    assert eval_interval_seconds() == 1.5


def test_eval_interval_invalid_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCRAM_EVAL_INTERVAL_S", "not-a-float")
    assert eval_interval_seconds() == 5.0


# ----------------------------------------------------------------------
# evaluate_once / fire flow
# ----------------------------------------------------------------------


async def test_evaluate_once_no_fire_when_predicate_false(fake_pool) -> None:
    async def pred() -> bool:
        return False

    r = Registry()
    r.register(_condition("c1", predicate=pred))
    ev = Evaluator(registry=r, pool=fake_pool)

    results = await ev.evaluate_once()
    assert len(results) == 1
    assert results[0].triggered is False
    assert results[0].fire_id is None
    assert len(fake_pool.store.fires) == 0


async def test_evaluate_once_fires_when_predicate_true(fake_pool) -> None:
    async def pred() -> bool:
        return True

    r = Registry()
    r.register(_condition("c1", predicate=pred))
    ev = Evaluator(registry=r, pool=fake_pool)

    results = await ev.evaluate_once()
    assert len(results) == 1
    assert results[0].triggered is True
    assert results[0].fire_id is not None
    assert results[0].dispatched is True
    # kill_fires row recorded.
    assert len(fake_pool.store.fires) == 1


async def test_evaluate_once_skips_when_predicate_raises(fake_pool) -> None:
    async def pred() -> bool:
        raise RuntimeError("predicate exploded")

    r = Registry()
    r.register(_condition("c1", predicate=pred))
    ev = Evaluator(registry=r, pool=fake_pool)

    results = await ev.evaluate_once()
    assert results[0].triggered is False
    assert results[0].error is not None
    assert "predicate raised" in results[0].error
    assert len(fake_pool.store.fires) == 0


async def test_evaluate_once_treats_timeout_as_false(fake_pool) -> None:
    async def hung() -> bool:
        await asyncio.sleep(2.0)
        return True

    r = Registry()
    r.register(_condition("c1", predicate=hung))
    ev = Evaluator(
        registry=r,
        pool=fake_pool,
        predicate_timeout_s=0.05,
    )

    results = await ev.evaluate_once()
    assert results[0].triggered is False  # timed out → treated as False


async def test_manual_fire_records_and_dispatches(fake_pool) -> None:
    r = Registry()
    cond = _condition("c1", predicate=None)
    cond.auto_fire = False  # manual-only
    r.register(KillCondition(
        id=cond.id,
        description=cond.description,
        action=cond.action,
        auto_fire=False,
        live=cond.live,
        requires_two_person=cond.requires_two_person,
        registered_by=cond.registered_by,
        predicate=None,
    ))
    ev = Evaluator(registry=r, pool=fake_pool)
    cond_in_registry = r.get("c1")
    assert cond_in_registry is not None
    result = await ev.fire(
        cond_in_registry,
        fired_by="op-jane",
        reason="audit dry-run",
    )
    assert result.dispatched is True
    assert result.fire_id is not None
    fire_row = fake_pool.store.fires[result.fire_id]
    assert fire_row["fired_by"] == "op-jane"
    assert fire_row["dispatched"] is True


async def test_two_person_pending_blocks_dispatch(fake_pool) -> None:
    """Default StubWitnessClient returns 'pending'; dispatch must NOT happen."""

    async def pred() -> bool:
        return True

    r = Registry()
    r.register(
        _condition(
            "c1",
            predicate=pred,
            requires_two_person=True,
            action_kind="global-readonly",
        )
    )
    ev = Evaluator(
        registry=r,
        pool=fake_pool,
        witness=StubWitnessClient(default_outcome="pending"),
    )
    results = await ev.evaluate_once()
    assert results[0].triggered is True
    assert results[0].dispatched is False
    assert "pending" in (results[0].dispatch_descriptor or "")
    fire = fake_pool.store.fires[results[0].fire_id]
    assert fire["two_person_status"] == "pending"
    assert fire["dispatched"] is False


async def test_two_person_approved_dispatches(fake_pool) -> None:
    async def pred() -> bool:
        return True

    r = Registry()
    r.register(
        _condition(
            "c1",
            predicate=pred,
            requires_two_person=True,
            action_kind="global-readonly",
        )
    )
    ev = Evaluator(
        registry=r,
        pool=fake_pool,
        witness=StubWitnessClient(
            default_outcome="approved", default_second_operator="op-bob"
        ),
    )
    results = await ev.evaluate_once()
    assert results[0].triggered is True
    assert results[0].dispatched is True
    fire = fake_pool.store.fires[results[0].fire_id]
    assert fire["two_person_status"] == "approved"
    assert fire["second_operator"] == "op-bob"
    assert fire["dispatched"] is True


async def test_two_person_rejected_blocks_dispatch(fake_pool) -> None:
    async def pred() -> bool:
        return True

    r = Registry()
    r.register(
        _condition(
            "c1",
            predicate=pred,
            requires_two_person=True,
            action_kind="global-readonly",
        )
    )
    ev = Evaluator(
        registry=r,
        pool=fake_pool,
        witness=StubWitnessClient(
            default_outcome="rejected", default_second_operator="op-bob"
        ),
    )
    results = await ev.evaluate_once()
    assert results[0].triggered is True
    assert results[0].dispatched is False
    fire = fake_pool.store.fires[results[0].fire_id]
    assert fire["two_person_status"] == "rejected"


async def test_dispatch_failure_records_error(fake_pool) -> None:
    async def pred() -> bool:
        return True

    async def boom(*_args, **_kwargs):
        raise RuntimeError("dispatcher exploded")

    router = DispatchRouter()
    router.override("tenant-quarantine", boom)

    r = Registry()
    r.register(_condition("c1", predicate=pred))
    ev = Evaluator(registry=r, pool=fake_pool, router=router)

    results = await ev.evaluate_once()
    assert results[0].triggered is True
    assert results[0].dispatched is False
    assert "dispatcher exploded" in (results[0].error or "")
    fire = fake_pool.store.fires[results[0].fire_id]
    assert fire["dispatched"] is False
