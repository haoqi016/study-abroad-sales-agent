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
_INSTALLMENT = re.compile(r"(?:\u5206\u671f|\u5148\u4ed8.{0,12}(?:\u4e00\u534a|50%|\u767e\u5206\u4e4b\u4e94\u5341)|\u5c3e\u6b3e|\b(?:installments?|payment plan|pay\s+in\s+\d+\s+parts?|pay\s+half\s+(?:now|upfront)|remaining balance)\b)", re.I)
_UNAPPROVED_URGENCY = re.compile(r"(?:\u4f18\u60e0|\u6298\u6263|\u540d\u989d).{0,14}(?:\u4ec5\u9650|\u622a\u6b62|\u5230\u671f|\u5c0f\u65f6|\u4eca\u5929|\u660e\u5929|\u4e24\u5929|\u4e09\u5929)|\b(?:discount|offer|deal|spots?|places?)\b.{0,35}\b(?:today|tomorrow|tonight|hours?|days?|expires?|ends?|deadline|limited time)\b|\b(?:today|tomorrow|tonight)\s+only\b|\b(?:only\s+today|limited\s+time)\b.{0,35}\b(?:discount|offer|deal|spots?|places?)\b", re.I)
_RESULT_GUARANTEE = re.compile(r"(?:\u4fdd\u5f55|\u4fdd\u8bc1\u5f55\u53d6|\u5305\u5f55\u53d6|\u767e\u5206\u4e4b\u767e\u5f55\u53d6|100%\u5f55\u53d6)|\b(?:guaranteed?\s+(?:admission|acceptance|offer|place)|guarantee\s+(?:you\s+)?(?:an?\s+)?(?:admission|acceptance|offer|place)|(?:100\s*%|certain)\s+(?:admission|acceptance)|we\s+will\s+(?:definitely|certainly)\s+get\s+you\s+(?:admitted|accepted))\b", re.I)
_UNAPPROVED_SERVICE_CAPABILITY = re.compile(
    r"(?:\u81ea\u52a8|\u5b9a\u65f6).{0,20}(?:\u63d0\u9192|\u63a8\u9001)|"
    r"(?:\u4e13\u5c5e|\u6bcf\u4f4d\u5b66\u751f|\u5b9e\u65f6|\u5728\u7ebf).{0,16}(?:\u8fdb\u5ea6\u770b\u677f|\u7533\u8bf7\u770b\u677f)|"
    r"(?:\u6211\u4eec|\u7cfb\u7edf|\u5e73\u53f0|\u5b66\u751f).{0,12}(?:\u8fdb\u5ea6\u770b\u677f|\u7533\u8bf7\u770b\u677f)|"
    r"(?:\u7edd\u4e0d|\u4fdd\u8bc1|\u4e0d\u4f1a|\u4e00\u5b9a\u4e0d\u4f1a).{0,16}(?:\u9519\u8fc7|\u6f0f\u6389).{0,16}(?:\u622a\u6b62|\u8282\u70b9|\u7533\u8bf7)|"
    r"\b(?:automatic|scheduled)\s+(?:deadline\s+)?(?:reminders?|notifications?)\b|"
    r"\b(?:personal|dedicated|real[- ]time)\s+(?:student\s+|application\s+|progress\s+)?(?:dashboard|progress tracker)\b|"
    r"\b(?:guarantee|promise|ensure)\b.{0,25}\b(?:never|won't|will not)\s+(?:miss|overlook)\b.{0,20}\b(?:deadline|application)\b", re.I)
