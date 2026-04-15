from __future__ import annotations

import json

from fastapi.testclient import TestClient

from customer_support_agent.repositories.sqlite.customers import CustomersRepository
from customer_support_agent.repositories.sqlite.drafts import DraftsRepository
from customer_support_agent.repositories.sqlite.tickets import TicketsRepository


def _seed_draft(content: str = "Draft reply content.") -> tuple[int, int]:
    """Insert customer → ticket → draft and return (ticket_id, draft_id).

    Relies on the patched get_settings being active (via api_client fixture).
    """
    customer = CustomersRepository().create_or_get(
        email="drafter@example.com", name="Drafter"
    )
    ticket = TicketsRepository().create(
        customer_id=customer["id"],
        subject="Draft test ticket",
        description="A ticket used for draft API tests.",
    )
    draft = DraftsRepository().create(
        ticket_id=ticket["id"],
        content=content,
        context_used=json.dumps({"version": 2, "errors": []}),
    )
    return ticket["id"], draft["id"]


def test_get_draft_returns_latest_for_ticket(api_client: TestClient) -> None:
    ticket_id, _ = _seed_draft("Hello, here is your draft.")
    response = api_client.get(f"/api/drafts/{ticket_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["ticket_id"] == ticket_id
    assert body["content"] == "Hello, here is your draft."
    assert body["status"] == "pending"


def test_get_draft_returns_404_when_no_draft_exists(api_client: TestClient) -> None:
    customer = CustomersRepository().create_or_get(email="nodraft@example.com")
    ticket = TicketsRepository().create(
        customer_id=customer["id"],
        subject="No draft",
        description="This ticket intentionally has no draft.",
    )
    response = api_client.get(f"/api/drafts/{ticket['id']}")
    assert response.status_code == 404


def test_patch_draft_updates_content(api_client: TestClient) -> None:
    _, draft_id = _seed_draft("Original text.")
    response = api_client.patch(
        f"/api/drafts/{draft_id}",
        json={"content": "Revised text from agent."},
    )
    assert response.status_code == 200
    assert response.json()["content"] == "Revised text from agent."


def test_patch_draft_accept_sets_status_to_accepted(api_client: TestClient) -> None:
    _, draft_id = _seed_draft("Final accepted reply.")
    response = api_client.patch(
        f"/api/drafts/{draft_id}",
        json={"status": "accepted"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_patch_draft_returns_404_for_unknown_draft(api_client: TestClient) -> None:
    response = api_client.patch(
        "/api/drafts/99999",
        json={"content": "irrelevant"},
    )
    assert response.status_code == 404
