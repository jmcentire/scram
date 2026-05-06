"""Two-person rule + witness stub tests."""

from __future__ import annotations

import logging

import pytest

from scram.two_person import (
    Decision,
    StubWitnessClient,
    evaluate_two_person,
)


def _make_decision() -> Decision:
    return Decision(
        id="fire-id-1",
        kind="scram-confirm",
        condition_id="c1",
        action_kind="global-readonly",
        reason="test",
        fired_by="op-alice",
        state_snapshot={},
    )


async def test_default_stub_returns_pending() -> None:
    """Without env-var override, stub blocks dispatch by returning pending."""
    client = StubWitnessClient()
    outcome, second_op = await client.ask(_make_decision())
    assert outcome == "pending"
    assert second_op is None


async def test_break_glass_env_var_returns_approved(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SCRAM_WITNESS_AUTO_APPROVE", "1")
    client = StubWitnessClient()
    with caplog.at_level(logging.ERROR, logger="scram.two_person"):
        outcome, second_op = await client.ask(_make_decision())
    assert outcome == "approved"
    assert second_op == "break-glass"
    # Break-glass MUST log at ERROR.
    assert any("BREAK-GLASS" in r.message for r in caplog.records)


async def test_explicit_outcome_override() -> None:
    client = StubWitnessClient(
        default_outcome="approved", default_second_operator="op-bob"
    )
    outcome, second_op = await client.ask(_make_decision())
    assert outcome == "approved"
    assert second_op == "op-bob"


async def test_explicit_rejected_outcome() -> None:
    client = StubWitnessClient(default_outcome="rejected")
    outcome, _ = await client.ask(_make_decision())
    assert outcome == "rejected"


async def test_explicit_timed_out_outcome() -> None:
    client = StubWitnessClient(default_outcome="timed-out")
    outcome, _ = await client.ask(_make_decision())
    assert outcome == "timed-out"


async def test_evaluate_two_person_constructs_decision() -> None:
    """evaluate_two_person wraps the inputs in a Decision before calling ask."""

    received: list[Decision] = []

    class Recording:
        async def ask(self, decision: Decision):
            received.append(decision)
            return ("approved", "op-bob")

    outcome, second = await evaluate_two_person(
        Recording(),
        fire_id="fire-1",
        condition_id="c1",
        action_kind="global-readonly",
        reason="test reason",
        fired_by="op-alice",
        state_snapshot={"k": "v"},
    )
    assert outcome == "approved"
    assert second == "op-bob"
    assert len(received) == 1
    decision = received[0]
    assert decision.id == "fire-1"
    assert decision.kind == "scram-confirm"
    assert decision.condition_id == "c1"
    assert decision.action_kind == "global-readonly"
    assert decision.reason == "test reason"
    assert decision.fired_by == "op-alice"
    assert decision.state_snapshot == {"k": "v"}


async def test_default_outcome_pending_logged_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pending outcomes log at WARNING (not ERROR; that's reserved for break-glass)."""
    client = StubWitnessClient(default_outcome="pending")
    with caplog.at_level(logging.WARNING, logger="scram.two_person"):
        await client.ask(_make_decision())
    assert any("ask" in r.message and r.levelno == logging.WARNING for r in caplog.records)
