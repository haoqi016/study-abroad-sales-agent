"""Synthetic, in-memory V2 walkthrough. No model, channel, or customer I/O."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sales_agent.v2_offline_api import OfflineWorkspaceApi


FIRST_RAW = "My total budget is at most $930, and the full package feels too expensive."
SECOND_RAW = "It is not about the budget. I am unsure about the service scope, especially what essay support and school selection each include."
FIRST_BASE = "Which part of the service makes the price feel high to you?"
FIRST_DRAFT = "Which part of the service feels too expensive to you?"
FIRST_REVISED = "Understood. Which part of the service would you like to clarify first?"
FIRST_SENT = "Which part of the service would you like to clarify first?"
SECOND_BASE = "Would you like to clarify the scope of essay support or school selection first?"
SECOND_DRAFT = "I see. Would you like to start with essay support or school selection?"


def _event(workspace: dict, kind: str) -> dict:
    return next(event for event in reversed(workspace["events"]) if event["event_type"] == kind)


def _script(inbound_id: str, *, second: bool = False) -> dict:
    base = SECOND_BASE if second else FIRST_BASE
    natural = SECOND_DRAFT if second else FIRST_DRAFT
    previous = "objective-clarify-price" if second else None
    decision = {
        "normalized_meaning": ("The student says budget is not the current issue and wants to understand the scope of essay support and school selection" if second
                               else "The student states a total budget ceiling of $930 and finds the full package expensive"),
        "normalized_meaning_spans": [SECOND_RAW if second else FIRST_RAW],
        "hypotheses": ([
            {"code": "SCOPE_UNCLEAR", "confidence": 0.8,
             "supporting_spans": [SECOND_RAW], "contradicting_evidence": []},
        ] if second else [
            {"code": "BUDGET_BLOCKER", "confidence": 0.5,
             "supporting_spans": ["the full package feels too expensive"], "contradicting_evidence": []},
        ]),
        "unknowns": ["the scope of essay support and school selection"] if second else ["which service component drives the price concern"],
        "selected_strategy": {
            "strategy_code": "CLARIFY_SCOPE" if second else "CLARIFY_PRICE_CONCERN",
            "reason": "The student explicitly clarified that the issue is service scope" if second else "Clarify the concern before discussing any price change",
            "alternatives_rejected": ["Continue treating budget as the blocker"] if second else ["Negotiate price immediately"],
        },
        "previous_objective_assessment": {
            "objective_id": previous, "result": "BLOCKED" if second else None,
            "observation_event_ids": [inbound_id],
            "reason": "The student explicitly said budget is not the issue" if second else None,
        },
        "current_objective": {
            "objective_id": "objective-clarify-scope" if second else "objective-clarify-price",
            "previous_objective_id": previous,
            "goal": "Identify which service boundary needs clarification" if second else "Identify the service component behind the price concern",
            "why_now": "The student asked about essay support and school selection scope" if second else "The student mentioned a budget ceiling and full-package cost",
            "trigger_event_ids": [inbound_id], "status": "ACTIVE",
            "success_signals": ["The student names the service boundary they want to explore first"] if second else ["The student names a specific service component"],
            "failure_signals": ["The student declines further discussion"], "attempt_count": 1, "max_attempts": 2,
        },
        "action_plan": {
            "selected_action": "Ask whether to clarify essay support or school selection first" if second else "Ask which service component drives the price concern",
            "expected_observation": "The student identifies the component to clarify",
            "fallback_if_blocked": "Ask a person to verify the service boundary" if second else "Clarify the service scope instead",
        },
        "offer": {"state": "NONE", "offer_id": None, "offer_version": None, "proposal": None},
        "content_contract": {"must_include": [], "may_include": [], "must_not_include": ["discount"],
                             "semantic_draft": base, "desired_next_step": "The student answers one question",
                             "communication_emphasis": "BALANCED"},
        "evidence_ids": [], "program_claims": [], "stop_required": False,
        "handoff_reason": None, "human_approval_required": True,
        "memory_candidates": ([{"category": "PREFERENCE", "key": "service_scope_preference",
                                "value": "essay support and school selection", "epistemic_status": "CUSTOMER_STATED",
                                "source_event_ids": [inbound_id], "evidence_span": "essay support and school selection"}]
                              if second else
                              [{"category": "BUDGET", "key": "budget.total_ceiling",
                                "value": 930, "epistemic_status": "CUSTOMER_STATED",
                                "source_event_ids": [inbound_id], "evidence_span": "total budget is at most $930"}]),
    }
    return {"demo_only": True, "tool_plan": {"requests": []}, "decision": decision,
            "conversation": {"messages": [{"type": "text", "content": natural}],
                             "style_transformations": ["SHORTENED"]}}


def _require_status(result: dict, expected: str) -> None:
    actual = result["pipeline"]["status"]
    if actual != expected:
        raise RuntimeError(f"pipeline status {actual}, expected {expected}: {result['pipeline']['reason_codes']}")


def run_demo() -> dict:
    """Return event-backed trace data; every external event here is fictional."""
    with OfflineWorkspaceApi() as api:
        sid = api.createStudent({"demo_only": True, "display_name": "Demo Student A",
                                 "sales": {"stage": "PRICE_OBJECTION",
                                           "contact_permission": "ALLOWED"}})["student_id"]
        first_inbound = _event(api.recordInbound(sid, {"demo_only": True, "raw_text": FIRST_RAW}),
                               "INBOUND_RECEIVED")
        api.queue_script(sid, _script(first_inbound["event_id"]))
        first = api.requestDecision(sid)
        _require_status(first, "REVIEW_REQUIRED")
        first_draft = first["workspace"]["drafts"][-1]
        if api._store.customer_visible_history(sid) != [
            {"role": "student", "text": FIRST_RAW, "event_id": first_inbound["event_id"]}
        ]:
            raise RuntimeError("unsent draft leaked into visible history")
        api.reviewDraft(sid, {"demo_only": True, "comment": "The opening sounds stiff; please make it more natural.",
                              "feedback_type": "NATURALNESS"})
        review = _event(api.getWorkspace(sid), "REVIEW")
        api.queue_script(sid, {"demo_only": True,
                               "review_reflection": {
                                   "failure_type": "NATURALNESS",
                                   "what_was_wrong": "The opening is stiff and needs a warmer tone",
                                   "evidence": ["The opening sounds stiff"],
                                   "revision_plan": ["Rewrite the opening in natural chat language"],
                                   "applies_to": "CURRENT_DRAFT"},
                               "conversation": {"messages": [{"type": "text", "content": FIRST_REVISED}],
                                                "style_transformations": ["ADDED_EMPATHY"]}})
        revision = api.requestDecision(sid)
        _require_status(revision, "REVIEW_REQUIRED")
        revised_draft = revision["workspace"]["drafts"][-1]
        approved_workspace = api.approveDraft(sid)
        approval = _event(approved_workspace, "REVIEW")
        # Fictional operator assertion for the offline exercise. This API records only;
        # it has no transport and cannot itself establish a real external send.
        api.recordActualSent(sid, {"demo_only": True, "confirmed_external_send": True,
                                   "actual_sent_text": FIRST_SENT})
        sent = _event(api.getWorkspace(sid), "HUMAN_SENT")
        second_inbound = _event(api.recordInbound(sid, {"demo_only": True, "raw_text": SECOND_RAW}),
                                "INBOUND_RECEIVED")
        api.queue_script(sid, _script(second_inbound["event_id"], second=True))
        second = api.requestDecision(sid)
        _require_status(second, "REVIEW_REQUIRED")
        second_draft = second["workspace"]["drafts"][-1]
        events = second["workspace"]["events"]
        contexts = {e["event_id"]: e for e in events if e["event_type"] == "TURN_CONTEXT_BUILT"}
        decisions = {e["event_id"]: e for e in events if e["event_type"] == "DECISION_READY"}
        gates = [e for e in events if e["event_type"] == "GATE_RESULT"]
        trace = {
            "student_id": sid, "events": events,
            "visible_history": api._store.customer_visible_history(sid),
            "memory": api._store.get_student_memory(sid),
            "turns": [
                {"inbound": first_inbound, "context": contexts[first["pipeline"]["context_event_id"]],
                 "decision": decisions[first["pipeline"]["decision_event_id"]],
                 "drafts": [first_draft, revised_draft], "review": review,
                 "approval": approval,
                 "sent": sent, "next_inbound": second_inbound},
                {"inbound": second_inbound, "context": contexts[second["pipeline"]["context_event_id"]],
                 "decision": decisions[second["pipeline"]["decision_event_id"]],
                 "drafts": [second_draft], "review": None, "approval": None,
                 "sent": None, "next_inbound": None},
            ],
            "gates": gates,
        }
        return trace


def render_markdown(trace: dict) -> str:
    events = trace["events"]
    gates = trace["gates"]
    lines = ["# Sales V2 synthetic offline end-to-end trace", "",
             "All student messages, human actions, and external-send confirmations in this example are scripted. No live model, customer channel, or send API is called. HUMAN_SENT records a fictional operator assertion locally; it does not prove a real message was sent or that any sales outcome occurred.", ""]
    for number, turn in enumerate(trace["turns"], 1):
        inbound = turn["inbound"]
        context = turn["context"]["payload"]
        decision = turn["decision"]["payload"]
        objective = decision["current_objective"]
        memory = {item["memory_id"]: item for item in trace["memory"]["items"]}
        lines += [f"## Turn {number}", "",
                  f"- Student statement (synthetic; INBOUND_RECEIVED `{inbound['event_id']}`): {inbound['payload']['raw_text']}",
                  f"- Prior objective assessment: `{decision['previous_objective_assessment']['result']}`; {decision['previous_objective_assessment']['reason'] or 'No prior objective on the first turn'}.",
                  "- Internal hypotheses: " + "; ".join(f"{h['code']}={h['confidence']}" for h in decision["hypotheses"]) + ".",
                  f"- Strategy rationale: {decision['selected_strategy']['reason']}; rejected: {', '.join(decision['selected_strategy']['alternatives_rejected'])}.",
                  f"- Current objective `{objective['objective_id']}`: {objective['goal']}; success signal: {', '.join(objective['success_signals'])}; action: {decision['action_plan']['selected_action']}.",
                  f"- Context: profile revision {context['student_snapshot_revision']}, memory revision {context['student_memory_revision']}, goal version `{context['global_goal']['version']}`, policy versions {', '.join(context['policy_versions'])}.",
                  f"- Working memory (rebuilt from events): previous objective `{context['working_memory']['active_objective_id']}`; prior hypotheses {context['working_memory']['current_hypotheses']}; open questions {context['working_memory']['open_questions']}; pending approvals {context['working_memory']['pending_approvals']}.",
                  f"- Customer-visible history event IDs: {', '.join(context['customer_visible_history_event_ids'])}; latest observation IDs: {', '.join(context['latest_observation_event_ids'])}.",
                  f"- Mandatory long-term memory IDs: {', '.join(context['mandatory_memory_ids']) or 'none'}; relevant IDs: {', '.join(context['relevant_memory_ids']) or 'none'}; omitted noncritical items: {context['context_manifest']['omitted_noncritical_memory_count']}."]
        for mid in context["mandatory_memory_ids"] + context["relevant_memory_ids"]:
            item = memory[mid]
            lines.append(f"  - `{mid}` {item['key']}={item['value']} ({item['epistemic_status']}; source event {', '.join(item['source_event_ids'])}; exact source span \"{item['evidence_span']}\").")
        lines += [f"- Base reply (Decision content contract; unsent): {decision['content_contract']['semantic_draft']}",
                  f"- Naturalized draft (Conversation; unsent): {turn['drafts'][0]['text']}"]
        for draft_index, draft in enumerate(turn["drafts"]):
            related = [g["payload"] for g in gates if g["payload"]["subject_event_id"] == draft["draft_id"]]
            if len(related) != 1:
                raise RuntimeError("draft POST_CONVERSATION Gate missing or ambiguous")
            gate = related[0]
            draft_label = "Initial draft" if draft_index == 0 else f"Revision {draft_index}"
            lines.append(f"- {draft_label} Gate `{draft['draft_id']}`: POST_CONVERSATION passed={gate['passed']}, action={gate['required_action']}; draft status at creation {draft['status']}.")
        pre = [g["payload"] for g in gates if g["payload"]["subject_event_id"] == turn["decision"]["event_id"]]
        if len(pre) != 1:
            raise RuntimeError("decision PRE_CONVERSATION Gate missing or ambiguous")
        lines.append(f"- Decision Gate: PRE_CONVERSATION passed={pre[0]['passed']}, action={pre[0]['required_action']}; adopted evidence IDs: {decision['evidence_ids'] or 'none (no institutional evidence retrieved)'}.")
        if turn["review"]:
            lines += [f"- Human rejection (internal REVIEW; invisible to student): {turn['review']['payload']['comment']}",
                      f"- Revised draft (unsent): {turn['drafts'][-1]['text']}",
                      f"- Human approval (REVIEW `{turn['approval']['event_id']}`): approved only the revised draft; approval is not a send event."]
        else:
            lines.append("- Approval: pending human approval; the draft remains unsent.")
        if turn["sent"]:
            sent = turn["sent"]
            lines.append(f"- Actual sent text field (fictional operator assertion; HUMAN_SENT `{sent['event_id']}`): {sent['payload']['actual_sent_text']}; diff from approved draft: {sent['payload']['diff_from_approved']}.")
        else:
            lines.append("- Actual sent text: no HUMAN_SENT event.")
        if turn["next_inbound"]:
            next_inbound = turn["next_inbound"]
            lines.append(f"- Next student reply (synthetic INBOUND_RECEIVED `{next_inbound['event_id']}`): {next_inbound['payload']['raw_text']}")
        else:
            lines.append("- Next student reply: none; this turn ends at an unsent draft.")
        created = [e["payload"] for e in events if e["event_type"] == "STUDENT_MEMORY_ITEM"
                   and inbound["event_id"] in e["payload"]["source_event_ids"]]
        lines.append("- Long-term memory written this turn: " + ("; ".join(f"{x['key']}={x['value']}, source `{inbound['event_id']}`" for x in created) or "none") + ".")
        lines.append("")
    lines += ["## Boundaries", "", "This trace validates only the synthetic event flow, state distinctions, and source binding. It does not validate model decision quality, real student reactions, or sales conversion. No institutional source verified the service scope in turn two, so the draft asks a clarifying question and makes no delivery claim.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a synthetic Sales V2 event trace as English Markdown")
    parser.add_argument("--output", type=Path, help="write Markdown to this path; default: stdout")
    args = parser.parse_args()
    markdown = render_markdown(run_demo())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
        print(args.output)
    else:
        print(markdown)


if __name__ == "__main__":
    main()
