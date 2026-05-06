"""In-memory + persisted registry of kill conditions.

Predicates are LOCAL CALLABLES (sim-vetted; ADR-001). Registration is
idempotent and startup-only: each component re-registers on every boot,
so a stale entry from a deleted predicate is replaced or pruned. The
in-memory map keeps the actual callables; the ``kill_conditions`` table
keeps the metadata so other tools (CLI, ops dashboards) can introspect
without holding live function references.

Concurrency: ``Registry`` is intended to be used from a single asyncio
event loop. The internal dict is not thread-safe; that's fine because
all callers run on the same loop (FastAPI handlers + the eval loop).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import KillAction, KillCondition

if TYPE_CHECKING:
    import asyncpg


class Registry:
    """Holds the live in-memory map and writes through to Postgres.

    ``Registry`` does NOT own a connection pool — callers pass an
    ``asyncpg.Pool`` per call. This keeps the registry usable from
    short-lived CLI commands (which open a transient pool) and the
    long-running service (which reuses one) without lifecycle coupling.
    """

    def __init__(self) -> None:
        self._conditions: dict[str, KillCondition] = {}

    # ------------------------------------------------------------------
    # In-memory operations
    # ------------------------------------------------------------------

    def register(self, condition: KillCondition) -> None:
        """Register or replace a condition in memory.

        Validation: id must be non-empty; if ``auto_fire`` is True, a
        predicate is required. We deliberately do NOT validate at the
        type level (no ``Annotated`` constraint) because predicate-less
        manual conditions are valid.
        """
        if not condition.id:
            raise ValueError("KillCondition.id must be non-empty")
        if condition.auto_fire and condition.predicate is None:
            raise ValueError(
                f"condition {condition.id!r} has auto_fire=True but no predicate"
            )
        self._conditions[condition.id] = condition

    def unregister(self, condition_id: str) -> bool:
        """Remove a condition from memory. Returns True if it existed."""
        return self._conditions.pop(condition_id, None) is not None

    def list(self) -> list[KillCondition]:
        """Return all in-memory conditions in stable id order."""
        return [self._conditions[k] for k in sorted(self._conditions)]

    def get(self, condition_id: str) -> KillCondition | None:
        return self._conditions.get(condition_id)

    def auto_fire_conditions(self) -> list[KillCondition]:
        """Return live, auto-fire conditions that have a predicate set."""
        return [
            c
            for c in self._conditions.values()
            if c.live and c.auto_fire and c.predicate is not None
        ]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def persist(self, pool: asyncpg.Pool, condition: KillCondition) -> None:
        """Upsert one condition into ``kill_conditions``.

        The row mirrors in-memory metadata; the predicate callable is
        intentionally not persisted (it lives only in memory).
        """
        row = condition.to_row()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO kill_conditions
                  (id, description, action_kind, action_config,
                   auto_fire, requires_two_person, live, registered_by)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
                ON CONFLICT (id) DO UPDATE SET
                  description = EXCLUDED.description,
                  action_kind = EXCLUDED.action_kind,
                  action_config = EXCLUDED.action_config,
                  auto_fire = EXCLUDED.auto_fire,
                  requires_two_person = EXCLUDED.requires_two_person,
                  live = EXCLUDED.live,
                  registered_by = EXCLUDED.registered_by
                """,
                row["id"],
                row["description"],
                row["action_kind"],
                _to_json(row["action_config"]),
                row["auto_fire"],
                row["requires_two_person"],
                row["live"],
                row["registered_by"],
            )

    async def delete_persisted(self, pool: asyncpg.Pool, condition_id: str) -> bool:
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM kill_conditions WHERE id = $1", condition_id
            )
        # asyncpg returns "DELETE N"
        return result.endswith(" 1")

    async def reload_metadata(self, pool: asyncpg.Pool) -> int:
        """Pull metadata from Postgres into memory.

        Predicates from old in-memory entries are PRESERVED for ids that
        still exist (fresh registrations from boot will overwrite them
        anyway); rows with no in-memory predicate stay predicate-less
        (manual-fire only). Returns the number of rows reloaded.
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, description, action_kind, action_config,
                       auto_fire, requires_two_person, live, registered_by,
                       registered_at
                FROM kill_conditions
                """
            )
        for r in rows:
            existing = self._conditions.get(r["id"])
            self._conditions[r["id"]] = KillCondition(
                id=r["id"],
                description=r["description"],
                action=KillAction(
                    kind=r["action_kind"],
                    config=_from_json(r["action_config"]),
                ),
                auto_fire=r["auto_fire"],
                requires_two_person=r["requires_two_person"],
                live=r["live"],
                registered_by=r["registered_by"],
                predicate=existing.predicate if existing else None,
                registered_at=r["registered_at"],
            )
        return len(rows)


def _to_json(value: dict[str, object]) -> str:
    """asyncpg accepts a json string for ``jsonb`` parameters; serialize here."""
    import json

    return json.dumps(value)


def _from_json(value: object) -> dict[str, object]:
    """asyncpg returns ``jsonb`` as a Python str (no codec) or already-parsed dict."""
    import json

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return {}


__all__ = ["Registry"]