_ABSTRACT_REQUIRED_CONTENT = re.compile(r"^(?:\u89e3\u91ca|\u5f3a\u8c03|\u8bf4\u660e|\u7a81\u51fa|\u8bb2\u6e05|\u544a\u77e5|\u8be2\u95ee|\u5c55\u793a|\u4f53\u73b0|\u9610\u8ff0|\u63cf\u8ff0|\u63d0\u53ca|\u8868\u8fbe|(?:explain|emphasize|describe|highlight|tell|ask|show|mention)\b)", re.I)
_CASE_CLAIM = re.compile(r"(?:\u6211\u4eec|\u672c\u673a\u6784|\u8001\u5e08).{0,18}(?:\u8f85\u5bfc\u8fc7|\u7ecf\u624b\u8fc7|\u6709|\u89c1\u8fc7|\u79ef\u7d2f).{0,18}(?:\u6848\u4f8b|\u76f8\u4f3c\u5b66\u751f|\u540c\u80cc\u666f\u5b66\u751f)|\b(?:we|our\s+(?:team|advisors?))\b.{0,40}\b(?:worked\s+with|helped|have)\b.{0,35}\b(?:cases?|students?|applicants?)\b", re.I)
_SPECIFIC_CASE = re.compile(r"(?:\u4e0e\u4f60|\u8ddf\u4f60|\u548c\u4f60|\u76f8\u4f3c|\u76f8\u8fd1|(?<!\u4e0d)\u540c\u80cc\u666f|(?<!\u4e0d)\u540c\u5c42\u7ea7|\u540c\u5747\u5206|NTU|NUS|\u6e2f\u5927)|\b(?:similar\s+to\s+you|similar\s+background|same\s+(?:background|grade|score)|NTU|NUS)\b", re.I)
_EXTERNAL_CASE_SOURCE_TYPE = "external_public_reference_unverified"
_EXTERNAL_CASE_TEXT = re.compile(r"\b(?:case|reference|offer|admission|acceptance|rejection|waitlist)\b", re.I)
_EXTERNAL_CASE_ATTRIBUTION = re.compile(
    r"\b(?:(?:external(?:\s+public)?|third[- ]party)\s+(?:reference|case|example)|"
    r"public\s+platform\s+(?:reference|case|reports?))\b", re.I)
_EXTERNAL_CASE_OUTCOME = re.compile(r"\b(?:offer|admission|admitted|accepted|acceptance|rejected|rejection|waitlisted|waitlist)\b", re.I)
_EXTERNAL_OUTCOME_CAVEAT = re.compile(r"\b(?:unverified|not\s+(?:independently\s+)?verified|self[- ]reported)\b", re.I)
_INSTITUTION_CASE_OWNERSHIP = re.compile(
    r"\b(?:our\s+(?:cases?|students?|applicants?)|one\s+of\s+our\s+(?:cases?|students?|applicants?)|"
    r"(?:we|our\s+(?:team|advisors?|institution))\b.{0,40}"
    r"\b(?:worked\s+with|helped|served|handled|guided|secured|got|have)\b.{0,35}"
    r"\b(?:cases?|students?|applicants?|offers?))\b", re.I)
_EXTERNAL_CASE_PREDICTION = re.compile(
    r"\b(?:your\s+(?:admission\s+)?chances?\s+(?:are|is)\s+(?:high|good|strong)|"
    r"you\s+(?:are\s+likely|will\s+probably)\s+(?:be\s+)?(?:admitted|accepted|get\s+an?\s+offer)|"
    r"(?:high|strong)\s+(?:admission\s+)?probability\s+for\s+you|"
    r"this\s+(?:case|reference)\s+(?:shows?|means?)\s+(?:you|your\s+chances?))\b", re.I)
_OVERSTATED_SERVICE_SCOPE = re.compile(
    r"\b(?:we|our\s+(?:team|advisors?))\b.{0,18}"
    r"\b(?:handle|manage|take\s+care\s+of|do)\b.{0,16}"
    r"\b(?:every\s+step|the\s+entire\s+(?:application\s+)?process|the\s+whole\s+application)\b", re.I)
