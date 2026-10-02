"""Conservative classification of an approved draft versus actual human send.

This is a trace hint, never a send gate. Only mechanically provable style-only
edits are classified as non-material; all other edits remain review-worthy.
"""

from __future__ import annotations

import unicodedata


_TERMINAL_PUNCTUATION = {"。", ".", "!", "！", "~", "～"}


def _is_pictograph(char: str) -> bool:
    codepoint = ord(char)
    return 0x1F300 <= codepoint <= 0x1FAFF


def _style_core(text: str) -> str:
    # Only remove decoration at the end. Internal punctuation/spacing can be
    # material: 1.5万 and 15万, or 100 0 and 1000, are not equivalent.
    normalized = unicodedata.normalize("NFKC", text).strip()
    while normalized and (normalized[-1] in _TERMINAL_PUNCTUATION or
                          _is_pictograph(normalized[-1]) or normalized[-1].isspace()):
        normalized = normalized[:-1]
    return normalized


def sent_material_change_classification(approved_text: str, actual_sent_text: str) -> str:
    """Return NO_CHANGE, STYLE_ONLY, or POSSIBLE_MATERIAL.

    A nontrivial paraphrase may be style-only in reality, but this deterministic
    check cannot prove it and must not silently certify it as such.
    """
    if approved_text == actual_sent_text:
        return "NO_CHANGE"
    if _style_core(approved_text) == _style_core(actual_sent_text):
        return "STYLE_ONLY"
    return "POSSIBLE_MATERIAL"
