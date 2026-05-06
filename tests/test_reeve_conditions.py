"""Tests for the reeve_conditions registration helper."""

from __future__ import annotations

from scram.reeve_conditions import reeve_kill_conditions


def test_returns_three_conditions() -> None:
    conds = reeve_kill_conditions()
    assert len(conds) == 3


def test_condition_ids_match_adr() -> None:
    """ADR-001 names exactly these three; downstream catalogs reference them."""
    ids = {c.id for c in reeve_kill_conditions()}
    assert ids == {
        "cross-tenant-lateral-movement",
        "audit-chain-integrity-break",
        "emergency-readonly-on-pg-down",
    }


def test_two_person_only_for_global_actions() -> None:
    """Tenant-scoped action does not require two-person; cluster-wide does."""
    by_id = {c.id: c for c in reeve_kill_conditions()}
    assert by_id["cross-tenant-lateral-movement"].requires_two_person is False
    assert by_id["audit-chain-integrity-break"].requires_two_person is True
    assert by_id["emergency-readonly-on-pg-down"].requires_two_person is True


def test_all_default_to_dry_run() -> None:
    """live=False on first deploy; ops promotes after observation."""
    for c in reeve_kill_conditions():
        assert c.live is False


def test_all_have_predicate_stubs() -> None:
    """V1 stubs are present so the auto_fire invariant holds; predicates
    return False so the conditions do not actually trigger."""
    for c in reeve_kill_conditions():
        assert c.predicate is not None


def test_registered_by_override() -> None:
    conds = reeve_kill_conditions(registered_by="reeve-staging")
    assert all(c.registered_by == "reeve-staging" for c in conds)
