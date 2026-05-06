"""Periodic predicate evaluator.

Polls every ``auto_fire=true AND live=true`` condition on a fixed
interval (default 5s; ``SCRAM_EVAL_INTERVAL_S`` overrides). For each
condition:

1. Calls the registered local Python predicate, wrapped in an
   aegis-style asyncio.wait_for budget so a hung predicate cannot stall
   the loop.
2. If True, writes a ``kill_fires`` row and (for non-two-person
   conditions) immediately dispatches the action; for two-person
   conditions, asks witness and dispatches only on ``approved``.
3. Records dispatch outcome back into the row.

Why hand-rolled instead of importing aegis: scram intentionally has
zero stack-internal Python dependencies. Aegis is a sibling Python
package that *will* be the right import once both are stable; today,
the asyncio.wait_for pattern below is what aegis itself uses, so the
shape is portable. When aegis V1 lands and is published, swap the
``_call_predicate_with_budget`` body for ``aegis.with_resource_budget``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from typing import TYPE_CHECKING

from .dispatch import DispatchRouter
from .registry import Registry
from .two_person import (
    StubWitnessClient,
    WitnessClient,
    evaluate_two_person,
)
from .types import KillCondition, Predicate, TriggerResult

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg

logger = logging.getLogger("scram.evaluator")

# Default per-predicate budget. Sized for a database-bound predicate;
# anything tighter starves Postgres on cold connections, anything looser
# means a hung predicate can starve the eval loop. Override per
# condition is V2 (action_config field "predicate_timeout_s").
DEFAULT_PREDICATE_TIMEOUT_S = 2.0


def eval_interval_seconds() -> float:
    """Resolve the loop tick interval from env, defaulting to 5s."""
    raw = os.environ.get("SCRAM_EVAL_INTERVAL_S")
    if raw is None:
        return 5.0
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "SCRAM_EVAL_INTERVAL_S=%r is not a float; using 5s", raw
        )
        return 5.0


async def _call_predicate_with_budget(
    predicate: Predicate, timeout_s: float
) -> bool:
    """Invoke a sync-or-async predicate under a wall-clock budget.

    Mirrors aegis's contract: the predicate must complete within
    ``timeout_s`` or be considered failed (returns False, logs warning).
    Sync predicates run on the default executor so they don't block the
    event loop.
    """
    loop = asyncio.get_running_loop()
    if inspect.iscoroutinefunction(predicate):
        coro = predicate()
    else:
        # Run sync callables in a thread to avoid blocking the loop.
        coro = loop.run_in_executor(None, predicate)
    try:
        return bool(await asyncio.wait_for(coro, timeout=timeout_s))
    except TimeoutError:
        logger.warning(
            "predicate exceeded %.2fs budget; treating as False", timeout_s
        )
        return False


async def _record_fire(
    pool: asyncpg.Pool,
    *,
    condition_id: str,
    fired_by: str,
    reason: str,
    state_snapshot: dict[str, object],
    two_person_status: str,
) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO kill_fires
              (condition_id, fired_by, reason, state_snapshot, two_person_status)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            RETURNING id
            """,
            condition_id,
            fired_by,
            reason,
            json.dumps(state_snapshot),
            two_person_status,
        )
    assert row is not None
    return row["id"]


async def _update_fire_dispatch(
    pool: asyncpg.Pool,
    *,
    fire_id: UUID,
    dispatched: bool,
    dispatch_result: str,
    two_person_status: str | None = None,
    second_operator: str | None = None,
) -> None:
    async with pool.acquire() as conn:
        if two_person_status is None:
            await conn.execute(
                """
                UPDATE kill_fires
                SET dispatched = $2, dispatch_result = $3
                WHERE id = $1
                """,
                fire_id,
                dispatched,
                dispatch_result,
            )
        else:
            await conn.execute(
                """
                UPDATE kill_fires
                SET dispatched = $2,
                    dispatch_result = $3,
                    two_person_status = $4,
                    second_operator = $5,
                    second_at = CASE WHEN $5 IS NULL THEN NULL ELSE now() END
                WHERE id = $1
                """,
                fire_id,
                dispatched,
                dispatch_result,
                two_person_status,
                second_operator,
            )