_STUDENT_PARTICIPATION = re.compile(
    r"\b(?:you\s+(?:still\s+)?(?:need\s+to|must|will\s+need\s+to)|please)\s+"
    r"(?:provide|confirm|share|send)\b.{0,55}"
    r"\b(?:documents?|materials?|information|details)\b", re.I)
_PROGRAM_FACT = re.compile(
    r"(?:\u9879\u76ee|\u4e13\u4e1a|\u5b66\u6821|\u9662\u6821|NTU|NUS|\u6e2f\u5927|\u6e2f\u4e2d\u6587|\u65b0\u56fd\u7acb|\u5357\u6d0b\u7406\u5de5|university|school|program|course)"
    r".{0,50}(?:\u622a\u6b62|\u5b66\u8d39|\u8bed\u8a00\u8981\u6c42|\u96c5\u601d|IELTS|\u6258\u798f|\u5747\u5206\u8981\u6c42|\u5148\u4fee|\u8bfe\u7a0b|"
    r"\u5f55\u53d6\u653f\u7b56|\u5f00\u653e\u7533\u8bf7|GMAT|GRE|GPA|\u5de5\u4f5c\u7ecf\u9a8c|deadline|tuition|language requirement|"
    r"prerequisite|admission requirement|application opens?|open for applications|is open|is closed|work experience)", re.I)
_PROGRAM_SPECIFIC_VALUE = re.compile(
    r"(?:20\d{2}\s*\u5e74\s*(?:\u622a\u6b62|\u5f00\u653e|\u5165\u5b66)|20\d{2}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{1,2}\s*\u6708\s*(?:\d{1,2}\s*\u65e5?|\u5e95|\u521d|\u4e2d|\u4e0a\u65ec|\u4e2d\u65ec|\u4e0b\u65ec|\u672b)|"
    r"(?:\u672c\u6708|\u8fd9\u4e2a\u6708|\u4e0b\u4e2a\u6708)\s*(?:\u5e95|\u672b|\u521d|\u4e2d)\s*(?:\u622a\u6b62|\u5f00\u653e)?|"
    r"\d+(?:\.\d+)?\s*(?:\u5143|\u82f1\u9551|\u6e2f\u5e01|\u5206)|"
    r"(?:\u96c5\u601d|IELTS|\u6258\u798f|\u5747\u5206|GPA|GMAT|GRE).{0,8}\d+(?:\.\d+)?|"
    r"(?:\u4e00|\u4e8c|\u4e09|\u4e24|\u56db|\u4e94|\u516d|\u4e03|\u516b|\u4e5d|\u5341|\d+)\s*\u5e74\u5de5\u4f5c\u7ecf\u9a8c|"
    r"\b20\d{2}\b|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\b|"
    r"(?:[$£]|USD|SGD|GBP)\s*\d+(?:\.\d+)?|(?:IELTS|TOEFL|GPA|GMAT|GRE)\s*\d+(?:\.\d+)?|\b\d+\s+years?\s+(?:of\s+)?work experience\b)", re.I
)
_PROGRAM_CATEGORICAL_ASSERTION = re.compile(
    r"(?:\u4e0d\u8981\u6c42|\u65e0\u9700|\u514d\u9664|\u5fc5\u987b|\u8981\u6c42|\u5df2\u5f00\u653e|\u5df2\u622a\u6b62|\u622a\u6b62\u4e86|\u5c1a\u672a\u622a\u6b62|"
    r"\u6682\u505c\u7533\u8bf7|\u5173\u95ed\u7533\u8bf7|\u8bfe\u7a0b\u5305\u62ec|\u5fc5\u4fee\u8bfe|\u9009\u4fee\u8bfe|\b(?:requires?|does not require|waives?|is open|is closed|includes? mandatory courses?)\b)", re.I)
