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
    ("DO_NOT_CONTACT", re.compile(r"^(?:\u8bf7|\u9ebb\u70e6)?(?:\u4e0d\u8981|\u522b|\u4e0d\u7528\u518d)(?:\u518d)?(?:\u8054\u7cfb|\u627e|\u53d1\u6d88\u606f\u7ed9)\u6211[。！! ]*$")),
    ("EXIT", re.compile(r"^(?:\u6211)?(?:\u4e0d\u8003\u8651\u4e86|\u4e0d\u9700\u8981\u4e86|\u4e0d\u60f3\u7ee7\u7eed\u4e86\u89e3\u4e86)[。！! ]*$")),
    ("OBJECTION_CLARIFIED", re.compile(r"^\u4e0d\u662f\u9884\u7b97\u95ee\u9898[，, ]+\u662f(?:\u6211)?\u4e0d\u6e05\u695a\u670d\u52a1\u8303\u56f4[。！! ]*$")),
    ("DEFERRED", re.compile(r"^(?:\u6211)?(?:\u518d\u60f3\u60f3|\u9700\u8981\u518d\u8003\u8651\u4e00\u4e0b|\u8fc7\u51e0\u5929\u518d\u8bf4)[。！! ]*$")),
    ("COMMITMENT_SIGNAL", re.compile(r"^(?:\u6211)?(?:\u613f\u610f\u7b7e\u7ea6|\u51b3\u5b9a\u7b7e\u7ea6\u4e86)[。！! ]*$")),
)


def assess_explicit_reply(raw_text: str) -> tuple[str, list[str], float, bool]:
    """Return (prediction, verbatim spans, confidence, may continue).

    Confidence describes pattern recognition only, not purchase likelihood.
    The human label is kept separate in the event store and never set here.
    """
    # Synthetic inputs carry a demo marker. Strip it only for classification;
    # the stored inbound text remains untouched and returned spans stay verbatim.
    text = re.sub(r"^(?:\u5408\u6210|DEMO)[：:]\s*", "", raw_text.strip(), count=1, flags=re.IGNORECASE)
    for signal, pattern in _EXPLICIT_SIGNALS:
        if pattern.fullmatch(text):
            return signal, [text], 1.0, signal not in {"EXIT", "DO_NOT_CONTACT"}
    return "UNCLEAR", [], 0.0, True
