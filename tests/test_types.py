"""Type-level invariant tests."""

from __future__ import annotations

from scram.types import KillAction, KillCondition, TriggerResult


def test_kill_action_to_jsonable() -> None:
    action = KillAction(kind="tenant-quarantine", config={"tenant_id": "t1"})
    assert action.to_jsonable() == {
        "kind": "tenant-quarantine",
        "config": {"tenant_id": "t1"},
    }


def test_kill_action_default_config() -> None:
    action = KillAction(kind="global-readonly")
    assert action.config == {}


def test_kill_condition_to_row_round_trip_shape() -> None:
    cond = KillCondition(
        id="c1",
        description="d",
        action=KillAction(kind="process-exit", config={"code": 137}),
        auto_fire=True,
        requires_two_person=False,
        live=True,
        registered_by="me",
    )
    row = cond.to_row()
    assert row["id"] == "c1"
    assert row["action_kind"] == "process-exit"
    assert row["action_config"] == {"code": 137}
    assert row["auto_fire"] is True
    assert row["live"] is True


def test_trigger_result_default_dispatch_descriptor_none() -> None:
    r = TriggerResult(
        condition_id="c1",
        triggered=False,
        fire_id=None,
        dispatched=False,
    )
    assert r.dispatch_descriptor is None
    assert r.error is None


def test_trigger_result_carries_descriptor() -> None:
    r = TriggerResult(
        condition_id="c1",
        triggered=True,
        fire_id=None,
        dispatched=True,
        dispatch_descriptor="stub:tenant-quarantine tenant=t1",
    )
    assert r.dispatch_descriptor == "stub:tenant-quarantine tenant=t1"