_PROGRAM_QUESTION = re.compile(r"(?:\u662f\u5426|\u662f\u4e0d\u662f|\u6709\u65e0|\u6709\u6ca1\u6709|\u591a\u5c11|\u54ea\u5929|\u51e0\u53f7|\u4ec0\u4e48|\u80fd\u5426|\u53ef\u5426|\u5417[，。？?]?|\?|^\s*(?:whether|when|what|how much|does|is|can)\b)", re.I)
_PROGRAM_SENTENCE_BOUNDARY = re.compile(r"[。！？!?；;\n]+")
_PROGRAM_LOOKUP_INTENT = re.compile(r"(?:\u5e2e\u4f60|\u5148|\u4f1a|\u53ef\u4ee5).{0,10}(?:\u6838\u5bf9|\u67e5|\u67e5\u8be2|\u786e\u8ba4\u4e00\u4e0b|\u770b\u4e00\u4e0b)|\b(?:i|we)\s+(?:will|can)\s+(?:check|verify|look up)\b", re.I)
_PROGRAM_SOURCE_REFERRAL = re.compile(r"(?:\u4ee5|\u8bf7\u4ee5|\u8981\u4ee5)(?:\u5b66\u6821)?(?:\u5b98\u7f51|\u5b98\u65b9\u4fe1\u606f|\u5b98\u65b9\u516c\u544a).{0,3}\u4e3a\u51c6|\u5f85\u5b98\u7f51\u6838\u5b9e|\b(?:subject to|verify against|check with)\s+(?:the\s+)?(?:official|university)\s+(?:website|source|notice)\b", re.I)
_MONEY = re.compile(r"(?<!\d)(?:[¥￥$£]\s*\d{3,6}|\d{3,6}\s*(?:\u5143|\u5757|\u4eba\u6c11\u5e01|RMB|CNY|USD|SGD|dollars?|pounds?))(?!\d)", re.I)
_NUMERIC_TOKEN = re.compile(r"\d+(?:\.\d+)?")
_PRODUCT_REF = re.compile(r"(?:\u4ea7\u54c1|\u5957\u9910|\u65b9\u6848|product|package|plan)\s*([A-F])(?![A-Za-z])|(?<![A-Za-z])([A-F])\s*(?:\u4ea7\u54c1|\u5957\u9910|product|package)", re.I)
_PRODUCT_REF_CONTINUATION = re.compile(r"\s*(?:\u6216|\u548c|\u4e0e|\u53ca|、|or|and)\s*([A-F])(?![A-Za-z])", re.I)
_C_EXECUTION = re.compile(r"(?:\u7533\u8bf7\u534f\u52a9|\u7533\u8bf7\u6267\u884c|\u7533\u8bf7\u9012\u4ea4|\u7f51\u7533|\u9012\u4ea4|\u5168\u6d41\u7a0b|\u5168\u5957|application\s+(?:submission|filing|execution)|submit\s+(?:your\s+)?application|full[- ]service|essay\s+(?:writing|drafting))", re.I)
_C_EXCLUSION = re.compile(r"(?:\u4e0d\u542b|\u4e0d\u5305\u62ec|\u4e0d\u63d0\u4f9b|\u4e0d\u8d1f\u8d23|\u6ca1\u6709|\u9700\u53e6\u8d2d|\u53e6\u884c\u8d2d\u4e70|\u53ea\u4ea4\u4ed8\u4e66\u9762\u65b9\u6848|\u4ec5\u4ea4\u4ed8\u4e66\u9762\u65b9\u6848|does\s+not\s+include|excludes?|not\s+included|written\s+plan\s+only)", re.I)
_ADVANTAGE_CLAIMS = (
    ("TEAM_REGION", re.compile(r"(?:\u6211\u4eec|\u56e2\u961f).{0,12}(?:\u65b0\u52a0\u5761|\u82f1\u56fd|\u9999\u6e2f).{0,12}(?:\u6210\u5458|\u8001\u5e08|\u56e2\u961f|\u5f53\u5730)|\b(?:our\s+team|we)\b.{0,24}\b(?:Singapore|UK|Hong Kong)\b.{0,24}\b(?:based|local|advisors?|members?)\b", re.I), {"ADV-002"}),
    ("SCHOOL_INFORMATION", re.compile(r"(?:\u6211\u4eec|\u56e2\u961f).{0,16}(?:\u6838\u5b9e|\u8be2\u95ee).{0,10}(?:\u5b66\u6821|\u9662\u6821).{0,8}(?:\u4e00\u624b|\u4fe1\u606f)|(?:\u5b66\u6821|\u9662\u6821).{0,10}\u4e00\u624b\u4fe1\u606f|\b(?:we|our\s+team)\b.{0,25}\b(?:verify|check|ask)\b.{0,20}\b(?:school|university)\b.{0,20}\b(?:firsthand|information)\b", re.I), {"ADV-003"}),
    ("PERSONALIZED_WRITING", re.compile(r"(?:PS|CV|\u6587\u4e66).{0,14}(?:\u4e2a\u6027\u5316|\u5b9a\u5236|\u4e0d\u5957\u6a21\u677f|\u4e0d\u7528\u6a21\u677f)|\b(?:PS|CV|essay|personal statement)\b.{0,24}\b(?:personalized|customized|no templates?)\b", re.I), {"ADV-004", "ADV-005"}),
    ("LOCAL_HOUSING", re.compile(r"\u7ebf\u4e0b\u770b\u623f|\b(?:in[- ]person|offline)\s+(?:housing|apartment|flat)\s+(?:viewing|visit)\b", re.I), {"ADV-006"}),
    ("INTERNSHIP_INFO", re.compile(r"\u5b9e\u4e60\u4fe1\u606f|\binternship\s+(?:information|leads?|opportunities)\b", re.I), {"ADV-008"}),
)


