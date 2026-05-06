"""Test fixtures.

The tests run hermetically (no real Postgres). We exercise the
persistence boundary with a fake pool/conn shim that records SQL +
serves canned rows. Behaviorally, this matches how the real asyncpg
pool is consumed (``async with pool.acquire() as conn`` →
``conn.execute(...)`` / ``conn.fetch(...)`` / ``conn.fetchrow(...)``)
without needing Docker. A future integration-test pass will swap in a
real container; for V1 the fake is the boundary the project needs.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest


class FakeConn:
    """Tracks SQL, returns canned rows, and emulates the bits of
    asyncpg's connection that scram uses."""

    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        sql_lower = sql.strip().lower()
        if sql_lower.startswith("insert into kill_conditions"):
            row = {
                "id": args[0],
                "description": args[1],
                "action_kind": args[2],
                "action_config": args[3],
                "auto_fire": args[4],
                "requires_two_person": args[5],
                "live": args[6],
                "registered_by": args[7],
                "registered_at": datetime.now(tz=UTC),
            }
            self.store.conditions[args[0]] = row
            return "INSERT 0 1"
        if sql_lower.startswith("delete from kill_conditions"):
            cid = args[0]
            existed = cid in self.store.conditions
            self.store.conditions.pop(cid, None)
            return "DELETE 1" if existed else "DELETE 0"
        if sql_lower.startswith("update kill_fires"):
            fire_id = args[0]
            fire = self.store.fires.get(fire_id)
            if fire is not None:
                fire["dispatched"] = args[1]
                fire["dispatch_result"] = args[2]
                if len(args) >= 5:
                    fire["two_person_status"] = args[3]
                    fire["second_operator"] = args[4]
                    fire["second_at"] = (
                        datetime.now(tz=UTC) if args[4] is not None else None
                    )
            return "UPDATE 1"
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((sql, args))
        sql_lower = sql.strip().lower()
        if "from kill_conditions" in sql_lower:
            return list(self.store.conditions.values())
        if "from kill_fires" in sql_lower:
            limit = args[0] if args else 50
            ordered = sorted(
                self.store.fires.values(), key=lambda r: r["fired_at"], reverse=True
            )
            return ordered[:limit]
        return []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.executed.append((sql, args))
        sql_lower = sql.strip().lower()
        if sql_lower.startswith("insert into kill_fires"):
            fid = uuid4()
            row = {
                "id": fid,
                "condition_id": args[0],
                "fired_by": args[1],
                "reason": args[2],
                "state_snapshot": args[3],
                "two_person_status": args[4],
                "fired_at": datetime.now(tz=UTC),
                "dispatched": False,
                "dispatch_result": None,
                "second_operator": None,
                "second_at": None,
            }
            self.store.fires[fid] = row
            return {"id": fid}
        return None


class FakePool:
    """asyncpg.Pool stand-in that yields FakeConns."""

    def __init__(self) -> None:
        self.store = FakeStore()

    @asynccontextmanager
    async def acquire(self):
        yield FakeConn(self.store)

    async def close(self) -> None:
        pass


class FakeStore:
    def __init__(self) -> None:
        self.conditions: dict[str, dict[str, Any]] = {}
        self.fires: dict[UUID, dict[str, Any]] = {}


@pytest.fixture
def fake_pool() -> FakePool:
    return FakePool()


@pytest.fixture
def api_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-token-abc"
    monkeypatch.setenv("SCRAM_API_TOKEN", token)
    return token


@pytest.fixture(autouse=True)
def _isolate_force_readonly_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests should run with the override OFF."""
    monkeypatch.delenv("SCRAM_FORCE_READONLY", raising=False)
    monkeypatch.delenv("SCRAM_WITNESS_AUTO_APPROVE", raising=False)


def fake_jsonb(value: Any) -> str:
    """Helper: tests serialize jsonb args the same way scram does."""
    return json.dumps(value)
