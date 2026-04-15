from __future__ import annotations

import pytest

from customer_support_agent.repositories.sqlite.customers import CustomersRepository


def test_create_or_get_creates_new_customer(patched_db) -> None:
    repo = CustomersRepository()
    customer = repo.create_or_get(
        email="new@example.com", name="New User", company="Acme"
    )
    assert customer["email"] == "new@example.com"
    assert customer["name"] == "New User"
    assert customer["company"] == "Acme"
    assert isinstance(customer["id"], int)


def test_create_or_get_returns_same_row_on_second_call(patched_db) -> None:
    repo = CustomersRepository()
    first = repo.create_or_get(email="repeat@example.com")
    second = repo.create_or_get(email="repeat@example.com")
    assert first["id"] == second["id"]


def test_create_or_get_backfills_missing_name_and_company(patched_db) -> None:
    """Calling create_or_get again with extra fields fills in blank columns."""
    repo = CustomersRepository()
    repo.create_or_get(email="partial@example.com")
    updated = repo.create_or_get(
        email="partial@example.com", name="Partial User", company="Corp"
    )
    assert updated["name"] == "Partial User"
    assert updated["company"] == "Corp"


def test_get_by_id_returns_correct_customer(patched_db) -> None:
    repo = CustomersRepository()
    created = repo.create_or_get(email="byid@example.com", name="By ID")
    fetched = repo.get_by_id(created["id"])
    assert fetched is not None
    assert fetched["email"] == "byid@example.com"


def test_get_by_id_returns_none_for_unknown_id(patched_db) -> None:
    repo = CustomersRepository()
    assert repo.get_by_id(99999) is None


def test_get_by_email_returns_correct_customer(patched_db) -> None:
    """Regression: get_by_email must use WHERE email = ?, not WHERE id = ?."""
    repo = CustomersRepository()
    # Create two customers so the target is NOT the first row (id=1).
    repo.create_or_get(email="first@example.com")
    repo.create_or_get(email="target@example.com", name="Target User")

    result = repo.get_by_email("target@example.com")

    assert result is not None, (
        "get_by_email returned None — likely querying 'WHERE id = ?' instead of 'WHERE email = ?'"
    )
    assert result["email"] == "target@example.com"
    assert result["name"] == "Target User"


def test_get_by_email_returns_none_for_unknown_email(patched_db) -> None:
    repo = CustomersRepository()
    assert repo.get_by_email("ghost@nowhere.com") is None
