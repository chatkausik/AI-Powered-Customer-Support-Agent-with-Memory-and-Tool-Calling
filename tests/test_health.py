from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok(api_client: TestClient) -> None:
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_reachable_without_auth_headers(api_client: TestClient) -> None:
    """Health check must be reachable with no auth or extra headers."""
    response = api_client.get("/health", headers={})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
