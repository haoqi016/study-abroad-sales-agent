"""Deterministic Agent-side gates for the frozen V2 price-objection slice.

These gates do not judge sales effectiveness and do not constrain the human's
eventual edit. They deliberately refuse model output that cannot be verified.
"""

from __future__ import annotations

import re
from typing import Any


class PolicyViolation(ValueError):
    def __init__(self, *codes: str, diagnostic_code: str | None = None):
        self.codes = list(codes)
        # Fixed-rule metadata only; never include matched customer/model text.
        self.diagnostic_code = diagnostic_code
        super().__init__(",".join(codes))


# Portfolio fixture only. These prices and approvals are invented for an
# offline demonstration and must never be treated as a live offer catalog.
PRICE_POLICY_VERSION = "portfolio-synthetic-pricing-v1"
GOLD_POLICY_VERSION = "portfolio-synthetic-scenarios-v1"
POLICY_GATE_VERSION = "portfolio-policy-gate-v1"
PRODUCTS = {
    "A": {"list_price": 1000, "floor": 900, "sellable": True},
    "B": {"list_price": 1300, "floor": 1200, "sellable": False},
    "C": {"list_price": 800, "floor": 700, "sellable": True},
    "D": {"list_price": 500, "floor": 500, "sellable": False},
    "E": {"list_price": 300, "floor": 300, "sellable": False},
    "F": {"list_price": 500, "floor": 500, "sellable": False},
}
DEFAULT_PRICING_POLICY = {
    "version": PRICE_POLICY_VERSION,
    "approval_status": "APPROVED",
    "approved_by": "OWNER",
    "products": PRODUCTS,
    "negotiation": {"A": {"first_counter": 950, "min_prior_actual_sales_messages": 1,
                           "later_counter_requires_human": True}},
}
# D/E/F have unset detailed delivery boundaries in the current approved
# contract. They are valid ideas for internal review, not autonomous offers.
_INSTALLMENT = re.compile(r"(?:分期|先付.{0,12}(?:一半|50%|百分之五十)|尾款)")
_UNAPPROVED_URGENCY = re.compile(r"(?:优惠|折扣|名额).{0,14}(?:仅限|截止|到期|小时|今天|明天|两天|三天)")
_RESULT_GUARANTEE = re.compile(r"(?:保录|保证录取|包录取|百分之百录取|100%录取)")
_UNAPPROVED_SERVICE_CAPABILITY = re.compile(
    r"(?:自动|定时).{0,20}(?:提醒|推送)|"
    r"(?:专属|每位学生|实时|在线).{0,16}(?:进度看板|申请看板)|"
    r"(?:我们|系统|平台|学生).{0,12}(?:进度看板|申请看板)|"
    r"(?:绝不|保证|不会|一定不会).{0,16}(?:错过|漏掉).{0,16}(?:截止|节点|申请)", re.I)
_ABSTRACT_REQUIRED_CONTENT = re.compile(r"^(?:解释|强调|说明|突出|讲清|告知|询问|展示|体现|阐述|描述|提及|表达)")
_CASE_CLAIM = re.compile(r"(?:我们|本机构|老师).{0,18}(?:辅导过|经手过|有|见过|积累).{0,18}(?:案例|相似学生|同背景学生)")
_SPECIFIC_CASE = re.compile(r"(?:与你|跟你|和你|相似|相近|(?<!不)同背景|(?<!不)同层级|同均分|NTU|NUS|港大)")
_PROGRAM_FACT = re.compile(
    r"(?:项目|专业|学校|院校|NTU|NUS|港大|港中文|新国立|南洋理工)"
    r".{0,36}(?:截止|学费|语言要求|雅思|IELTS|托福|均分要求|先修|课程|"
    r"录取政策|开放申请|GMAT|GRE|GPA|工作经验)", re.I)
