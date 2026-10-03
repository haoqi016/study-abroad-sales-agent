"""Shared validation and response envelope for Sales V2 read-only tools."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_VERSION = "sales-tools.v1"
VALID_STATUSES = {"OK", "NO_RESULTS", "UNAVAILABLE", "INVALID_REQUEST", "POLICY_BLOCKED"}
MAX_CATALOG_BYTES = 1_000_000
_PRIVATE_PATTERN = re.compile(
    r"(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:\+?86[- ]?)?1[3-9]\d{9}|"
    # Unicode escapes keep the public source ASCII while retaining Chinese PII labels.
    r"(?:\u5fae\u4fe1|\u624b\u673a\u53f7|\u8eab\u4efd\u8bc1|"
    r"\u5ba2\u6237\u7f16\u53f7|case[_ -]?id|student[_ -]?id|\u59d3\u540d|"
    r"wechat|phone(?:\s+number)?|national\s+id|customer\s+id|full\s+name)\s*[:\uFF1A])",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def opaque_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def envelope(
    status: str,
    source_type: str,
    *,
    source_version: str | None = None,
    limitations: list[str] | None = None,
    data: list[dict] | None = None,
    error_code: str | None = None,
    retryable: bool = False,
) -> dict:
    if status not in VALID_STATUSES:
        raise ValueError("invalid tool status")
    return {
        "status": status,
        "tool_call_id": opaque_id("tc"),
        "tool_version": TOOL_VERSION,
        "source_type": source_type,
        "source_version": source_version,
        "retrieved_at": now_iso(),
        "limitations": limitations or [],
        "data": data or [],
        "error": {"code": error_code, "retryable": retryable} if error_code else None,
    }


def read_json_limited(path: str | Path, *, expected_sha256: str | None = None) -> Any:
    source = Path(path)
    if not source.is_file() or source.stat().st_size > MAX_CATALOG_BYTES:
        raise ValueError("catalog missing or too large")
    raw = source.read_bytes()
    if expected_sha256 and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("catalog digest mismatch")
    return json.loads(raw.decode("utf-8"))


def timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def safe_public_text(value: Any, *, max_length: int = 160) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value.strip()) <= max_length
        and not _PRIVATE_PATTERN.search(value)
        and not any(ord(c) < 32 for c in value)
    )


def approved_source(source: Any) -> bool:
    """Only explicit, versioned adapters can cross the source boundary."""
    return bool(
        source is not None
        and getattr(source, "approval_status", None) == "APPROVED"
        and safe_public_text(getattr(source, "source_version", None), max_length=100)
    )


class SourceTimeout(Exception):
    """An approved, read-only source did not complete within its contract SLA."""


def call_with_timeout(callable_, seconds: float):
    """Bound caller wait; source adapter is required to be side-effect free."""
    outcome: dict[str, Any] = {}

    def run():
        try:
            outcome["value"] = callable_()
        except Exception as exc:  # Source errors are data unavailability, not agent instructions.
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise SourceTimeout()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")
