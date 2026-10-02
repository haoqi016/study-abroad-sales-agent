"""Synthetic, in-memory V2 walkthrough. No model, channel, or customer I/O."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sales_agent.v2_offline_api import OfflineWorkspaceApi


FIRST_RAW = "我总预算最多15000元，感觉全套费用有点高。"
SECOND_RAW = "不是预算问题，是不清楚服务范围，尤其文书和选校分别做什么。"
FIRST_BASE = "你最担心费用对应的哪部分服务？"
FIRST_DRAFT = "你最担心费用对应哪部分服务呀？"
FIRST_REVISED = "明白。你现在最想弄清哪部分服务？"
FIRST_SENT = "你现在最想弄清哪部分服务？"
SECOND_BASE = "你更想先了解文书还是选校的服务边界？"
SECOND_DRAFT = "收到。你想先了解文书还是选校的服务边界？"


def _event(workspace: dict, kind: str) -> dict:
    return next(event for event in reversed(workspace["events"]) if event["event_type"] == kind)


def _script(inbound_id: str, *, second: bool = False) -> dict:
    base = SECOND_BASE if second else FIRST_BASE
    natural = SECOND_DRAFT if second else FIRST_DRAFT
    previous = "objective-clarify-price" if second else None
    decision = {
        "normalized_meaning": ("学生否认预算是当前问题，想了解文书和选校服务范围" if second
                               else "学生说明总预算最多15000元，认为全套费用偏高"),
        "normalized_meaning_spans": [SECOND_RAW if second else FIRST_RAW],
        "hypotheses": ([
            {"code": "SCOPE_UNCLEAR", "confidence": 0.8,
             "supporting_spans": [SECOND_RAW], "contradicting_evidence": []},
        ] if second else [
            {"code": "BUDGET_BLOCKER", "confidence": 0.5,
             "supporting_spans": ["感觉全套费用有点高"], "contradicting_evidence": []},
        ]),
        "unknowns": ["文书与选校服务边界"] if second else ["费用顾虑对应的服务环节"],
        "selected_strategy": {
            "strategy_code": "CLARIFY_SCOPE" if second else "CLARIFY_PRICE_CONCERN",
            "reason": "学生明确纠正为范围不清" if second else "先澄清顾虑所指，不提出折价",
            "alternatives_rejected": ["继续按预算障碍推进"] if second else ["直接议价"],
        },
        "previous_objective_assessment": {
            "objective_id": previous, "result": "BLOCKED" if second else None,
            "observation_event_ids": [inbound_id],
            "reason": "学生明确说不是预算问题" if second else None,
        },
        "current_objective": {
            "objective_id": "objective-clarify-scope" if second else "objective-clarify-price",
            "previous_objective_id": previous,
            "goal": "确认需要澄清的服务边界" if second else "确认费用顾虑对应的服务环节",
            "why_now": "学生询问文书和选校范围" if second else "学生提到总预算和全套费用",
            "trigger_event_ids": [inbound_id], "status": "ACTIVE",
            "success_signals": ["学生指出先想了解的服务边界"] if second else ["学生指出具体服务环节"],
            "failure_signals": ["学生拒绝继续讨论"], "attempt_count": 1, "max_attempts": 2,
        },
        "action_plan": {
            "selected_action": "询问优先澄清文书还是选校" if second else "询问费用顾虑对应的环节",
            "expected_observation": "学生指出需要澄清的环节",
            "fallback_if_blocked": "交由人工核实服务边界" if second else "改为澄清服务范围",
        },
        "offer": {"state": "NONE", "offer_id": None, "offer_version": None, "proposal": None},
        "content_contract": {"must_include": [], "may_include": [], "must_not_include": ["折价"],
                             "semantic_draft": base, "desired_next_step": "学生回答一个问题",
                             "communication_emphasis": "BALANCED"},
        "evidence_ids": [], "program_claims": [], "stop_required": False,
        "handoff_reason": None, "human_approval_required": True,
        "memory_candidates": ([{"category": "PREFERENCE", "key": "service_scope_preference",
                                "value": "文书和选校", "epistemic_status": "CUSTOMER_STATED",
                                "source_event_ids": [inbound_id], "evidence_span": "文书和选校"}]
                              if second else
                              [{"category": "BUDGET", "key": "budget.total_ceiling",
                                "value": 15000, "epistemic_status": "CUSTOMER_STATED",
                                "source_event_ids": [inbound_id], "evidence_span": "总预算最多15000元"}]),
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
        sid = api.createStudent({"demo_only": True, "display_name": "合成学生甲",
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
        api.reviewDraft(sid, {"demo_only": True, "comment": "第一句有点生硬，请改得自然一点",
                              "feedback_type": "NATURALNESS"})
        review = _event(api.getWorkspace(sid), "REVIEW")
        api.queue_script(sid, {"demo_only": True,
                               "review_reflection": {
                                   "failure_type": "NATURALNESS",
                                   "what_was_wrong": "首句生硬，需调整语气",
                                   "evidence": ["第一句有点生硬"],
                                   "revision_plan": ["将首句改成自然的微信表达"],
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
    lines = ["# Sales V2 合成离线端到端 Trace", "",
             "本示例全部学生消息、人工动作及‘外部发送确认’都是脚本合成。脚本没有调用真实模型、客户渠道或发送接口；HUMAN_SENT 只是对合成操作的本地记录，不证明发生真实发送或产生销售效果。", ""]
    for number, turn in enumerate(trace["turns"], 1):
        inbound = turn["inbound"]
        context = turn["context"]["payload"]
        decision = turn["decision"]["payload"]
        objective = decision["current_objective"]
        memory = {item["memory_id"]: item for item in trace["memory"]["items"]}
        lines += [f"## 第 {number} 轮", "",
                  f"- 学生原话（合成，INBOUND_RECEIVED `{inbound['event_id']}`）：{inbound['payload']['raw_text']}",
                  f"- 上轮目标评估：`{decision['previous_objective_assessment']['result']}`；{decision['previous_objective_assessment']['reason'] or '首轮无上一目标'}。",
                  f"- 内部假设：" + "；".join(f"{h['code']}={h['confidence']}" for h in decision["hypotheses"]),
                  f"- 策略取舍：{decision['selected_strategy']['reason']}；未选：{'、'.join(decision['selected_strategy']['alternatives_rejected'])}。",
                  f"- 当前小目标 `{objective['objective_id']}`：{objective['goal']}；成功信号：{'、'.join(objective['success_signals'])}；动作：{decision['action_plan']['selected_action']}。",
                  f"- Context：档案修订 {context['student_snapshot_revision']}，记忆修订 {context['student_memory_revision']}，目标版本 `{context['global_goal']['version']}`，政策版本 {', '.join(context['policy_versions'])}。",
                  f"- 工作记忆（本轮从事件重建）：上一目标 `{context['working_memory']['active_objective_id']}`；旧假设 {context['working_memory']['current_hypotheses']}；未决问题 {context['working_memory']['open_questions']}；待批事项 {context['working_memory']['pending_approvals']}。",
                  f"- 学生可见历史事件：{', '.join(context['customer_visible_history_event_ids'])}；最新观察：{', '.join(context['latest_observation_event_ids'])}。",
                  f"- 长期记忆必备 ID：{', '.join(context['mandatory_memory_ids']) or '无'}；相关 ID：{', '.join(context['relevant_memory_ids']) or '无'}；遗漏非关键项：{context['context_manifest']['omitted_noncritical_memory_count']}。"]
        for mid in context["mandatory_memory_ids"] + context["relevant_memory_ids"]:
            item = memory[mid]
            lines.append(f"  - `{mid}` {item['key']}={item['value']}（{item['epistemic_status']}；来源事件 {', '.join(item['source_event_ids'])}；原话片段“{item['evidence_span']}”）。")
        lines += [f"- 基础回复（Decision 内容合同，未发送）：{decision['content_contract']['semantic_draft']}",
                  f"- 自然化草稿（Conversation，未发送）：{turn['drafts'][0]['text']}"]
        for draft_index, draft in enumerate(turn["drafts"]):
            related = [g["payload"] for g in gates if g["payload"]["subject_event_id"] == draft["draft_id"]]
            if len(related) != 1:
                raise RuntimeError("draft POST_CONVERSATION Gate missing or ambiguous")
            gate = related[0]
            draft_label = "初稿" if draft_index == 0 else f"第 {draft_index} 次修订稿"
            lines.append(f"- {draft_label} Gate `{draft['draft_id']}`：POST_CONVERSATION passed={gate['passed']}，action={gate['required_action']}；生成时草稿状态 {draft['status']}。")
        pre = [g["payload"] for g in gates if g["payload"]["subject_event_id"] == turn["decision"]["event_id"]]
        if len(pre) != 1:
            raise RuntimeError("decision PRE_CONVERSATION Gate missing or ambiguous")
        lines.append(f"- 决策 Gate：PRE_CONVERSATION passed={pre[0]['passed']}，action={pre[0]['required_action']}；采用证据 ID：{decision['evidence_ids'] or '无（未检索机构资料）'}。")
        if turn["review"]:
            lines += [f"- 人工打回（内部 REVIEW，学生不可见）：{turn['review']['payload']['comment']}",
                      f"- 修订草稿（未发送）：{turn['drafts'][-1]['text']}",
                      f"- 人工批准（REVIEW `{turn['approval']['event_id']}`）：仅批准修订草稿；批准本身不等于发送。"]
        else:
            lines.append("- 审批：尚未人工批准；草稿未发送。")
        if turn["sent"]:
            sent = turn["sent"]
            lines.append(f"- 实际发送内容字段（合成操作员声明，HUMAN_SENT `{sent['event_id']}`）：{sent['payload']['actual_sent_text']}；与批准稿比较：{sent['payload']['diff_from_approved']}。")
        else:
            lines.append("- 实际发送内容：无 HUMAN_SENT 事件。")
        if turn["next_inbound"]:
            next_inbound = turn["next_inbound"]
            lines.append(f"- 下一轮学生回复（合成 INBOUND_RECEIVED `{next_inbound['event_id']}`）：{next_inbound['payload']['raw_text']}")
        else:
            lines.append("- 下一轮学生回复：无；本轮到未发送草稿为止。")
        created = [e["payload"] for e in events if e["event_type"] == "STUDENT_MEMORY_ITEM"
                   and inbound["event_id"] in e["payload"]["source_event_ids"]]
        lines.append("- 本轮写入长期记忆：" + ("；".join(f"{x['key']}={x['value']}，来源 `{inbound['event_id']}`" for x in created) or "无") + "。")
        lines.append("")
    lines += ["## 边界", "", "本 Trace 只验证合成事件流、状态区分和来源绑定；不验证模型决策质量、真实客户反应或销售转化。第二轮的服务范围尚未由机构证据核实，因此只提澄清问题，没有对客陈述交付事实。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a synthetic Sales V2 event trace as Chinese Markdown")
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
