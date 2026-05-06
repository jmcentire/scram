"""Registry tests — register, unregister, list, and persist+reload."""

from __future__ import annotations

import pytest

from scram.registry import Registry
from scram.types import KillAction, KillCondition


async def _stub_pred() -> bool:
    return False


def _make_condition(
    cid: str = "c1",
    *,
    auto_fire: bool = False,
    live: bool = False,
    requires_two_person: bool = False,
    has_predicate: bool = True,
) -> KillCondition:
    return KillCondition(
        id=cid,
        description=f"description for {cid}",
        action=KillAction(kind="tenant-quarantine", config={"tenant_id": "t1"}),
        auto_fire=auto_fire,
        live=live,
        requires_two_person=requires_two_person,
        registered_by="test",
        predicate=_stub_pred if has_predicate else None,
    )


def test_register_and_get() -> None:
    r = Registry()
    cond = _make_condition()
    r.register(cond)
    assert r.get("c1") is cond
    assert r.get("missing") is None


def test_register_replaces_same_id() -> None:
    r = Registry()
    r.register(_make_condition("c1", auto_fire=False))
    r.register(_make_condition("c1", auto_fire=True))
    assert r.get("c1").auto_fire is True


def test_register_rejects_empty_id() -> None:
    r = Registry()
    with pytest.raises(ValueError, match="id must be non-empty"):
        r.register(_make_condition(""))


def test_register_requires_predicate_when_auto_fire() -> None:
    r = Registry()
    with pytest.raises(ValueError, match="auto_fire=True but no predicate"):
        r.register(
            _make_condition(
                "c1", auto_fire=True, has_predicate=False
            )
        )


def test_unregister_returns_existed_flag() -> None:
    r = Registry()
    r.register(_make_condition("c1"))
    assert r.unregister("c1") is True
    assert r.unregister("c1") is False  # already gone


def test_list_returns_stable_order() -> None:
    r = Registry()
    for cid in ["c-z", "c-a", "c-m"]:
        r.register(_make_condition(cid))
    ids = [c.id for c in r.list()]
    assert ids == ["c-a", "c-m", "c-z"]


def test_auto_fire_conditions_filters_correctly() -> None:
    r = Registry()
    # auto_fire + live + predicate → included
    r.register(_make_condition("a", auto_fire=True, live=True))
    # auto_fire but not live → excluded
    r.register(_make_condition("b", auto_fire=True, live=False))
    # live but not auto_fire → excluded (manual-fire only)
    r.register(_make_condition("c", auto_fire=False, live=True))
    ids = [c.id for c in r.auto_fire_conditions()]
    assert ids == ["a"]


async def test_persist_writes_through(fake_pool) -> None:
    r = Registry()
    cond = _make_condition("c1", auto_fire=True, live=True)
    r.register(cond)
    await r.persist(fake_pool, cond)
    assert "c1" in fake_pool.store.conditions
    row = fake_pool.store.conditions["c1"]
    assert row["action_kind"] == "tenant-quarantine"
    assert row["auto_fire"] is True
    assert row["live"] is True


async def test_persist_upserts_on_conflict(fake_pool) -> None:
    r = Registry()
    cond1 = _make_condition("c1", auto_fire=False, live=False)
    cond2 = _make_condition("c1", auto_fire=True, live=True)
    r.register(cond1)
    await r.persist(fake_pool, cond1)
    r.register(cond2)
    await r.persist(fake_pool, cond2)
    row = fake_pool.store.conditions["c1"]
    assert row["auto_fire"] is True


async def test_delete_persisted_returns_existed(fake_pool) -> None:
    r = Registry()
    cond = _make_condition("c1")
    await r.persist(fake_pool, cond)
    assert await r.delete_persisted(fake_pool, "c1") is True
    assert await r.delete_persisted(fake_pool, "c1") is False


async def test_reload_metadata_preserves_predicates(fake_pool) -> None:
    """Reload pulls metadata from DB but keeps in-memory predicates."""
    r = Registry()
    cond = _make_condition("c1", auto_fire=True, live=True)
    r.register(cond)
    await r.persist(fake_pool, cond)

    # Simulate restart: drop in-memory state, reload from "DB".
    r2 = Registry()
    r2.register(cond)  # boot-time fresh registration brings predicate
    count = await r2.reload_metadata(fake_pool)
    assert count == 1
    reloaded = r2.get("c1")
    assert reloaded is not None
    assert reloaded.predicate is not None  # carried over from in-memory


async def test_reload_metadata_yields_predicate_less_for_unknown_ids(
    fake_pool,
) -> None:
    """A row in DB without a matching in-memory entry returns no predicate."""
    r = Registry()
    cond = _make_condition("c1")
    await r.persist(fake_pool, cond)
    # Fresh registry, no register() call.
    r2 = Registry()
    await r2.reload_metadata(fake_pool)
    reloaded = r2.get("c1")
    assert reloaded is not None
    assert reloaded.predicate is None
