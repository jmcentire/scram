"""Tests for the boot-only env-var override.

These tests verify the documented contract:
1. ``is_force_readonly_enabled()`` reads the env-var at module import,
   not on every call.
2. The cache is recomputable for tests via ``_recompute_for_tests``.
3. The default is False when the env-var is absent or any value other
   than the literal string ``"1"``.
"""

from __future__ import annotations

import pytest

from scram import force_readonly


def test_default_is_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCRAM_FORCE_READONLY", raising=False)
    force_readonly._recompute_for_tests()
    assert force_readonly.is_force_readonly_enabled() is False


def test_true_when_set_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAM_FORCE_READONLY", "1")
    force_readonly._recompute_for_tests()
    assert force_readonly.is_force_readonly_enabled() is True


@pytest.mark.parametrize("value", ["true", "yes", "0", "", "TRUE", "on"])
def test_only_literal_one_enables(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """The contract is exact-string match on '1'. Any other value is False
    so we don't accidentally trigger readonly from a typo'd config."""
    monkeypatch.setenv("SCRAM_FORCE_READONLY", value)
    force_readonly._recompute_for_tests()
    assert force_readonly.is_force_readonly_enabled() is False


def test_caching_means_runtime_change_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of boot-only: setting the var after the cache is
    populated has zero effect until ``_recompute_for_tests`` is called."""
    monkeypatch.delenv("SCRAM_FORCE_READONLY", raising=False)
    force_readonly._recompute_for_tests()
    assert force_readonly.is_force_readonly_enabled() is False

    # Mid-runtime flip — production code MUST NOT do this; we simulate it
    # only to assert it's a no-op.
    monkeypatch.setenv("SCRAM_FORCE_READONLY", "1")
    assert force_readonly.is_force_readonly_enabled() is False  # still cached

    # Explicit recompute is the test-only escape hatch.
    force_readonly._recompute_for_tests()
    assert force_readonly.is_force_readonly_enabled() is True


def test_recompute_helper_returns_new_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCRAM_FORCE_READONLY", "1")
    assert force_readonly._recompute_for_tests() is True
    monkeypatch.delenv("SCRAM_FORCE_READONLY", raising=False)
    assert force_readonly._recompute_for_tests() is False
