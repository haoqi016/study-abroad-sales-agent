"""Optional, fact-free presentation cues for the Conversation Agent.

This module selects fixed English labels. It never copies a student message,
Decision text, evidence, pricing, or an example reply into its output. The
pipeline must keep its material policy gate when this optional cue is used.
"""

from __future__ import annotations

import re
from typing import Any


STYLE_VERSION = "conversation-style-shape.public.v1"

_SHAPES = {
    "EXIT": ("low_pressure", "one_or_two", "optional_existing_question",
             "Respect the student's pause or refusal. Keep the approved reply brief."),
    "EVIDENCE": ("transparent", "two_or_three", "at_most_one_existing_question",
                 "State uncertainty plainly and use only approved evidence in the content contract."),
    "RELATIONSHIP": ("warm", "two_or_three", "at_most_one_existing_question",
                     "Briefly acknowledge a concern the student actually stated, then give the approved answer."),
    "DIRECT": ("direct", "two_or_three", "at_most_one_existing_question",
               "Acknowledge the question briefly, then explain the approved concrete benefit."),
    "PAYMENT": ("clear", "two_or_three", "at_most_one_existing_question",
                "Answer the payment question clearly without adding a new term or commitment."),
    "BUDGET": ("clear", "two_or_three", "at_most_one_existing_question",
               "Acknowledge the budget concern, then explain only the approved value and next step."),
    "DEFAULT": ("plain", "two_or_three", "at_most_one_existing_question",
                "Use a brief natural acknowledgment before the approved answer."),
}

_NO_REASSURANCE = re.compile(
    r"\b(?:do\s+not|don't|no|skip)\s+(?:the\s+)?(?:reassur(?:e|ance)|comfort|empathy|empathize)\b|"
    r"\b(?:just|only)\s+(?:give|tell|show)\s+me\s+(?:the\s+)?(?:facts|details|specifics)\b",
    re.IGNORECASE,
)
_PAYMENT = re.compile(r"\b(?:pay(?:ment)?|installments?|deposit|first\s+payment)\b", re.IGNORECASE)
_BUDGET = re.compile(r"\b(?:budget|price|pricing|cost|fee|quote)\b", re.IGNORECASE)


def select_style_shape(decision: dict[str, Any], student_raw_text: str = "") -> dict[str, str]:
    """Choose fixed expression cues without moving any source text into output.

    The cue may change tone and message rhythm only. It is not a source for a
    claim, a new question, or a different customer objective.
    """
    content = decision.get("content_contract", {}) if isinstance(decision, dict) else {}
    content = content if isinstance(content, dict) else {}
    semantic_draft = content.get("semantic_draft", "")
    semantic_draft = semantic_draft if isinstance(semantic_draft, str) else ""
    student_raw_text = student_raw_text if isinstance(student_raw_text, str) else ""
    emphasis = content.get("communication_emphasis")
    strategy = decision.get("selected_strategy", {}) if isinstance(decision, dict) else {}
    strategy = strategy if isinstance(strategy, dict) else {}
    strategy_code = strategy.get("strategy_code")

    if strategy_code in {"STOP", "LOW_PRESSURE_EXIT", "PAUSE"}:
        key = "EXIT"
    elif strategy_code in {"VERIFY_EVIDENCE", "HANDOFF_EVIDENCE"}:
        key = "EVIDENCE"
    elif emphasis in {"RELATIONAL_REASSURANCE", "RELATIONSHIP_REASSURANCE"}:
        key = "RELATIONSHIP"
    elif emphasis in {"TRANSACTIONAL_VALUE", "CONCRETE_BENEFITS"}:
        key = "DIRECT"
    elif _PAYMENT.search(student_raw_text) and _PAYMENT.search(semantic_draft):
        key = "PAYMENT"
    elif _BUDGET.search(student_raw_text) and _BUDGET.search(semantic_draft):
        key = "BUDGET"
    else:
        key = "DEFAULT"

    tone, bubble_rhythm, question_pacing, rapport_move = _SHAPES[key]
    if key != "EXIT" and _NO_REASSURANCE.search(student_raw_text):
        rapport_move = "Skip reassurance. Answer the stated question directly using only approved content."
    return {
        "style_version": STYLE_VERSION,
        "style_id": key.lower(),
        "tone": tone,
        "bubble_rhythm": bubble_rhythm,
        "question_pacing": question_pacing,
        "rapport_move": rapport_move,
    }
