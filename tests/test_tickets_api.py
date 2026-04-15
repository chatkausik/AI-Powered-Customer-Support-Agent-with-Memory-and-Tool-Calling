from __future__ import annotations

from fastapi.testclient import TestClient

_TICKET = {
    "customer_email": "alice@example.com",
    "customer_name": "Alice",
    "customer_company": "Acme",
    "subject": "Cannot login to dashboard",
    "description": "I have been unable to login for the past three days.",
    "priority": "high",
    "auto_generate": False,  # skip LLM in tests
}


def test_create_ticket_returns_ticket_data(api_client: TestClient) -> None:
    response = api_client.post("/api/tickets", json=_TICKET)
    assert response.status_code == 200
    body = response.json()
    assert body["customer_email"] == "alice@example.com"
    assert body["subject"] == "Cannot login to dashboard"
    assert body["priority"] == "high"
    assert body["status"] == "open"
    assert isinstance(body["id"], int)


def test_create_ticket_reuses_existing_customer(api_client: TestClient) -> None:
    """Two tickets for the same email must share the same customer_id."""
    r1 = api_client.post("/api/tickets", json=_TICKET).json()
    r2 = api_client.post("/api/tickets", json={**_TICKET, "subject": "Second issue", "description": "Another problem here."}).json()
    assert r1["customer_id"] == r2["customer_id"]


def test_list_tickets_includes_created_ticket(api_client: TestClient) -> None:
    api_client.post("/api/tickets", json=_TICKET)
    response = api_client.get("/api/tickets")
    assert response.status_code == 200
    tickets = response.json()
    assert isinstance(tickets, list)
    assert any(t["subject"] == "Cannot login to dashboard" for t in tickets)


def test_get_ticket_by_id(api_client: TestClient) -> None:
    created = api_client.post("/api/tickets", json=_TICKET).json()
    response = api_client.get(f"/api/tickets/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_ticket_returns_404_for_unknown_id(api_client: TestClient) -> None:
    response = api_client.get("/api/tickets/99999")
    assert response.status_code == 404
