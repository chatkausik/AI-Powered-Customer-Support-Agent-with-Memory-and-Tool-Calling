from __future__ import annotations

import json

from customer_support_agent.integrations.tools.sentiment_tools import analyze_ticket_sentiment
from customer_support_agent.integrations.tools.support_tools import (
    get_support_tools,
    lookup_customer_plan,
    lookup_open_ticket_load,
)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


def test_get_support_tools_exposes_all_three_tools() -> None:
    names = [t.name for t in get_support_tools()]
    assert "lookup_customer_plan" in names
    assert "lookup_open_ticket_load" in names
    assert "analyze_ticket_sentiment" in names


# ---------------------------------------------------------------------------
# lookup_customer_plan
# ---------------------------------------------------------------------------


def test_lookup_customer_plan_returns_valid_json_structure() -> None:
    raw = lookup_customer_plan.invoke({"customer_email": "user@example.com"})
    result = json.loads(raw)

    assert result["tool"] == "lookup_customer_plan"
    assert result["details"]["plan_tier"] in ("free", "starter", "pro", "enterprise")
    assert isinstance(result["details"]["sla_hours"], int)
    assert isinstance(result["details"]["priority_queue"], bool)
    assert "summary" in result
    assert "recommended_action" in result


def test_lookup_customer_plan_is_deterministic() -> None:
    r1 = json.loads(lookup_customer_plan.invoke({"customer_email": "stable@test.com"}))
    r2 = json.loads(lookup_customer_plan.invoke({"customer_email": "stable@test.com"}))
    assert r1["details"]["plan_tier"] == r2["details"]["plan_tier"]
    assert r1["details"]["sla_hours"] == r2["details"]["sla_hours"]


def test_lookup_customer_plan_varies_by_email() -> None:
    """Different emails should eventually produce different plans."""
    emails = [f"user{i}@test.com" for i in range(20)]
    tiers = {
        json.loads(lookup_customer_plan.invoke({"customer_email": e}))["details"]["plan_tier"]
        for e in emails
    }
    assert len(tiers) > 1, "Expected multiple distinct plan tiers across 20 emails"


# ---------------------------------------------------------------------------
# lookup_open_ticket_load
# ---------------------------------------------------------------------------


def test_lookup_open_ticket_load_unknown_email(patched_db) -> None:
    result = json.loads(
        lookup_open_ticket_load.invoke({"customer_email": "ghost@nowhere.com"})
    )
    assert result["tool"] == "lookup_open_ticket_load"
    assert result["details"]["customer_found"] is False
    assert result["details"]["open_tickets"] is None
    assert result["details"]["load_band"] == "unknown"


def test_lookup_open_ticket_load_counts_open_tickets(patched_db) -> None:
    from customer_support_agent.repositories.sqlite.customers import CustomersRepository
    from customer_support_agent.repositories.sqlite.tickets import TicketsRepository

    email = "loaded@example.com"
    customer = CustomersRepository().create_or_get(email=email)
    TicketsRepository().create(
        customer_id=customer["id"],
        subject="Issue one",
        description="First open ticket.",
    )
    TicketsRepository().create(
        customer_id=customer["id"],
        subject="Issue two",
        description="Second open ticket.",
    )

    result = json.loads(lookup_open_ticket_load.invoke({"customer_email": email}))

    assert result["details"]["customer_found"] is True
    assert result["details"]["open_tickets"] == 2
    assert result["details"]["load_band"] == "moderate"


# ---------------------------------------------------------------------------
# analyze_ticket_sentiment
# ---------------------------------------------------------------------------


def test_analyze_sentiment_high_escalation_billing_complaint() -> None:
    result = json.loads(
        analyze_ticket_sentiment.invoke({
            "subject": "Charged 3 times and nobody is helping",
            "description": "I have been charged 3 times and nobody is helping.",
        })
    )
    assert result["sentiment"] == "negative"
    assert result["escalation_risk"] == "high"
    assert result["confidence"] > 0.7
    assert "recommended_action" in result
    assert "summary" in result


def test_analyze_sentiment_neutral_routine_query() -> None:
    result = json.loads(
        analyze_ticket_sentiment.invoke({
            "subject": "Account limits query",
            "description": "Just checking account limits for next month.",
        })
    )
    assert result["sentiment"] == "neutral"
    assert result["escalation_risk"] == "low"
    assert result["confidence"] > 0.0


def test_analyze_sentiment_returns_all_required_keys() -> None:
    result = json.loads(
        analyze_ticket_sentiment.invoke({
            "subject": "Login issue",
            "description": "I cannot login to my account.",
        })
    )
    for key in ("tool", "sentiment", "confidence", "escalation_risk", "summary", "recommended_action"):
        assert key in result, f"Missing key: {key}"
