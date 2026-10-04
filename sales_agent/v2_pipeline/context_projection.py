"""Source-linked, bounded context views for synthetic Sales V2 model calls.

The topic index is a navigation aid, not a summary of customer statements.
Only customer-visible history can enter the recent message window. Drafts and
internal reviews remain outside that window.
"""

from __future__ import annotations

import re
from typing import Any


HISTORY_MAX_MESSAGES = 16
HISTORY_TEXT_CHAR_BUDGET = 3000  # Approximate budget, not a tokenizer limit.
OLDER_HISTORY_INDEX_LIMIT = 8

_TOPICS = (
    ("PRICE_OR_PAYMENT", re.compile(
        r"\b(?:price|pricing|quote|quoted|budget|cost|fee|payment|pay|deposit|"
        r"contract|sign|installments?)\b|(?:[$£€]\s*\d)", re.I)),
    ("SERVICE_SCOPE", re.compile(
        r"\b(?:service|scope|support|essay|personal statement|school selection|"
        r"application|submission|documents?)\b", re.I)),
    ("TIMING", re.compile(
        r"\b(?:deadline|timing|timeline|when|next week|tomorrow|this month|"
        r"intake|start date)\b", re.I)),
    ("TARGET", re.compile(
        r"\b(?:university|college|school|program(?:me)?|major|degree|"
        r"Singapore|Hong Kong|United Kingdom|Australia|NTU|NUS)\b", re.I)),
)
_KEY_STUDENT_SIGNAL = re.compile(
    r"\b(?:ready to (?:sign|pay|enrol|enroll)|(?:sign|pay|decide) today|"
    r"if (?:the )?price (?:works|is right|is acceptable)|"
    r"(?:please )?(?:do not|don't|stop) (?:contact(?:ing)?|message|messaging|text(?:ing)?) me|"
    r"(?:budget|deposit|first payment)\b.{0,18}(?:[$£€]\s*)?\d+|"
    r"(?:sign|review) (?:the )?contract)\b", re.I,
)
_TOPIC_LABEL = {
    "PRICE_OR_PAYMENT": "price, payment, or contract",
    "SERVICE_SCOPE": "service scope or application work",
    "TIMING": "timing or application deadline",
    "TARGET": "target institution or program",
}


def _topics(text: str) -> list[str]:
    return [code for code, pattern in _TOPICS if pattern.search(text)]


def _excerpt(text: str, limit: int = 450) -> dict[str, Any]:
    return {"text": text[:limit], "truncated": len(text) > limit}


def _signal_quote(text: str) -> dict[str, Any] | None:
    match = _KEY_STUDENT_SIGNAL.search(text)
    if match is None:
        return None
    start = max(0, match.start() - 55)
    end = min(len(text), match.end() + 85)
    return {"exact_text": text[start:end], "truncated": start > 0 or end < len(text)}


def project_customer_history(
    visible_history: list[dict], *, latest_student_text: str,
    max_messages: int = HISTORY_MAX_MESSAGES,
    text_char_budget: int = HISTORY_TEXT_CHAR_BUDGET,
) -> dict:
    """Keep a contiguous recent tail, then index older source events.

    The newest message is never clipped, even when it exceeds the budget.
    A long preceding message stops the tail rather than being skipped to pick
    older, shorter messages out of order.
    """
    if max_messages < 1 or text_char_budget < 1:
        raise ValueError("invalid_history_projection_limit")
    selected_reversed: list[dict] = []
    used_chars = 0
    for item in reversed(visible_history):
        if item.get("role") not in {"student", "sales"}:
            raise ValueError("non_customer_visible_history_item")
        if len(selected_reversed) >= max_messages:
            break
        message = item["text"]
        if selected_reversed and used_chars + len(message) > text_char_budget:
            break
        selected_reversed.append(item)
        used_chars += len(message)
    recent = list(reversed(selected_reversed))
    older = visible_history[:len(visible_history) - len(recent)]
    latest_topics = set(_topics(latest_student_text))
    ranked = []
    for position, item in enumerate(older):
        topics = _topics(item["text"])
        if topics:
            ranked.append((bool(latest_topics.intersection(topics)), position, item, topics))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    chosen = sorted(ranked[:OLDER_HISTORY_INDEX_LIMIT], key=lambda row: row[1])
    older_index = [{
        "source_event_id": item["event_id"],
        "role": item["role"],
        "topic_codes": topics,
        "summary": ("Student previously mentioned " if item["role"] == "student"
                    else "Salesperson actually sent content about ")
                   + ", ".join(_TOPIC_LABEL[topic] for topic in topics),
        "summary_kind": "DETERMINISTIC_TOPIC_INDEX_NOT_VERBATIM",
    } for _, _, item, topics in chosen]
    key_quotes = [{"source_event_id": item["event_id"], **quote}
                  for item in reversed(older) if item["role"] == "student"
                  if (quote := _signal_quote(item["text"])) is not None][:2]
    key_quotes.reverse()
    return {
        "customer_visible_history": recent,
        "older_history_index": older_index,
        "older_key_original_quotes": key_quotes,
        "history_projection": {
            "max_recent_messages": max_messages,
            "text_char_budget": text_char_budget,
            "recent_count": len(recent),
            "older_count": len(older),
            "older_unindexed_count": len(older) - len(older_index),
            "truncated_customer_history": bool(older),
            "latest_message_kept_exactly": bool(recent) and recent[-1]["text"] == latest_student_text,
        },
    }


