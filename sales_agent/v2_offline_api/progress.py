"""Conservative, source-bound signals for the synthetic offline workspace.

These patterns only recognize explicit, short student statements. Everything
else remains UNCLEAR for a person to interpret; they are not sales outcomes.
"""

from __future__ import annotations

import re


_EXPLICIT_SIGNALS = (
    ("DO_NOT_CONTACT", re.compile(r"^(?:please\s+)?(?:do\s+not|don't|stop)\s+(?:contact(?:ing)?|message|messaging|text(?:ing)?)\s+me(?:\s+again)?[.! ]*$", re.I)),
    ("EXIT", re.compile(r"^(?:i(?:'m| am)\s+)?(?:no\s+longer\s+interested|not\s+interested|not\s+considering\s+this)[.! ]*$", re.I)),
    ("OBJECTION_CLARIFIED", re.compile(r"^(?:(?:it'?s|it\s+is)\s+)?not\s+(?:about\s+)?(?:the\s+)?budget[.,; ]+(?:i(?:'m| am)\s+)?(?:unclear|unsure|confused)\s+about\s+(?:the\s+)?service\s+scope(?:[,; ]+especially\s+.{1,120})?[.! ]*$", re.I)),
    ("DEFERRED", re.compile(r"^(?:i(?:'ll| will)\s+)?(?:think\s+about\s+it|need\s+more\s+time|decide\s+later)[.! ]*$", re.I)),
    ("COMMITMENT_SIGNAL", re.compile(r"^(?:i(?:'m| am)\s+ready\s+to\s+sign|i(?:'ve| have)\s+decided\s+to\s+sign)[.! ]*$", re.I)),
    ("DO_NOT_CONTACT", re.compile(r"^(?:请|麻烦)?(?:不要|别|不用再)(?:再)?(?:联系|找|发消息给)我[。！! ]*$")),
    ("EXIT", re.compile(r"^(?:我)?(?:不考虑了|不需要了|不想继续了解了)[。！! ]*$")),
    ("OBJECTION_CLARIFIED", re.compile(r"^不是预算问题[，, ]+是(?:我)?不清楚服务范围[。！! ]*$")),
    ("DEFERRED", re.compile(r"^(?:我)?(?:再想想|需要再考虑一下|过几天再说)[。！! ]*$")),
    ("COMMITMENT_SIGNAL", re.compile(r"^(?:我)?(?:愿意签约|决定签约了)[。！! ]*$")),
)


def assess_explicit_reply(raw_text: str) -> tuple[str, list[str], float, bool]:
    """Return (prediction, verbatim spans, confidence, may continue).

    Confidence describes pattern recognition only, not purchase likelihood.
    The human label is kept separate in the event store and never set here.
    """
    # Synthetic inputs carry a demo marker. Strip it only for classification;
    # the stored inbound text remains untouched and returned spans stay verbatim.
    text = re.sub(r"^(?:合成|DEMO)[：:]\s*", "", raw_text.strip(), count=1, flags=re.IGNORECASE)
    for signal, pattern in _EXPLICIT_SIGNALS:
        if pattern.fullmatch(text):
            return signal, [text], 1.0, signal not in {"EXIT", "DO_NOT_CONTACT"}
    return "UNCLEAR", [], 0.0, True
