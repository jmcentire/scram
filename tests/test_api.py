"""FastAPI surface tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from scram.api import create_app
from scram.evaluator import Evaluator
from scram.registry import Registry
from scram.two_person import StubWitnessClient


def _client(fake_pool, *, with_evaluator: bool = True) -> TestClient:
    registry = Registry()
    evaluator = (
        Evaluator(
            registry=registry,
            pool=fake_pool,
            witness=StubWitnessClient(default_outcome="approved", default_second_operator="op-bob"),
        )
        if with_evaluator
        else None
    )
    app = create_app(registry=registry, evaluator=evaluator, pool=fake_pool)
    return TestClient(app)


def _condition_payload(cid: str = "c1", **overrides) -> dict:
    base = {
        "id": cid,
        "description": "test condition",
        "action": {"kind": "tenant-quarantine", "config": {"tenant_id": "t1"}},
        "auto_fire": False,
        "requires_two_person": False,
        "live": False,
        "registered_by": "test",
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# Health
# ----------------------------------------------------------------------


def test_health_returns_ok(fake_pool) -> None:
    client = _client(fake_pool)
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["registered_conditions"] == 0
    assert "tenant-quarantine" in body["valid_action_kinds"]


# ----------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------


def test_create_condition_requires_token(fake_pool, monkeypatch) -> None:
    monkeypatch.setenv("SCRAM_API_TOKEN", "set-but-omitted-from-request")
    client = _client(fake_pool)
    resp = client.post("/v1/conditions", json=_condition_payload())
    assert resp.status_code == 401


def test_create_condition_rejects_bad_token(fake_pool, api_token) -> None:
    client = _client(fake_pool)
    resp = client.post(
        "/v1/conditions",
        json=_condition_payload(),
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_authed_endpoint_503s_when_token_unset(
    fake_pool, monkeypatch
) -> None:
    """Service refuses authed calls until SCRAM_API_TOKEN is set: fail-closed."""
    monkeypatch.delenv("SCRAM_API_TOKEN", raising=False)
    client = _client(fake_pool)
    resp = client.post(
        "/v1/conditions",
        json=_condition_payload(),
        headers={"Authorization": "Bearer anything"},
    )
    assert resp.status_code == 503


# ----------------------------------------------------------------------
# Conditions CRUD
# ----------------------------------------------------------------------


def test_list_conditions_initially_empty(fake_pool) -> None:
    client = _client(fake_pool)
    resp = client.get("/v1/conditions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_then_list_condition(fake_pool, api_token) -> None:
    client = _client(fake_pool)
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = client.post(
        "/v1/conditions", json=_condition_payload(), headers=headers
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "c1"
    assert body["has_predicate"] is False  # API never accepts predicates

    listed = client.get("/v1/conditions").json()
    assert [c["id"] for c in listed] == ["c1"]


def test_create_rejects_unknown_action_kind(fake_pool, api_token) -> None:
    client = _client(fake_pool)
    payload = _condition_payload()
    payload["action"]["kind"] = "definitely-not-real"
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = client.post("/v1/conditions", json=payload, headers=headers)
    assert resp.status_code == 422


def test_create_rejects_missing_required_field(fake_pool, api_token) -> None:
    client = _client(fake_pool)
    payload = _condition_payload()
    del payload["description"]
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = client.post("/v1/conditions", json=payload, headers=headers)
    assert resp.status_code == 422


def test_create_rejects_extra_fields(fake_pool, api_token) -> None:
    """Pydantic strict-extra catches typos and trojan fields."""
    client = _client(fake_pool)
    payload = _condition_payload()
    payload["surprise"] = "yes"
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = client.post("/v1/conditions", json=payload, headers=headers)
    assert resp.status_code == 422


def test_delete_condition(fake_pool, api_token) -> None:
    client = _client(fake_pool)
    headers = {"Authorization": f"Bearer {api_token}"}
    client.post("/v1/conditions", json=_condition_payload(), headers=headers)
    resp = client.delete("/v1/conditions/c1", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["removed_from_memory"] is True
    assert body["removed_from_db"] is True

    resp = client.delete("/v1/conditions/c1", headers=headers)
    assert resp.status_code == 404


# ----------------------------------------------------------------------
# Manual fire
# ----------------------------------------------------------------------


def test_manual_fire_works_after_register(fake_pool, api_token) -> None:
    client = _client(fake_pool)
    headers = {"Authorization": f"Bearer {api_token}"}
    client.post(
        "/v1/conditions",
        json=_condition_payload("c-fire", live=True),
        headers=headers,
    )
    resp = client.post(
        "/v1/fire",
        headers=headers,
        json={
            "condition_id": "c-fire",
            "reason": "operator drill",
            "operator": "op-alice",
            "state_snapshot": {"drill": True},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispatched"] is True
    assert body["fire_id"] is not None
    assert body["dispatch_descriptor"] is not None


def test_manual_fire_unknown_condition_404(fake_pool, api_token) -> None:
    client = _client(fake_pool)
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = client.post(
        "/v1/fire",
        headers=headers,
        json={
            "condition_id": "no-such-thing",
            "reason": "reason",
            "operator": "op",
        },
    )
    assert resp.status_code == 404


def test_manual_fire_503_when_evaluator_missing(fake_pool, api_token) -> None:
    client = _client(fake_pool, with_evaluator=False)
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = client.post(
        "/v1/fire",
        headers=headers,
        json={"condition_id": "x", "reason": "r", "operator": "o"},
    )
    assert resp.status_code == 503


# ----------------------------------------------------------------------
# Fires listing
# ----------------------------------------------------------------------


def test_list_fires_empty(fake_pool) -> None:
    client = _client(fake_pool)
    resp = client.get("/v1/fires")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_fires_returns_recorded(fake_pool, api_token) -> None:
    client = _client(fake_pool)
    headers = {"Authorization": f"Bearer {api_token}"}
    client.post(
        "/v1/conditions",
        json=_condition_payload("c-fire", live=True),
        headers=headers,
    )
    client.post(
        "/v1/fire",
        headers=headers,
        json={"condition_id": "c-fire", "reason": "drill", "operator": "op-a"},
    )
    resp = client.get("/v1/fires")
    assert resp.status_code == 200
    fires = resp.json()
    assert len(fires) == 1
    assert fires[0]["condition_id"] == "c-fire"
    assert fires[0]["dispatched"] is True


def test_list_fires_limit_clamped(fake_pool, api_token) -> None:
    client = _client(fake_pool)
    resp = client.get("/v1/fires?limit=99999")
    # Clamped to 500; response succeeds.
    assert resp.status_code == 200