_PROGRAM_SPECIFIC_VALUE = re.compile(
    r"(?:20\d{2}\s*年\s*(?:截止|开放|入学)|20\d{2}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{1,2}\s*月\s*(?:\d{1,2}\s*日?|底|初|中|上旬|中旬|下旬|末)|"
    r"(?:本月|这个月|下个月)\s*(?:底|末|初|中)\s*(?:截止|开放)?|"
    r"\d+(?:\.\d+)?\s*(?:元|英镑|港币|分)|"
    r"(?:雅思|IELTS|托福|均分|GPA|GMAT|GRE).{0,8}\d+(?:\.\d+)?|"
    r"(?:一|二|三|两|四|五|六|七|八|九|十|\d+)\s*年工作经验)", re.I
)
_PROGRAM_CATEGORICAL_ASSERTION = re.compile(
    r"(?:不要求|无需|免除|必须|要求|已开放|已截止|截止了|尚未截止|"
    r"暂停申请|关闭申请|课程包括|必修课|选修课)")
_PROGRAM_QUESTION = re.compile(r"(?:是否|是不是|有无|有没有|多少|哪天|几号|什么|能否|可否|吗[，。？?]?)")
_PROGRAM_SENTENCE_BOUNDARY = re.compile(r"[。！？!?；;\n]+")
_PROGRAM_LOOKUP_INTENT = re.compile(r"(?:帮你|先|会|可以).{0,10}(?:核对|查|查询|确认一下|看一下)")
_PROGRAM_SOURCE_REFERRAL = re.compile(r"(?:以|请以|要以)(?:学校)?(?:官网|官方信息|官方公告).{0,3}为准|待官网核实")
_MONEY = re.compile(r"(?<!\d)(?:[¥￥]\s*\d{3,6}|\d{3,6}\s*(?:元|块|人民币))(?!\d)")
_NUMERIC_TOKEN = re.compile(r"\d+(?:\.\d+)?")
_PRODUCT_REF = re.compile(r"(?:产品|套餐|方案)\s*([A-F])(?![A-Za-z])|(?<![A-Za-z])([A-F])\s*(?:产品|套餐)")
_PRODUCT_REF_CONTINUATION = re.compile(r"\s*(?:或|和|与|及|、)\s*([A-F])(?![A-Za-z])")
_C_EXECUTION = re.compile(r"(?:申请协助|申请执行|申请递交|网申|递交|全流程|全套)")
_C_EXCLUSION = re.compile(r"(?:不含|不包括|不提供|不负责|没有|需另购|另行购买|只交付书面方案|仅交付书面方案)")
_ADVANTAGE_CLAIMS = (
    ("TEAM_REGION", re.compile(r"(?:我们|团队).{0,12}(?:新加坡|英国|香港).{0,12}(?:成员|老师|团队|当地)"), {"ADV-002"}),
    ("SCHOOL_INFORMATION", re.compile(r"(?:我们|团队).{0,16}(?:核实|询问).{0,10}(?:学校|院校).{0,8}(?:一手|信息)|(?:学校|院校).{0,10}一手信息"), {"ADV-003"}),
    ("PERSONALIZED_WRITING", re.compile(r"(?:PS|CV|文书).{0,14}(?:个性化|定制|不套模板|不用模板)"), {"ADV-004", "ADV-005"}),
    ("LOCAL_HOUSING", re.compile(r"线下看房"), {"ADV-006"}),
    ("INTERNSHIP_INFO", re.compile(r"实习信息"), {"ADV-008"}),
)


def _flat_text(decision: dict) -> str:
    content = decision.get("content_contract", {})
    parts = [content.get("semantic_draft", "")]
    parts.extend(content.get("must_include", []))
    return "\n".join(str(part) for part in parts)


def _money_values(text: str) -> set[int]:
    return {int(re.search(r"\d+", match.group()).group()) for match in _MONEY.finditer(text)}