def _flat_text(decision: dict) -> str:
    content = decision.get("content_contract", {})
    parts = [content.get("semantic_draft", "")]
    parts.extend(content.get("must_include", []))
    return "\n".join(str(part) for part in parts)


def _money_values(text: str) -> set[int]:
    return {int(re.search(r"\d+", match.group()).group()) for match in _MONEY.finditer(text)}


def _product_references(text: str) -> set[str]:
    """Detect explicitly named catalog products, including 'Product C or D'."""
    found: set[str] = set()
    for match in _PRODUCT_REF.finditer(text):
        found.add((match.group(1) or match.group(2)).upper())
        tail = text[match.end():]
        while continuation := _PRODUCT_REF_CONTINUATION.match(tail):
            found.add(continuation.group(1).upper())
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
        prefix = text[max(0, match.start() - 30):match.start()]
        if match.group() == "\u5206\u671f" and re.search(r"(?:\u4e0d\u652f\u6301|\u4e0d\u80fd|\u4e0d\u53ef|\u4e0d\u505a|\u6ca1\u6709|\u65e0|\u4e0d\u5141\u8bb8)$", prefix):
            continue
        if re.search(r"\b(?:do\s+not|don't|cannot|can't|no|not\s+available)\s+(?:offer|allow|support|have)?\s*$", prefix, re.I):
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
            if _PROGRAM_QUESTION.search(sentence):
                continue
            if _PROGRAM_SPECIFIC_VALUE.search(window):
                return True
            if (_PROGRAM_CATEGORICAL_ASSERTION.search(window)
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
    external_case_ids = {key for key, item in returned.items()
                         if item["source_type"] == _EXTERNAL_CASE_SOURCE_TYPE}
    adopted = set(decision["evidence_ids"])
    if _EXTERNAL_CASE_ATTRIBUTION.search(text) and not adopted & external_case_ids:
        raise PolicyViolation("EXTERNAL_CASE_EVIDENCE_REQUIRED")
    if adopted & external_case_ids:
        # Synthetic public references neither prove an institutional service
        # relationship nor establish a verified admission outcome.
        if adopted & case_ids:
            raise PolicyViolation("MIXED_CASE_PROVENANCE_UNSUPPORTED")
        if _EXTERNAL_CASE_TEXT.search(text) and not _EXTERNAL_CASE_ATTRIBUTION.search(text):
            raise PolicyViolation("EXTERNAL_CASE_ATTRIBUTION_REQUIRED")
        if _INSTITUTION_CASE_OWNERSHIP.search(text):
            raise PolicyViolation("EXTERNAL_CASE_NOT_INSTITUTION_SERVED")
        if _EXTERNAL_CASE_OUTCOME.search(text) and not _EXTERNAL_OUTCOME_CAVEAT.search(text):
            raise PolicyViolation("EXTERNAL_CASE_OUTCOME_UNVERIFIED")
        if _EXTERNAL_CASE_ATTRIBUTION.search(text) and not _EXTERNAL_OUTCOME_CAVEAT.search(text):
            raise PolicyViolation("EXTERNAL_CASE_UNVERIFIED_CAVEAT_REQUIRED")
        if _EXTERNAL_CASE_PREDICTION.search(text):
            raise PolicyViolation("EXTERNAL_CASE_PREDICTION_UNSUPPORTED")
    advantage_ids = {key for key, item in returned.items() if item["source_type"] == "approved_advantage"
                     and item["item"].get("advantage_id") == "ADV-001"}
    case_kind = _case_claim_kind(text)
    if case_kind == "SPECIFIC" and not adopted & case_ids:
        raise PolicyViolation("UNSUPPORTED_CASE_CLAIM")
    if case_kind == "GENERAL" and not adopted & (case_ids | advantage_ids):
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
                r"(?:\u6211\u4eec|\u673a\u6784|\u8001\u5e08).{0,12}(?:\u5e2e\u4f60|\u4ee3\u4f60|\u66ff\u4f60).{0,8}(?:\u9012\u4ea4|\u7f51\u7533|\u6587\u4e66\u64b0\u5199)|"
                r"\b(?:we|our\s+(?:team|advisors?))\b.{0,22}\b(?:submit|file|write|draft)\b.{0,18}\b(?:application|essay|personal statement)\b", text, re.I)):
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
    if _OVERSTATED_SERVICE_SCOPE.search(text) and not _OVERSTATED_SERVICE_SCOPE.search(base):
        raise PolicyViolation("CONVERSATION_OVERSTATED_SCOPE")
    if _STUDENT_PARTICIPATION.search(base) and not _STUDENT_PARTICIPATION.search(text):
        raise PolicyViolation("CONVERSATION_DROPPED_STUDENT_PARTICIPATION")
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
    if _EXTERNAL_CASE_ATTRIBUTION.search(text) and not _EXTERNAL_CASE_ATTRIBUTION.search(base):
        raise PolicyViolation("CONVERSATION_ADDED_EXTERNAL_CASE")
    if _EXTERNAL_CASE_ATTRIBUTION.search(base):
        if not _EXTERNAL_CASE_ATTRIBUTION.search(text):
            raise PolicyViolation("CONVERSATION_DROPPED_EXTERNAL_ATTRIBUTION")
        if _INSTITUTION_CASE_OWNERSHIP.search(text):
            raise PolicyViolation("CONVERSATION_CHANGED_EXTERNAL_CASE_SOURCE")
        if _EXTERNAL_CASE_OUTCOME.search(text) and not _EXTERNAL_OUTCOME_CAVEAT.search(text):
            raise PolicyViolation("CONVERSATION_UNVERIFIED_EXTERNAL_OUTCOME")
        if not _EXTERNAL_OUTCOME_CAVEAT.search(text):
            raise PolicyViolation("CONVERSATION_DROPPED_EXTERNAL_CAVEAT")
        if _EXTERNAL_CASE_PREDICTION.search(text):
            raise PolicyViolation("CONVERSATION_ADDED_EXTERNAL_PREDICTION")
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