def build_commercial_decision_view(
    *, context: dict, all_events: list[dict], memory_items: list[dict],
    active_outcome_ids: set[str],
) -> dict:
    """Separate observed commercial events from a previous model hypothesis."""
    by_id = {event["event_id"]: event for event in all_events}
    sent = [event for event in all_events if event["event_type"] == "HUMAN_SENT"]
    quote_memories = [item for item in memory_items
                      if item.get("active") and item.get("category") == "OFFER_HISTORY"]
    actual_quotes = []
    for item in quote_memories[-3:]:
        value = item["value"]
        sent_ids = item.get("source_event_ids", [])
        sent_id = sent_ids[0] if sent_ids else None
        if sent_id not in by_id or by_id[sent_id]["event_type"] != "HUMAN_SENT":
            continue
        actual_quotes.append({
            "source_sent_event_id": sent_id,
            "price": value.get("price"),
            "price_status": value.get("price_status", "UNKNOWN"),
            "currency": value.get("currency"),
            "payment_terms": value.get("payment_terms", []),
            "actual_sent_content": _excerpt(value["actual_sent_text"]),
        })
    linked_replies = []
    for event in all_events:
        if event["event_type"] != "PROGRESS_ASSESSMENT":
            continue
        sent_id = event["payload"].get("reply_to_sent_event_id")
        inbound_id = event["payload"].get("inbound_event_id")
        inbound = by_id.get(inbound_id)
        if (not sent_id or sent_id not in by_id or by_id[sent_id]["event_type"] != "HUMAN_SENT"
                or inbound is None or inbound["event_type"] != "INBOUND_RECEIVED"):
            continue
        linked_replies.append({
            "source_inbound_event_id": inbound_id,
            "reply_to_actual_sent_event_id": sent_id,
            "student_raw": _excerpt(inbound["payload"]["raw_text"]),
            "link_status": "RECORDED_LINK_NOT_CAUSAL_PROOF",
        })
    outcomes = [{
        "source_event_id": event["event_id"],
        "outcome_type": event["payload"]["outcome_type"],
        "amount": event["payload"].get("amount"),
        "currency": event["payload"].get("currency"),
        "verification_status": event["payload"].get("verification_status", "UNKNOWN"),
        "source_type": event["payload"].get("source_type"),
    } for event in all_events if event["event_type"] == "COMMERCIAL_OUTCOME"
        and event["event_id"] in active_outcome_ids][-5:]
    last_decision = next((event for event in reversed(all_events)
                          if event["event_type"] == "DECISION_READY"), None)
    working = context["working_memory"]
    prior = None
    if last_decision:
        strategy = last_decision["payload"].get("selected_strategy", {})
        sent_ids = working.get("previous_customer_message_sent_event_ids", [])
        prior = {
            "source_decision_event_id": last_decision["event_id"],
            "epistemic_status": "AGENT_HYPOTHESIS_UNVERIFIED",
            "assumed_purchase_blocker": strategy.get("purchase_blocker", "UNKNOWN"),
            "intended_customer_action": strategy.get("next_milestone") or
                                        last_decision["payload"].get("content_contract", {}).get("desired_next_step"),
            "why_now": last_decision["payload"].get("current_objective", {}).get("why_now"),
            "previous_goal": last_decision["payload"].get("current_objective", {}).get("goal"),
            "actual_sent_event_ids_for_decision": sent_ids,
            "student_observation_event_id": context.get("latest_student_message_event_id"),
            "outcome_assessment": "UNKNOWN",
        }
        matching = [event for event in all_events if event["event_type"] == "PROGRESS_ASSESSMENT"
                    and event["payload"].get("inbound_event_id") == context.get("latest_student_message_event_id")
                    and event["payload"].get("reply_to_sent_event_id") in sent_ids]
        if matching:
            assessment = matching[-1]
            prior["outcome_assessment"] = assessment["payload"]["predicted_signal"]
            prior["outcome_assessment_source_event_id"] = assessment["event_id"]
            prior["outcome_assessment_epistemic_status"] = "AGENT_ASSESSMENT_UNVERIFIED"
    latest_sent = sent[-1] if sent else None
    return {
        "schema_version": "sales.commercial-decision-view.v0",
        "observed_facts": {
            "latest_student_message": {
                "source_event_id": context.get("latest_student_message_event_id"),
                "raw_text": context.get("latest_student_raw_text"),
            },
            "latest_actual_sent": ({"source_event_id": latest_sent["event_id"],
                                    "actual_sent_content": _excerpt(latest_sent["payload"]["actual_sent_text"])}
                                   if latest_sent else None),
            "actual_sent_quotes": actual_quotes,
            "customer_replies_with_recorded_link": linked_replies[-3:],
            "commercial_outcomes": outcomes,
        },
        "previous_working_judgment": prior,
        "questions_for_this_turn": [
            "What does the student's exact statement show about purchase readiness? Mark uncertainty explicitly.",
            "What is the nearest unresolved blocker to a voluntary next commitment, and what remains a hypothesis?",
            "Which single voluntary action is appropriate to invite in this turn?",
        ],
    }


def conversation_goal_from_decision(decision: dict) -> dict:
    """Pass the contract's proposed step without granting new authority."""
    objective = decision.get("current_objective", {})
    contract = decision.get("content_contract", {})
    next_step = contract.get("desired_next_step")
    return {
        "current_objective": objective.get("goal"),
        "why_now": objective.get("why_now"),
        "desired_customer_action": next_step,
        "contract_next_step_pre_review": next_step,
        "instruction_status": "PRE_HUMAN_REVIEW_INTERNAL_GUIDANCE_NOT_CUSTOMER_FACT",
    }