def _product_references(text: str) -> set[str]:
    """Detect explicitly named catalog products, including '产品 C 或 D'."""
    found: set[str] = set()
    for match in _PRODUCT_REF.finditer(text):
        found.add(match.group(1) or match.group(2))
        tail = text[match.end():]
        while continuation := _PRODUCT_REF_CONTINUATION.match(tail):
            found.add(continuation.group(1))
            tail = tail[continuation.end():]
    return found


def _product_c_scope_exceeded(text: str) -> bool:
    for sentence in _PROGRAM_SENTENCE_BOUNDARY.split(text):
        if "C" in _product_references(sentence) and _C_EXECUTION.search(sentence) and not _C_EXCLUSION.search(sentence):
            return True
    return False


def _check_product_claims(text: str, offer: dict, pricing_policy: dict) -> None:
    references = _product_references(text)
    if not references:
        return
    if any(not pricing_policy["products"].get(product_id, {}).get("sellable") for product_id in references):
        raise PolicyViolation("PRODUCT_NOT_AUTONOMOUSLY_SELLABLE")
    if _product_c_scope_exceeded(text):
        raise PolicyViolation("PRODUCT_C_SCOPE_EXCEEDED")
    if offer.get("state") == "STANDARD_OFFER":
        quote = offer.get("quote")
        if not isinstance(quote, dict) or references != {quote.get("product_id")}:
            raise PolicyViolation("PRODUCT_CLAIM_OFFER_MISMATCH")
    elif offer.get("state") == "NONE":
        raise PolicyViolation("PRODUCT_CLAIM_WITHOUT_OFFER")


def _claims_installment(text: str) -> bool:
    for match in _INSTALLMENT.finditer(text):
        prefix = text[max(0, match.start() - 7):match.start()]
        if match.group() == "分期" and re.search(r"(?:不支持|不能|不可|不做|没有|无|不允许)$", prefix):
            continue
        return True
    return False


def _case_claim_kind(text: str) -> str | None:
    for match in _CASE_CLAIM.finditer(text):
        neighborhood = text[max(0, match.start() - 12):match.end() + 20]
        if _SPECIFIC_CASE.search(neighborhood):
            return "SPECIFIC"
        return "GENERAL"
    return None


def _has_specific_program_fact(text: str) -> bool:
    """Do not confuse a generic service promise with a project's factual claim.

    This is a conservative textual screen, not a substitute for human review or
    official verification of a claim that the model explicitly records.
    """
    for sentence in _PROGRAM_SENTENCE_BOUNDARY.split(text):
        for match in _PROGRAM_FACT.finditer(sentence):
            window = sentence[max(0, match.start() - 16):min(len(sentence), match.end() + 24)]
            if _PROGRAM_LOOKUP_INTENT.search(sentence[:match.start()]):
                continue
            if _PROGRAM_SPECIFIC_VALUE.search(window):
                return True
            if (_PROGRAM_CATEGORICAL_ASSERTION.search(window)
                    and not _PROGRAM_QUESTION.search(window)
                    and not _PROGRAM_SOURCE_REFERRAL.search(window)):
                return True
    return False


