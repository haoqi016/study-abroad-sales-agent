"""Synthetic development examples for the optional offline Gold exercise.

These examples are separate from evaluation cases. They supply an internal
comparison and writing shape, never facts, prices, approvals, or permissions.
"""

from __future__ import annotations

import re
from copy import deepcopy

VERSION = "public-synthetic-gold.v1"
MODE = "FICTIONAL_STUDENT_OFFLINE_HOMEWORK"

_EXAMPLES = (
    {"gold_id": "PUBLIC-SCOPE-01", "kind": "decision", "neutral_situation": "Student asks what support includes", "cues": ("what do you do", "what is included", "help with", "scope"), "suggested_action": "ANSWER_SCOPE", "decision_move": "Answer the stated scope question using verified service facts, then ask one relevant question."},
    {"gold_id": "PUBLIC-BUDGET-01", "kind": "decision", "neutral_situation": "Student states a budget concern", "cues": ("budget", "expensive", "price", "cost"), "suggested_action": "CLARIFY", "decision_move": "Acknowledge the stated constraint; check the actual offer history before discussing any change."},
    {"gold_id": "PUBLIC-LANGUAGE-01", "kind": "language", "neutral_situation": "Short, direct explanation", "cues": (), "writing_shape": "Acknowledge the exact question briefly. State only verified facts in plain language. End with one optional next step."},
)


def catalog(kind: str) -> dict:
    if kind not in {"decision", "language"}:
        raise ValueError("invalid_gold_kind")
    return {"version": VERSION, "split": "development", "source_type": "SYNTHETIC_PUBLIC_EXAMPLE",
            "entries": [{key: deepcopy(value) for key, value in item.items() if key != "cues"}
                        for item in _EXAMPLES if item["kind"] == kind]}


def select(kind: str, student_text: str) -> dict:
    if not isinstance(student_text, str):
        raise ValueError("student_text_required")
    candidates = [item for item in _EXAMPLES if item["kind"] == kind]
    match = next((item for item in candidates if item["cues"] and
                  any(re.search(r"\b" + re.escape(cue) + r"\b", student_text, re.I)
                      for cue in item["cues"])), None)
    if kind == "language":
        match = candidates[0]
    if match is None:
        return {"status": "NO_MATCH" if kind == "decision" else "NO_LANGUAGE_MATCH",
                "gold_ids": [], "gold_id": None, "reason": "No applicable public synthetic example"}
    return {"status": "MATCH", "gold_ids": [match["gold_id"]], "gold_id": match["gold_id"],
            "source_version": VERSION, "reason": "Matched a neutral synthetic development situation"}


def get(gold_id: str, kind: str) -> dict:
    match = next((item for item in _EXAMPLES if item["gold_id"] == gold_id and item["kind"] == kind), None)
    if match is None:
        raise ValueError("unknown_public_gold_id")
    return {key: deepcopy(value) for key, value in match.items() if key != "cues"}


def style_review(messages: list[dict], language_example: dict) -> dict:
    """Check a few concrete risks; this is not a similarity score or approval."""
    issues = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("type") != "text":
            continue
        content = message.get("content", "")
        if not isinstance(content, str):
            continue
        if index == 0 and re.match(r"\s*(regarding your question|i will now explain|please note)", content, re.I):
            issues.append({"code": "FORMAL_OPENING", "location": "messages.0",
                           "instruction": "Start with a short, direct acknowledgment of the student's question."})
        if len(content) > 180:
            issues.append({"code": "LONG_MESSAGE", "location": f"messages.{index}",
                           "instruction": "Split the message while keeping every factual limit and condition."})
    return {"version": "public-style-risk.v1", "status": "REVISE" if issues else "PASS",
            "gold_id": language_example["gold_id"], "issues": issues}