class Evaluator:
    """Owns the periodic eval loop and the fire-recording lifecycle.

    Construction takes the dependencies; ``run()`` is an async coroutine
    that loops until cancelled. Tests drive it via the lower-level
    :func:`evaluate_once`.
    """

    def __init__(
        self,
        *,
        registry: Registry,
        pool: asyncpg.Pool,
        router: DispatchRouter | None = None,
        witness: WitnessClient | None = None,
        predicate_timeout_s: float = DEFAULT_PREDICATE_TIMEOUT_S,
    ) -> None:
        self.registry = registry
        self.pool = pool
        self.router = router or DispatchRouter()
        self.witness = witness or StubWitnessClient()
        self.predicate_timeout_s = predicate_timeout_s

    async def evaluate_once(self) -> list[TriggerResult]:
        """Evaluate all auto-fire conditions once. Returns per-condition results."""
        results: list[TriggerResult] = []
        for condition in self.registry.auto_fire_conditions():
            result = await self._evaluate_condition(condition)
            results.append(result)
        return results

    async def _evaluate_condition(
        self, condition: KillCondition
    ) -> TriggerResult:
        assert condition.predicate is not None  # invariant from auto_fire_conditions
        try:
            triggered = await _call_predicate_with_budget(
                condition.predicate, self.predicate_timeout_s
            )
        except Exception as e:
            logger.error(
                "predicate raised for condition=%s: %s", condition.id, e
            )
            return TriggerResult(
                condition_id=condition.id,
                triggered=False,
                fire_id=None,
                dispatched=False,
                error=f"predicate raised: {e}",
            )
        if not triggered:
            return TriggerResult(
                condition_id=condition.id,
                triggered=False,
                fire_id=None,
                dispatched=False,
            )

        # Triggered: record + (maybe) dispatch.
        return await self.fire(
            condition,
            fired_by="auto",
            reason=f"predicate returned True for condition {condition.id}",
            state_snapshot={"source": "auto", "predicate": condition.id},
        )

    async def fire(
        self,
        condition: KillCondition,
        *,
        fired_by: str,
        reason: str,
        state_snapshot: dict[str, object] | None = None,
    ) -> TriggerResult:
        """Record a kill_fires row and dispatch (subject to two-person)."""
        snapshot = state_snapshot or {}
        initial_status = "pending" if condition.requires_two_person else "not-required"
        fire_id = await _record_fire(
            self.pool,
            condition_id=condition.id,
            fired_by=fired_by,
            reason=reason,
            state_snapshot=snapshot,
            two_person_status=initial_status,
        )

        if condition.requires_two_person:
            outcome, second_op = await evaluate_two_person(
                self.witness,
                fire_id=str(fire_id),
                condition_id=condition.id,
                action_kind=condition.action.kind,
                reason=reason,
                fired_by=fired_by,
                state_snapshot=snapshot,
            )
            if outcome != "approved":
                # Persist outcome; do NOT dispatch.
                await _update_fire_dispatch(
                    self.pool,
                    fire_id=fire_id,
                    dispatched=False,
                    dispatch_result=f"two-person {outcome}; dispatch blocked",
                    two_person_status=outcome,
                    second_operator=second_op,
                )
                return TriggerResult(
                    condition_id=condition.id,
                    triggered=True,
                    fire_id=fire_id,
                    dispatched=False,
                    dispatch_descriptor=f"two-person {outcome}",
                )
            # Approved — fall through to dispatch.
            await _update_fire_dispatch(
                self.pool,
                fire_id=fire_id,
                dispatched=False,
                dispatch_result="two-person approved; dispatching",
                two_person_status="approved",
                second_operator=second_op,
            )

        result = await self.router.dispatch(condition, reason)
        descriptor = result.dispatch_descriptor or (result.error or "unknown")
        await _update_fire_dispatch(
            self.pool,
            fire_id=fire_id,
            dispatched=result.dispatched,
            dispatch_result=descriptor,
        )
        return TriggerResult(
            condition_id=condition.id,
            triggered=True,
            fire_id=fire_id,
            dispatched=result.dispatched,
            dispatch_descriptor=result.dispatch_descriptor,
            error=result.error,
        )

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Run the eval loop until cancelled or ``stop_event`` is set.

        Each tick is bounded by the eval interval; if a tick takes
        longer than the interval, the next tick fires immediately and
        we log a warning. Shutdown is cooperative: cancellation or
        ``stop_event.set()`` terminates the loop cleanly.
        """
        interval = eval_interval_seconds()
        logger.info("scram evaluator starting; interval=%.2fs", interval)
        while True:
            if stop_event is not None and stop_event.is_set():
                logger.info("scram evaluator stopping (stop_event set)")
                return
            tick_start = asyncio.get_running_loop().time()
            try:
                results = await self.evaluate_once()
                fired = [r for r in results if r.triggered]
                if fired:
                    logger.warning(
                        "evaluator tick: %d/%d conditions fired",
                        len(fired),
                        len(results),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("evaluator tick raised: %s", e)
            elapsed = asyncio.get_running_loop().time() - tick_start
            sleep_s = interval - elapsed
            if sleep_s <= 0:
                logger.warning(
                    "tick took %.2fs >= interval %.2fs; running back-to-back",
                    elapsed,
                    interval,
                )
                continue
            try:
                await asyncio.sleep(sleep_s)
            except asyncio.CancelledError:
                raise


__all__ = [
    "DEFAULT_PREDICATE_TIMEOUT_S",
    "Evaluator",
    "eval_interval_seconds",
]
