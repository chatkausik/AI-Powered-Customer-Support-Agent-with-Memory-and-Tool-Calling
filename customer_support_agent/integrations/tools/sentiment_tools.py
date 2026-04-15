from __future__ import annotations

import functools
import json
import re

from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Keyword rules
# ---------------------------------------------------------------------------

_ESCALATION_HIGH_PATTERNS: list[str] = [
    r"charged\s+\d+\s+times?",
    r"double.?charge",
    r"triple.?charge",
    r"over.?charged",
    r"refund",
    r"fraud",
    r"scam",
    r"lawsuit",
    r"legal\s+action",
    r"nobody.{0,20}help",
    r"no\s+one.{0,20}help",
    r"unacceptable",
    r"this\s+is\s+ridiculous",
    r"absolutely\s+furious",
    r"extremely\s+frustrated",
    r"worst.{0,15}(service|support|experience)",
    r"still\s+not\s+(fixed|resolved|working)",
    r"escalat",
    r"supervisor",
    r"manager",
    r"cancel\s+(my\s+)?(account|subscription|plan)",
]

_NEGATIVE_PATTERNS: list[str] = [
    r"not\s+working",
    r"doesn'?t\s+work",
    r"broken",
    r"issue",
    r"problem",
    r"error",
    r"fail",
    r"bug",
    r"wrong",
    r"incorrect",
    r"frustrated",
    r"annoyed",
    r"disappointed",
    r"slow",
    r"outage",
    r"down\b",
    r"can'?t\s+(access|login|connect|use)",
    r"urgent",
    r"asap",
    r"immediately",
]

_POSITIVE_PATTERNS: list[str] = [
    r"thank",
    r"great",
    r"love",
    r"appreciate",
    r"perfect",
    r"excellent",
    r"awesome",
    r"happy",
    r"pleased",
    r"works\s+(now|well|great|perfectly)",
    r"resolved",
    r"fixed",
]

_NEUTRAL_PATTERNS: list[str] = [
    r"\bjust\s+check",
    r"\bchecking\b",
    r"how\s+(do|can|to)\b",
    r"question\s+about",
    r"wondering",
    r"curious",
    r"info\s+about",
    r"what\s+is\b",
    r"limits?\b",
    r"next\s+month",
    r"upcoming",
    r"plans?\s+to\b",
]


def _match_count(text: str, patterns: list[str]) -> int:
    count = 0
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            count += 1
    return count


@functools.lru_cache(maxsize=512)
def _analyze(subject: str, description: str) -> str:
    """Pure keyword-based sentiment analysis. Cached by (subject, description)."""
    combined = f"{subject}\n{description}"

    high_hits = _match_count(combined, _ESCALATION_HIGH_PATTERNS)
    neg_hits = _match_count(combined, _NEGATIVE_PATTERNS)
    pos_hits = _match_count(combined, _POSITIVE_PATTERNS)
    neutral_hits = _match_count(combined, _NEUTRAL_PATTERNS)

    # --- Determine escalation risk ---
    if high_hits >= 1:
        escalation_risk = "high"
    elif neg_hits >= 3:
        escalation_risk = "medium"
    elif neg_hits >= 1:
        escalation_risk = "low-medium"
    else:
        escalation_risk = "low"

    # --- Determine sentiment ---
    if high_hits >= 1 or (neg_hits >= 2 and neg_hits > pos_hits):
        sentiment = "negative"
        confidence = min(0.95, 0.60 + high_hits * 0.12 + neg_hits * 0.04)
    elif pos_hits > neg_hits and pos_hits >= 1:
        sentiment = "positive"
        confidence = min(0.95, 0.55 + pos_hits * 0.08)
    elif neutral_hits >= 1 and neg_hits == 0 and high_hits == 0:
        sentiment = "neutral"
        confidence = min(0.90, 0.55 + neutral_hits * 0.10)
    elif neg_hits == 1:
        sentiment = "negative"
        confidence = 0.55
    else:
        sentiment = "neutral"
        confidence = 0.50

    # --- Build human-readable summary ---
    subject_snippet = subject.strip()[:60]
    if escalation_risk == "high":
        summary = (
            f"High-urgency ticket: '{subject_snippet}'. "
            "Customer language signals strong frustration or a billing/financial concern."
        )
    elif sentiment == "negative":
        summary = (
            f"Ticket '{subject_snippet}' reflects customer dissatisfaction. "
            "Prompt, empathetic handling is advised."
        )
    elif sentiment == "positive":
        summary = (
            f"Ticket '{subject_snippet}' has a positive tone. "
            "Customer appears satisfied or grateful."
        )
    else:
        summary = (
            f"Ticket '{subject_snippet}' is routine/informational. "
            "Standard response flow is appropriate."
        )

    # --- Recommended action ---
    if escalation_risk == "high":
        recommended_action = (
            "Escalate immediately. Acknowledge the customer's frustration explicitly, "
            "offer a concrete resolution timeline, and loop in a senior agent if billing is involved."
        )
    elif escalation_risk == "medium":
        recommended_action = (
            "Prioritise above normal queue. Open with empathy, "
            "confirm the issue is being actively investigated."
        )
    elif sentiment == "positive":
        recommended_action = (
            "Acknowledge positivity, answer the question concisely."
        )
    else:
        recommended_action = (
            "Handle via standard support flow with a friendly, informative tone."
        )

    payload = {
        "tool": "analyze_ticket_sentiment",
        "sentiment": sentiment,
        "confidence": round(confidence, 2),
        "escalation_risk": escalation_risk,
        "summary": summary,
        "recommended_action": recommended_action,
        "debug": {
            "high_escalation_hits": high_hits,
            "negative_hits": neg_hits,
            "positive_hits": pos_hits,
            "neutral_hits": neutral_hits,
        },
    }
    return json.dumps(payload)


@tool
def analyze_ticket_sentiment(subject: str, description: str) -> str:
    """Analyse the emotional tone and escalation risk of a support ticket.

    Call this tool early in draft generation to understand the customer's
    emotional state before composing a reply. The result shapes tone and
    urgency of the draft:

    - **negative / high escalation** → open with strong empathy, escalate if
      billing or repeated failures are mentioned.
    - **negative / medium escalation** → empathetic, prioritised response.
    - **neutral**                     → concise, informational reply.
    - **positive**                    → friendly acknowledgement.

    Args:
        subject:     The ticket subject line (plain text, no HTML).
        description: The full ticket body written by the customer.

    Returns:
        JSON string with keys:
            sentiment         – "positive" | "neutral" | "negative"
            confidence        – float 0-1
            escalation_risk   – "low" | "low-medium" | "medium" | "high"
            summary           – one-sentence human-readable summary
            recommended_action – guidance for the support agent
    """
    return _analyze(subject, description)