def validate_decision(context: dict, decision: dict, returned: dict[str, dict],
                      *, pricing_policy: dict | None = None) -> list[str]:
    """Return warnings or raise for unsafe / structurally invalid decisions."""
    pricing_policy = pricing_policy or DEFAULT_PRICING_POLICY
    if not isinstance(pricing_policy, dict) or pricing_policy.get("approval_status") != "APPROVED" or pricing_policy.get("approved_by") != "OWNER" or not isinstance(pricing_policy.get("products"), dict):
        raise PolicyViolation("PRICING_POLICY_NOT_APPROVED")
    if not isinstance(decision, dict):
        raise PolicyViolation("DECISION_NOT_OBJECT")
    mandatory = {"hypotheses", "unknowns", "selected_strategy", "previous_objective_assessment",
                 "current_objective", "action_plan", "offer", "content_contract", "evidence_ids",
                 "stop_required", "human_approval_required"}
    if not mandatory <= set(decision):
        raise PolicyViolation("DECISION_FIELDS_MISSING")
    if decision.get("human_approval_required") is not True:
        raise PolicyViolation("HUMAN_APPROVAL_REQUIRED")
    if not isinstance(decision["evidence_ids"], list) or not all(isinstance(x, str) for x in decision["evidence_ids"]):
        raise PolicyViolation("INVALID_EVIDENCE_IDS")
    if len(decision["evidence_ids"]) != len(set(decision["evidence_ids"])):
        raise PolicyViolation("DUPLICATE_EVIDENCE")
    if not set(decision["evidence_ids"]) <= set(returned):
        raise PolicyViolation("UNRETURNED_EVIDENCE_ADOPTED")
    if context["current_state"]["contact_permission"] == "DO_NOT_CONTACT":
        if not decision["stop_required"] or decision["current_objective"].get("status") != "STOPPED":
            raise PolicyViolation("DO_NOT_CONTACT_REQUIRES_STOP")
    if context["current_state"]["contact_permission"] == "UNKNOWN" and not decision["stop_required"]:
        raise PolicyViolation("UNKNOWN_CONTACT_PERMISSION")
    objective = decision["current_objective"]
    if not isinstance(objective, dict) or not objective.get("goal") or not objective.get("why_now") or not objective.get("success_signals") or not objective.get("failure_signals"):
        raise PolicyViolation("UNOBSERVABLE_OBJECTIVE")
    if objective.get("status") not in {"ACTIVE", "STOPPED"} or type(objective.get("attempt_count")) is not int or type(objective.get("max_attempts")) is not int or not 1 <= objective["attempt_count"] <= objective["max_attempts"] <= 4:
        raise PolicyViolation("OBJECTIVE_ATTEMPT_LIMIT")
    if objective["status"] == "STOPPED" and decision["action_plan"].get("selected_action") is not None:
        raise PolicyViolation("STOPPED_OBJECTIVE_HAS_ACTION")
    content = decision["content_contract"]
    if not isinstance(content, dict) or not isinstance(content.get("semantic_draft"), str) or not isinstance(content.get("must_include"), list) or not isinstance(content.get("must_not_include"), list):
        raise PolicyViolation("CONTENT_CONTRACT_INCOMPLETE")
    if objective["status"] == "ACTIVE" and not content["semantic_draft"].strip() and decision["offer"].get("state") != "CUSTOM_OFFER_PROPOSAL":
        raise PolicyViolation("EMPTY_SEMANTIC_DRAFT")
    if any(isinstance(item, str) and _ABSTRACT_REQUIRED_CONTENT.search(item.strip())
           for item in content["must_include"]):
        raise PolicyViolation("ABSTRACT_REQUIRED_CONTENT")
    text = _flat_text(decision)
    if _RESULT_GUARANTEE.search(text):
        raise PolicyViolation("RESULT_GUARANTEE")
    if _UNAPPROVED_SERVICE_CAPABILITY.search(text):
        raise PolicyViolation("UNAPPROVED_SERVICE_CAPABILITY")
    if _claims_installment(text):
        raise PolicyViolation("STANDARD_INSTALLMENT_UNAPPROVED")
    if _UNAPPROVED_URGENCY.search(text):
        raise PolicyViolation("UNAPPROVED_URGENCY")
    case_ids = {key for key, item in returned.items() if item["source_type"] == "anonymized_historical_case"}
    advantage_ids = {key for key, item in returned.items() if item["source_type"] == "approved_advantage"
                     and item["item"].get("advantage_id") == "ADV-001"}
    case_kind = _case_claim_kind(text)
    if case_kind == "SPECIFIC" and not set(decision["evidence_ids"]) & case_ids:
        raise PolicyViolation("UNSUPPORTED_CASE_CLAIM")
    if case_kind == "GENERAL" and not set(decision["evidence_ids"]) & (case_ids | advantage_ids):
        raise PolicyViolation("UNSUPPORTED_CASE_CLAIM")
    if _has_specific_program_fact(text) and not decision.get("program_claims"):
        raise PolicyViolation("PROGRAM_FACT_REQUIRES_CLAIM_RECORD")
    for rule_name, pattern, required_ids in _ADVANTAGE_CLAIMS:
        if pattern.search(text) and not set(decision["evidence_ids"]) & required_ids:
            raise PolicyViolation("UNSUPPORTED_ADVANTAGE_CLAIM",
                                  diagnostic_code=f"ADVANTAGE_{rule_name}")
    offer = decision["offer"]
    if not isinstance(offer, dict) or offer.get("state") not in {"NONE", "STANDARD_OFFER", "CUSTOM_OFFER_PROPOSAL", "APPROVED_CUSTOM_OFFER"}:
        raise PolicyViolation("INVALID_OFFER_STATE")
    _check_product_claims(text, offer, pricing_policy)
    if offer["state"] == "CUSTOM_OFFER_PROPOSAL":
        proposal = offer.get("proposal")
        required = {"customer_need_evidence", "why_standard_offers_are_not_best_fit", "proposed_scope",
                    "explicit_exclusions", "proposed_price", "payment_terms", "estimated_delivery_cost",
                    "estimated_margin", "delivery_requirements", "risks", "customer_next_step",
                    "required_approvals", "approval_status"}
        if not isinstance(proposal, dict) or not required <= set(proposal) or proposal.get("approval_status") != "PENDING":
            raise PolicyViolation("CUSTOM_PROPOSAL_INCOMPLETE")
        if proposal.get("estimated_delivery_cost", {}).get("status") != "UNVERIFIED_ESTIMATE" or proposal.get("estimated_margin", {}).get("status") != "UNVERIFIED_ESTIMATE":
            raise PolicyViolation("CUSTOM_COST_NOT_UNVERIFIED")
        if set(proposal.get("required_approvals", [])) != {"PRODUCT", "DELIVERY", "PRICING"}:
            raise PolicyViolation("CUSTOM_APPROVAL_ROLES_MISSING")
        if _money_values(text) & _money_values(str(proposal.get("proposed_price"))):
            raise PolicyViolation("UNAPPROVED_CUSTOM_PRICE_LEAK")
        return ["CUSTOM_OFFER_INTERNAL_ONLY"]
    if offer["state"] == "STANDARD_OFFER":
        quote = offer.get("quote")
        if quote is not None:
            if not isinstance(quote, dict) or set(quote) < {"product_id", "price", "policy_version"}:
                raise PolicyViolation("INVALID_STANDARD_QUOTE")
            product = pricing_policy["products"].get(quote["product_id"])
            if not product or not product["sellable"] or quote["policy_version"] != pricing_policy["version"]:
                raise PolicyViolation("PRODUCT_NOT_AUTONOMOUSLY_SELLABLE")
            price = quote["price"]
            if type(price) is not int or not product["floor"] <= price <= product["list_price"]:
                raise PolicyViolation("PRICE_OUTSIDE_POLICY")
            negotiation = pricing_policy.get("negotiation", {}).get(quote["product_id"])
            if negotiation and price < product["list_price"]:
                prior_sent = sum(1 for item in context["customer_visible_history"] if item["role"] == "sales")
                if prior_sent < negotiation["min_prior_actual_sales_messages"]:
                    raise PolicyViolation("DISCOUNT_BEFORE_VALUE_TURN")
                if negotiation.get("later_counter_requires_human") and price != negotiation["first_counter"]:
                    raise PolicyViolation("SECOND_COUNTER_REQUIRES_HUMAN")
            if quote["product_id"] == "C" and (_product_c_scope_exceeded(text) or re.search(
                r"(?:我们|机构|老师).{0,12}(?:帮你|代你|替你).{0,8}(?:递交|网申|文书撰写)", text)):
                raise PolicyViolation("PRODUCT_C_SCOPE_EXCEEDED")
            prices_in_text = _money_values(text)
            if prices_in_text and price not in prices_in_text:
                raise PolicyViolation("QUOTE_TEXT_PRICE_MISMATCH")
    if offer["state"] == "NONE" and _money_values(text):
        raise PolicyViolation("UNBOUND_PRICE_CLAIM")
    return []


def validate_conversation(decision: dict, messages: list[dict], *, verified_claims: list[dict]) -> str:
    if not isinstance(messages, list) or not 1 <= len(messages) <= 6:
        raise PolicyViolation("INVALID_CONVERSATION_MESSAGES")
    if any(not isinstance(m, dict) or m.get("type") not in {"text", "sticker_suggestion"} for m in messages):
        raise PolicyViolation("INVALID_CONVERSATION_MESSAGE")
    text = "\n".join(m.get("content", "") for m in messages if m["type"] == "text")
    if not text.strip():
        raise PolicyViolation("CONVERSATION_TEXT_REQUIRED")
    base = decision["content_contract"]["semantic_draft"]
    if _product_references(text) != _product_references(base):
        raise PolicyViolation("CONVERSATION_CHANGED_PRODUCT_CLAIM")
    if _product_c_scope_exceeded(text):
        raise PolicyViolation("PRODUCT_C_SCOPE_EXCEEDED")
    if _money_values(text) != _money_values(base):
        raise PolicyViolation("MATERIAL_PRICE_CHANGED")
    if set(_NUMERIC_TOKEN.findall(text)) != set(_NUMERIC_TOKEN.findall(base)):
        raise PolicyViolation("MATERIAL_NUMBER_CHANGED")
    if _RESULT_GUARANTEE.search(text) or _claims_installment(text) or _UNAPPROVED_URGENCY.search(text):
        raise PolicyViolation("CONVERSATION_ADDED_FORBIDDEN_PROMISE")
    if _UNAPPROVED_SERVICE_CAPABILITY.search(text):
        raise PolicyViolation("CONVERSATION_ADDED_UNAPPROVED_SERVICE_CAPABILITY")
    if _case_claim_kind(text) and not _case_claim_kind(base):
        raise PolicyViolation("CONVERSATION_ADDED_CASE_CLAIM")
    if _case_claim_kind(text) == "SPECIFIC" and _case_claim_kind(base) != "SPECIFIC":
        raise PolicyViolation("CONVERSATION_UPGRADED_CASE_CLAIM")
    for _rule_name, pattern, _required_ids in _ADVANTAGE_CLAIMS:
        if pattern.search(text) and not pattern.search(base):
            raise PolicyViolation("CONVERSATION_ADDED_ADVANTAGE_CLAIM")
    if _has_specific_program_fact(text) and not _has_specific_program_fact(base):
        raise PolicyViolation("CONVERSATION_ADDED_PROGRAM_FACT")
    for forbidden in decision["content_contract"].get("must_not_include", []):
        if isinstance(forbidden, str) and forbidden and forbidden in text:
            raise PolicyViolation("CONVERSATION_FORBIDDEN_CONTENT")
    for required in decision["content_contract"].get("must_include", []):
        if isinstance(required, str) and required and required not in text:
            raise PolicyViolation("CONVERSATION_DROPPED_REQUIRED_CONTENT")
    if _has_specific_program_fact(text):
        if not verified_claims or any(claim.get("status") != "VERIFIED" for claim in verified_claims):
            raise PolicyViolation("UNVERIFIED_PROGRAM_FACT_IN_CONVERSATION")
        for claim in decision.get("program_claims", []):
            value = claim.get("expected_value")
            if isinstance(value, str) and (value not in base or value not in text):
                raise PolicyViolation("PROGRAM_FACT_NOT_BOUND_TO_VERIFIED_CLAIM")
    return text
