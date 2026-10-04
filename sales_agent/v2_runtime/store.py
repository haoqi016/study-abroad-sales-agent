"""Append-only local event ledger and typed V2 context assembly.

This is an offline foundation. It has no student-facing transport, cloud
identity, provider calls, or permission to send a message on its own.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4

from .sent_diff import sent_material_change_classification


class ContractViolation(ValueError):
    """The requested state transition would violate a frozen V2 invariant."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _offer_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _nonempty_strings(value: Any, *, allow_empty: bool = False) -> bool:
    return (isinstance(value, list) and (allow_empty or bool(value))
            and all(isinstance(item, str) and bool(item.strip()) for item in value))


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _opaque_id() -> str:
    return uuid4().hex


def _sent_offer_price(text: str) -> int | None:
    """Extract one explicit, affirmative sent quote; ambiguity stays unstructured."""
    amounts = list(re.finditer(
        r"(?<!\d)(?:[¥￥$£]\s*(\d{3,7})|(\d{3,7})\s*(?:\u5143|\u5757|RMB|CNY|USD|SGD|dollars?|pounds?))(?!\d)",
        text, re.I))
    if len(amounts) != 1 or not re.search(
        r"\u62a5\u4ef7|\u4ef7\u683c|\u603b\u4ef7|\u8d39\u7528|\u65b9\u6848|\u5168\u5957|\u6309|\u7ed9\u4f60|\u7ed9\u5230|\u53ea\u8981|\u6536\u4f60|¥|￥|"
        r"\b(?:quote|price|costs?|package|plan|offer|charge)\b|[$£]", text, re.I):
        return None
    match = amounts[0]
    before = text[max(0, match.start() - 45):match.start()]
    if re.search(r"\u4e0d\u662f|\u5e76\u975e|\u4e0d\u80fd|\u65e0\u6cd5|\u4e0d\u6309|\u539f\u4ef7|\u9884\u7b97|\u6700\u591a|\u4e0a\u9650|\u4e4b\u524d|\u539f\u672c|"
                 r"\b(?:not|cannot|can't|budget|at\s+most|up\s+to|original|previous)\b", before, re.I):
        return None
    return int(match.group(1) or match.group(2))


def _sent_offer_currency(text: str) -> str | None:
    """Currency is recorded only when the literal sent text identifies it."""
    if re.search(r"[¥￥]|\b(?:RMB|CNY)\b|\d\s*(?:\u5143|\u5757)", text, re.I):
        return "CNY"
    if re.search(r"\$|\b(?:USD|dollars?)\b", text, re.I):
        return "USD"
    if re.search(r"£|\b(?:GBP|pounds?)\b", text, re.I):
        return "GBP"
    if re.search(r"\bSGD\b", text, re.I):
        return "SGD"
    return None


def _sent_payment_terms(text: str) -> list[str]:
    """Keep only literal, affirmative payment phrases from the sent message."""
    candidates = re.finditer(
        r"\u4e00\u6b21\u4ed8\u6e05|\u5168\u6b3e\u652f\u4ed8|\u5206[\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+\u671f(?:\u4ed8\u6b3e)?|\u5206\u671f\u4ed8\u6b3e|"
        r"\b(?:pay\s+in\s+full|full\s+payment|pay\s+in\s+\d+\s+installments?|payment\s+plan|pay\s+half\s+(?:now|upfront))\b",
        text, re.I)
    terms = []
    for match in candidates:
        before = text[max(0, match.start() - 35):match.start()]
        if not re.search(r"(?:\u4e0d|\u4e0d\u80fd|\u65e0\u6cd5|\u5e76\u975e|\u4e0d\u662f|\b(?:not|no|cannot|can't|don't|do\s+not)\s+(?:(?:offer|allow|support|have)\s+)?(?:an?\s+)?)$", before, re.I) and match.group() not in terms:
            terms.append(match.group())
    return terms


def _snapshot_fields(value: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten record leaves; target arrays remain one mutable field each."""
    fields = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if path in {"student_id", "demo_only", "schema_version"}:
            continue
        if isinstance(item, dict):
            fields.update(_snapshot_fields(item, path))
        else:
            fields[path] = item
    return fields


_FIELD_SOURCE_TYPES = {"STUDENT_MESSAGE", "SALESPERSON_INPUT", "DETERMINISTIC_NORMALIZER", "AGENT_INFERENCE"}


class V2RuntimeStore:
    """One local database containing immutable events for multiple students.

    Public methods return deep-copied event dictionaries. A retry using the
    same `(student_id, idempotency_key)` returns the original result; reusing
    the key for a different request raises instead of silently changing data.
    Callers must authenticate and authorize salesperson identities separately.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = RLock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA busy_timeout = 5000")
        if self.db_path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                UNIQUE(student_id, revision)
            );
            CREATE INDEX IF NOT EXISTS idx_events_student ON events(student_id, revision);
            CREATE TABLE IF NOT EXISTS idempotency (
                student_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                event_id TEXT NOT NULL REFERENCES events(event_id),
                PRIMARY KEY(student_id, idempotency_key)
            );
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT, 'events_are_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT, 'events_are_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS idempotency_no_update BEFORE UPDATE ON idempotency
            BEGIN SELECT RAISE(ABORT, 'idempotency_is_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS idempotency_no_delete BEFORE DELETE ON idempotency
            BEGIN SELECT RAISE(ABORT, 'idempotency_is_immutable'); END;
            """
        )

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "V2RuntimeStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _begin(self) -> None:
        self._lock.acquire()
        try:
            self._db.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._lock.release()
            raise

    def _end(self, ok: bool) -> None:
        try:
            self._db.execute("COMMIT" if ok else "ROLLBACK")
        finally:
            self._lock.release()

    def _retry(self, student_id: str, key: str, request: dict) -> dict | None:
        row = self._db.execute(
            "SELECT request_digest, event_id FROM idempotency WHERE student_id=? AND idempotency_key=?",
            (student_id, key),
        ).fetchone()
        if row is None:
            return None
        if row["request_digest"] != _digest(request):
            raise ContractViolation("idempotency_key_reused_with_different_request")
        return self.get_event(row["event_id"])

    def _append(
        self,
        *,
        student_id: str,
        conversation_id: str,
        event_type: str,
        schema_version: str,
        actor: str,
        payload: dict,
        idempotency_key: str,
        request: dict,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict:
        if not student_id or not conversation_id or not idempotency_key:
            raise ContractViolation("missing_event_identity")
        revision = self._db.execute(
            "SELECT COALESCE(MAX(revision), 0)+1 FROM events WHERE student_id=?", (student_id,)
        ).fetchone()[0]
        event = {
            "schema_version": schema_version,
            "event_id": _opaque_id(),
            "student_id": student_id,
            "conversation_id": conversation_id,
            "causation_id": causation_id,
            "correlation_id": correlation_id or _opaque_id(),
            "actor": actor,
            "occurred_at": _utc_now(),
            "revision": revision,
            "idempotency_key": idempotency_key,
            "event_type": event_type,
            "payload": deepcopy(payload),
        }
        self._db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
            (
                event["event_id"], student_id, conversation_id, revision, event_type,
                schema_version, event["occurred_at"], json.dumps(event, ensure_ascii=False),
            ),
        )
        self._db.execute(
            "INSERT INTO idempotency VALUES (?,?,?,?)",
            (student_id, idempotency_key, _digest(request), event["event_id"]),
        )
        return deepcopy(event)

    def _append_side_effect(
        self, *, student_id: str, conversation_id: str, event_type: str,
        schema_version: str, payload: dict, causation_id: str, correlation_id: str,
        idempotency_key: str,
    ) -> dict:
        """Append a deterministic child event within its parent's transaction."""
        request = {"event_type": event_type, "payload": payload, "causation_id": causation_id}
        previous = self._retry(student_id, idempotency_key, request)
        if previous:
            return previous
        return self._append(
            student_id=student_id, conversation_id=conversation_id, event_type=event_type,
            schema_version=schema_version, actor="SYSTEM", payload=payload,
            idempotency_key=idempotency_key, request=request, causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def get_event(self, event_id: str) -> dict | None:
        row = self._db.execute("SELECT envelope_json FROM events WHERE event_id=?", (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_events(self, student_id: str, *, event_type: str | None = None) -> list[dict]:
        if event_type is None:
            rows = self._db.execute(
                "SELECT envelope_json FROM events WHERE student_id=? ORDER BY revision", (student_id,)
            )
        else:
            rows = self._db.execute(
                "SELECT envelope_json FROM events WHERE student_id=? AND event_type=? ORDER BY revision",
                (student_id, event_type),
            )
        return [json.loads(row[0]) for row in rows]

    def _require(self, event_id: str, event_type: str, student_id: str | None = None) -> dict:
        event = self.get_event(event_id)
        if event is None or event["event_type"] != event_type or (student_id and event["student_id"] != student_id):
            raise ContractViolation(f"required_{event_type.lower()}_event_not_found")
        return event

    def _write(self, student_id: str, key: str, request: dict, build: Any) -> dict:
        self._begin()
        ok = False
        try:
            previous = self._retry(student_id, key, request)
            if previous is not None:
                ok = True
                return previous
            result = build()
            ok = True
            return result
        finally:
            self._end(ok)

    def record_public_gold_stage(self, *, student_id: str, conversation_id: str,
                                 context_event_id: str, stage: str, output: dict,
                                 idempotency_key: str) -> dict:
        """Persist an internal synthetic exercise stage, never a student fact."""
        if stage not in {"D0", "G0", "D1", "G1", "C0", "C0_STYLE_CHECK",
                         "C0_REPAIR", "C0_STYLE_RECHECK"}:
            raise ContractViolation("invalid_public_gold_stage")
        if not isinstance(output, dict) or len(json.dumps(output, ensure_ascii=False)) > 30000:
            raise ContractViolation("public_gold_output_too_large")
        context = self.get_event(context_event_id)
        if not context or context["student_id"] != student_id or context["conversation_id"] != conversation_id:
            raise ContractViolation("gold_context_mismatch")
        request = {"type": "GOLD_STAGE_RECORDED", "context_event_id": context_event_id,
                   "stage": stage, "output": output}
        def build() -> dict:
            return self._append(student_id=student_id, conversation_id=conversation_id,
                                event_type="GOLD_STAGE_RECORDED", schema_version="public.gold-stage.v1",
                                actor="SYSTEM", payload={"context_event_id": context_event_id,
                                                         "stage": stage, "output": deepcopy(output)},
                                idempotency_key=idempotency_key, request=request,
                                causation_id=context_event_id)
        return self._write(student_id, idempotency_key, request, build)

    def public_gold_trace(self, student_id: str, context_event_id: str) -> list[dict]:
        return [event for event in self.list_events(student_id, event_type="GOLD_STAGE_RECORDED")
                if event["payload"]["context_event_id"] == context_event_id]

    def record_public_gold_resolution(self, *, student_id: str, conversation_id: str,
                                      pause_event_id: str, choice: str, reason: str,
                                      idempotency_key: str) -> dict:
        pause = self.get_event(pause_event_id)
        if not pause or pause["student_id"] != student_id or pause["conversation_id"] != conversation_id \
                or pause["event_type"] != "GOLD_STAGE_RECORDED" \
                or pause["payload"].get("stage") != "D1" \
                or pause["payload"].get("output", {}).get("status") != "ASK_HUMAN":
            raise ContractViolation("gold_pause_is_not_current")
        if choice != "FIRST" or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ContractViolation("invalid_gold_resolution")
        request = {"type": "GOLD_HUMAN_RESOLVED", "pause_event_id": pause_event_id,
                   "choice": choice, "reason": reason}
        def build() -> dict:
            previous = [event for event in self.list_events(student_id, event_type="GOLD_HUMAN_RESOLVED")
                        if event["payload"]["pause_event_id"] == pause_event_id]
            if previous:
                raise ContractViolation("gold_pause_already_resolved")
            return self._append(student_id=student_id, conversation_id=conversation_id,
                                event_type="GOLD_HUMAN_RESOLVED", schema_version="public.gold-resolution.v1",
                                actor="SALESPERSON", payload={"pause_event_id": pause_event_id,
                                                              "choice": choice, "reason": reason},
                                idempotency_key=idempotency_key, request=request,
                                causation_id=pause_event_id)
        return self._write(student_id, idempotency_key, request, build)

    def record_public_gold_resume_handoff(self, *, student_id: str, conversation_id: str,
                                          pause_event_id: str, idempotency_key: str) -> dict:
        """A retry marker with no model text or untrusted exception detail."""
        request = {"type": "GOLD_RESUME_HANDOFF", "pause_event_id": pause_event_id}
        def build() -> dict:
            return self._append(student_id=student_id, conversation_id=conversation_id,
                                event_type="GOLD_RESUME_HANDOFF", schema_version="public.gold-resume-handoff.v1",
                                actor="SYSTEM", payload={"pause_event_id": pause_event_id},
                                idempotency_key=idempotency_key, request=request,
                                causation_id=pause_event_id)
        return self._write(student_id, idempotency_key, request, build)

    def record_inbound(
        self, *, student_id: str, conversation_id: str, raw_text: str,
        idempotency_key: str, channel: str = "WECHAT", recorded_by: str = "salesperson",
        source_occurred_at: str | None = None, channel_message_id: str | None = None,
    ) -> dict:
        if channel not in {"XIAOHONGSHU", "WECHAT", "REFERRAL", "OTHER"}:
            raise ContractViolation("invalid_channel")
        request = {"type": "INBOUND_RECEIVED", "conversation_id": conversation_id, "raw_text": raw_text,
                   "channel": channel, "recorded_by": recorded_by,
                   "source_occurred_at": source_occurred_at, "channel_message_id": channel_message_id}

        def build() -> dict:
            event = self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="INBOUND_RECEIVED", schema_version="student.inbound-message.v1",
                actor="STUDENT", payload={"raw_text": raw_text, "channel": channel,
                "recorded_by": recorded_by, "source_occurred_at": source_occurred_at,
                "channel_message_id": channel_message_id,
                "raw_text_sha256": sha256(raw_text.encode("utf-8")).hexdigest(), "attachments": []},
                idempotency_key=idempotency_key, request=request,
            )
            self._cancel_reminders(student_id, {"SEND_DUE", "FOLLOW_UP_DUE"}, event)
            return event

        return self._write(student_id, idempotency_key, request, build)

    def record_internal_note(
        self, *, student_id: str, conversation_id: str, text: str, idempotency_key: str,
        recorded_by: str = "salesperson",
    ) -> dict:
        request = {"type": "INTERNAL_NOTE", "conversation_id": conversation_id,
                   "text": text, "recorded_by": recorded_by}
        return self._write(student_id, idempotency_key, request, lambda: self._append(
            student_id=student_id, conversation_id=conversation_id, event_type="INTERNAL_NOTE",
            schema_version="sales.internal-note.v1", actor="SALESPERSON",
            payload={"text": text, "recorded_by": recorded_by},
            idempotency_key=idempotency_key, request=request,
        ))

    def latest_inbound_interpretation(self, inbound_event_id: str) -> dict | None:
        inbound = self._require(inbound_event_id, "INBOUND_RECEIVED")
        events = self.list_events(inbound["student_id"], event_type="INBOUND_INTERPRETED")
        matches = [event for event in events if event["payload"]["inbound_event_id"] == inbound_event_id]
        return matches[-1] if matches else None

    def record_inbound_interpretation(
        self, *, inbound_event_id: str, normalized_meaning: str,
        normalized_meaning_spans: list[str], hypotheses: list[dict], unknowns: list[str],
        idempotency_key: str, actor: str = "DECISION_AGENT",
        supersedes_event_id: str | None = None,
    ) -> dict:
        """Append a source-bound reading; never edit the student's raw message."""
        inbound = self._require(inbound_event_id, "INBOUND_RECEIVED")
        student_id = inbound["student_id"]
        request = {"type": "INBOUND_INTERPRETED", "inbound_event_id": inbound_event_id,
                   "normalized_meaning": normalized_meaning,
                   "normalized_meaning_spans": normalized_meaning_spans,
                   "hypotheses": hypotheses, "unknowns": unknowns,
                   "actor": actor, "supersedes_event_id": supersedes_event_id}

        def build() -> dict:
            previous = self.latest_inbound_interpretation(inbound_event_id)
            if actor not in {"DECISION_AGENT", "SALESPERSON"}:
                raise ContractViolation("invalid_interpretation_actor")
            if (previous is None and supersedes_event_id is not None) or (
                previous is not None and supersedes_event_id != previous["event_id"]
            ):
                raise ContractViolation("stale_interpretation_version")
            if previous and previous["actor"] == "SALESPERSON" and actor != "SALESPERSON":
                raise ContractViolation("model_cannot_replace_human_interpretation")
            raw_text = inbound["payload"]["raw_text"]
            if not isinstance(normalized_meaning, str) or not normalized_meaning.strip():
                raise ContractViolation("invalid_normalized_meaning")
            if not _nonempty_strings(normalized_meaning_spans) or any(
                span not in raw_text for span in normalized_meaning_spans
            ):
                raise ContractViolation("normalized_meaning_requires_raw_span")
            if not isinstance(hypotheses, list) or not _nonempty_strings(unknowns, allow_empty=True):
                raise ContractViolation("invalid_interpretation_fields")
            for hypothesis in hypotheses:
                if not isinstance(hypothesis, dict) or set(hypothesis) != {
                    "code", "confidence", "supporting_spans", "contradicting_evidence"
                } or not isinstance(hypothesis["code"], str) or not hypothesis["code"].strip():
                    raise ContractViolation("invalid_interpretation_hypothesis")
                confidence = hypothesis["confidence"]
                if type(confidence) not in {float, int} or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ContractViolation("invalid_interpretation_confidence")
                if not _nonempty_strings(hypothesis["supporting_spans"]) or any(
                    span not in raw_text for span in hypothesis["supporting_spans"]
                ):
                    raise ContractViolation("hypothesis_requires_raw_span")
                if not _nonempty_strings(hypothesis["contradicting_evidence"], allow_empty=True):
                    raise ContractViolation("invalid_contradicting_evidence")
            version = previous["payload"]["interpretation_version"] + 1 if previous else 1
            return self._append(
                student_id=student_id, conversation_id=inbound["conversation_id"],
                event_type="INBOUND_INTERPRETED", schema_version="sales.inbound-interpretation.v1",
                actor=actor, payload={"inbound_event_id": inbound_event_id,
                                      "raw_text_sha256": inbound["payload"]["raw_text_sha256"],
                                      "interpretation_version": version,
                                      "supersedes_event_id": supersedes_event_id,
                                      "normalized_meaning": normalized_meaning,
                                      "normalized_meaning_spans": normalized_meaning_spans,
                                      "hypotheses": hypotheses, "unknowns": unknowns},
                idempotency_key=idempotency_key, request=request,
                causation_id=inbound_event_id, correlation_id=inbound["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def upsert_student_snapshot(
        self, *, student_id: str, conversation_id: str, snapshot: dict,
        idempotency_key: str, expected_revision: int | None = None,
        field_sources: dict[str, dict] | None = None,
    ) -> dict:
        if not isinstance(snapshot, dict) or not isinstance(field_sources or {}, dict):
            raise ContractViolation("invalid_snapshot_provenance")
        if snapshot.get("student_id") != student_id:
            raise ContractViolation("snapshot_student_id_mismatch")
        request = {"type": "STUDENT_SNAPSHOT", "conversation_id": conversation_id,
                   "snapshot": snapshot, "expected_revision": expected_revision,
                   "field_sources": field_sources}

        def build() -> dict:
            previous = self.get_student_snapshot(student_id)
            current = previous["snapshot_revision"] if previous else 0
            if expected_revision is not None and current != expected_revision:
                raise ContractViolation("stale_student_snapshot_revision")
            fields = _snapshot_fields(snapshot)
            prior_fields = _snapshot_fields(previous["snapshot"]) if previous else {}
            changed = {path for path, value in fields.items()
                       if path not in prior_fields or prior_fields[path] != value}
            if set(field_sources or {}) - changed:
                raise ContractViolation("field_source_without_changed_value")
            provenance = {}
            for path in changed:
                source = (field_sources or {}).get(path, {"source_type": "SALESPERSON_INPUT", "confirmed": True})
                if not isinstance(source, dict) or set(source) - {
                    "source_type", "source_event_id", "observed_at", "confirmed", "confidence", "evidence_span"
                }:
                    raise ContractViolation("invalid_snapshot_provenance")
                source_type = source.get("source_type")
                if source_type not in _FIELD_SOURCE_TYPES or type(source.get("confirmed")) is not bool:
                    raise ContractViolation("invalid_snapshot_provenance")
                source_event_id = source.get("source_event_id")
                source_event = self.get_event(source_event_id) if source_event_id else None
                if source_event_id and (source_event is None or source_event["student_id"] != student_id):
                    raise ContractViolation("field_source_missing_or_other_student")
                if source_type in {"STUDENT_MESSAGE", "DETERMINISTIC_NORMALIZER", "AGENT_INFERENCE"}:
                    if source_event is None or source_event["event_type"] != "INBOUND_RECEIVED":
                        raise ContractViolation("field_source_requires_student_message")
                if source_type == "AGENT_INFERENCE":
                    if path == "sales.contact_permission":
                        raise ContractViolation("agent_inference_cannot_set_contact_permission")
                    confidence = source.get("confidence")
                    span = source.get("evidence_span")
                    if (source["confirmed"] or not isinstance(confidence, (float, int))
                            or isinstance(confidence, bool) or not 0 <= confidence <= 1
                            or not isinstance(span, str) or not span
                            or span not in source_event["payload"]["raw_text"]):
                        raise ContractViolation("agent_inference_requires_evidence_and_confidence")
                if source_type == "STUDENT_MESSAGE" and source.get("confirmed"):
                    raise ContractViolation("student_message_needs_human_confirmation")
                observed_at = source.get("observed_at") or (source_event["payload"].get("source_occurred_at")
                    or source_event["occurred_at"] if source_event else None)
                if observed_at is not None:
                    try:
                        parsed = datetime.fromisoformat(observed_at)
                        if parsed.tzinfo is None:
                            raise ValueError
                    except (TypeError, ValueError) as exc:
                        raise ContractViolation("invalid_field_observed_at") from exc
                old_meta = self.get_student_field_provenance(student_id).get(path) if previous else None
                if (old_meta and old_meta["confirmed"] and old_meta["source_type"] == "SALESPERSON_INPUT"
                        and source_type == "AGENT_INFERENCE"):
                    raise ContractViolation("agent_inference_cannot_override_human_confirmed_field")
                provenance[path] = {"source_type": source_type, "source_event_id": source_event_id,
                                    "observed_at": observed_at, "confirmed": source["confirmed"]}
                if source_type == "AGENT_INFERENCE":
                    provenance[path].update(confidence=source["confidence"], evidence_span=source["evidence_span"])
            return self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="STUDENT_SNAPSHOT", schema_version="sales.student-record.v1",
                actor="SALESPERSON", payload={"snapshot_revision": current + 1, "snapshot": snapshot,
                                              "field_provenance": provenance},
                idempotency_key=idempotency_key, request=request,
            )

        return self._write(student_id, idempotency_key, request, build)

    def get_student_snapshot(self, student_id: str) -> dict | None:
        events = self.list_events(student_id, event_type="STUDENT_SNAPSHOT")
        if not events:
            return None
        payload = events[-1]["payload"]
        return {"snapshot_revision": payload["snapshot_revision"], "snapshot": deepcopy(payload["snapshot"]),
                "source_event_id": events[-1]["event_id"],
                "field_provenance": self.get_student_field_provenance(student_id)}

    def get_student_field_provenance(self, student_id: str) -> dict[str, dict]:
        """Rebuild the current field metadata from immutable student events."""
        result: dict[str, dict] = {}
        prior_fields: dict[str, Any] = {}
        sticky_dnc = False
        for event in self.list_events(student_id):
            if event["event_type"] == "STUDENT_SNAPSHOT":
                fields = _snapshot_fields(event["payload"]["snapshot"])
                recorded = event["payload"].get("field_provenance", {})
                for path, value in fields.items():
                    if path == "sales.contact_permission" and sticky_dnc and value != "DO_NOT_CONTACT":
                        continue
                    if path not in prior_fields or prior_fields[path] != value:
                        meta = recorded.get(path) or {"source_type": "SALESPERSON_INPUT",
                                                       "source_event_id": None, "observed_at": None,
                                                       "confirmed": False}
                        result[path] = {**meta, "source_event_id": meta.get("source_event_id") or event["event_id"],
                                        "observed_at": meta.get("observed_at") or event["occurred_at"]}
                result = {path: meta for path, meta in result.items() if path in fields}
                prior_fields = fields
                sticky_dnc = sticky_dnc or fields.get("sales.contact_permission") == "DO_NOT_CONTACT"
            elif event["event_type"] == "CONTACT_PERMISSION_SET":
                sticky_dnc = event["payload"]["permission"] == "DO_NOT_CONTACT"
                source_id = event["payload"].get("source_event_id")
                source = self.get_event(source_id) if source_id else None
                result["sales.contact_permission"] = {
                    "source_type": "STUDENT_MESSAGE" if source and source["event_type"] == "INBOUND_RECEIVED"
                                   else "SALESPERSON_INPUT",
                    "source_event_id": source_id or event["event_id"],
                    "observed_at": (source["payload"].get("source_occurred_at") or source["occurred_at"])
                                   if source and source["event_type"] == "INBOUND_RECEIVED"
                                   else event["occurred_at"],
                    "confirmed": not bool(source and source["event_type"] == "INBOUND_RECEIVED"),
                }
        return deepcopy(result)

    def set_contact_permission(
        self, *, student_id: str, conversation_id: str, permission: str,
        idempotency_key: str, source_event_id: str | None = None,
    ) -> dict:
        if permission not in {"ALLOWED", "LIMITED", "DO_NOT_CONTACT", "UNKNOWN"}:
            raise ContractViolation("invalid_contact_permission")
        request = {"type": "CONTACT_PERMISSION_SET", "conversation_id": conversation_id,
                   "permission": permission, "source_event_id": source_event_id}

        def build() -> dict:
            if source_event_id:
                source = self.get_event(source_event_id)
                if source is None or source["student_id"] != student_id:
                    raise ContractViolation("contact_permission_source_missing_or_other_student")
            event = self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="CONTACT_PERMISSION_SET", schema_version="sales.contact-permission.v1",
                actor="SALESPERSON", payload={"permission": permission, "source_event_id": source_event_id},
                idempotency_key=idempotency_key, request=request, causation_id=source_event_id,
            )
            if permission == "DO_NOT_CONTACT":
                self._cancel_reminders(student_id, {"SEND_DUE", "FOLLOW_UP_DUE"}, event)
            return event

        return self._write(student_id, idempotency_key, request, build)

    def get_contact_permission(self, student_id: str) -> str:
        permission = "UNKNOWN"
        for event in self.list_events(student_id):
            if event["event_type"] == "CONTACT_PERMISSION_SET":
                permission = event["payload"]["permission"]
            elif event["event_type"] == "STUDENT_SNAPSHOT":
                proposed = event["payload"]["snapshot"].get("sales", {}).get("contact_permission")
                if proposed == "DO_NOT_CONTACT" or (permission != "DO_NOT_CONTACT" and proposed in {
                    "ALLOWED", "LIMITED", "UNKNOWN"
                }):
                    permission = proposed
        return permission

    def _require_context_contact_permission_current(self, student_id: str, context: dict) -> None:
        """A later permission event invalidates this turn, even if it restores the same value."""
        expected = context["payload"]["current_state"]["contact_permission"]
        if self.get_contact_permission(student_id) != expected:
            raise ContractViolation("contact_permission_changed_after_context")
        if any(event["revision"] > context["revision"] for event in
               self.list_events(student_id, event_type="CONTACT_PERMISSION_SET")):
            raise ContractViolation("contact_permission_changed_after_context")

    def add_memory_item(
        self, *, student_id: str, conversation_id: str, category: str,
        key: str, value: Any, epistemic_status: str, source_event_ids: list[str],
        idempotency_key: str, evidence_span: str | None = None,
        confidence: float | None = None, valid_until: str | None = None,
        supersedes_memory_id: str | None = None, human_confirmed: bool = False,
    ) -> dict:
        categories = {"BACKGROUND", "GOAL", "BUDGET", "PREFERENCE", "DECISION_ROLE",
                      "CONTACT_PERMISSION", "OFFER_HISTORY", "COMMITMENT", "REJECTED_PATH",
                      "CURRENT_STUDENT_LEARNING"}
        statuses = {"CUSTOMER_STATED", "HUMAN_CONFIRMED", "SYSTEM_VERIFIED", "AGENT_HYPOTHESIS"}
        if category not in categories or epistemic_status not in statuses or not key or not source_event_ids:
            raise ContractViolation("invalid_memory_item")
        if epistemic_status == "HUMAN_CONFIRMED" and not human_confirmed:
            raise ContractViolation("human_confirmation_required")
        if epistemic_status == "AGENT_HYPOTHESIS" and (confidence is None or not 0 <= confidence <= 1):
            raise ContractViolation("hypothesis_requires_confidence")
        request = {"type": "STUDENT_MEMORY_ITEM", "conversation_id": conversation_id,
                   "category": category, "key": key, "value": value,
                   "epistemic_status": epistemic_status, "source_event_ids": source_event_ids,
                   "evidence_span": evidence_span, "confidence": confidence,
                   "valid_until": valid_until, "supersedes_memory_id": supersedes_memory_id,
                   "human_confirmed": human_confirmed}

        def build() -> dict:
            sources = [self.get_event(event_id) for event_id in source_event_ids]
            if any(event is None or event["student_id"] != student_id for event in sources):
                raise ContractViolation("memory_source_event_missing_or_other_student")
            if epistemic_status == "CUSTOMER_STATED":
                if not evidence_span:
                    raise ContractViolation("customer_stated_requires_evidence_span")
                if any(event["event_type"] != "INBOUND_RECEIVED" for event in sources):
                    raise ContractViolation("customer_stated_requires_inbound_source")
                if evidence_span and not any(evidence_span in event["payload"]["raw_text"] for event in sources):
                    raise ContractViolation("memory_evidence_span_not_in_student_raw_text")
            if epistemic_status == "HUMAN_CONFIRMED" and not any(
                event["actor"] == "SALESPERSON" for event in sources
            ):
                raise ContractViolation("human_confirmed_requires_human_source")
            if category == "OFFER_HISTORY":
                raise ContractViolation("offer_history_is_derived_from_actual_send")
            if supersedes_memory_id:
                memory = self._memory_event(student_id, supersedes_memory_id)
                if memory is None or memory["payload"]["key"] != key:
                    raise ContractViolation("superseded_memory_not_found_or_key_mismatch")
            memory = self.get_student_memory(student_id)
            return self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="STUDENT_MEMORY_ITEM", schema_version="sales.student-memory.v1",
                actor="SALESPERSON" if human_confirmed else "DECISION_AGENT",
                payload={"memory_id": _opaque_id(), "memory_revision": memory["memory_revision"] + 1,
                         "category": category, "key": key, "value": value,
                         "epistemic_status": epistemic_status, "source_event_ids": source_event_ids,
                         "evidence_span": evidence_span, "confidence": confidence,
                         "observed_at": _utc_now(), "valid_until": valid_until,
                         "supersedes_memory_id": supersedes_memory_id},
                idempotency_key=idempotency_key, request=request, causation_id=source_event_ids[-1],
            )

        return self._write(student_id, idempotency_key, request, build)

    def _memory_event(self, student_id: str, memory_id: str) -> dict | None:
        for event in self.list_events(student_id, event_type="STUDENT_MEMORY_ITEM"):
            if event["payload"]["memory_id"] == memory_id:
                return event
        return None

    def _sent_offer_memory(self, sent: dict) -> dict | None:
        """Build a stable index from the sent event, never from approval alone."""
        text = sent["payload"]["actual_sent_text"]
        price = _sent_offer_price(text)
        terms = _sent_payment_terms(text)
        if price is None and not terms and not re.search(r"\u62a5\u4ef7\u5f85\u5b9a|\u4ef7\u683c\u5f85\u5b9a|\u5177\u4f53\u4ef7\u683c\u8fd8\u9700\u8981\u786e\u8ba4|\b(?:price\s+(?:is\s+)?pending|price\s+to\s+be\s+confirmed|quote\s+pending)\b", text, re.I):
            # A draft or approval with no actual commercial content is not a sent Offer.
            return None
        value = {
            "actual_sent_text": text,
            "price": price,
            "price_status": "EXPLICIT_AMOUNT" if price is not None else "UNRESOLVED",
            "currency": _sent_offer_currency(text) if price is not None else None,
            "payment_terms": terms,
            "scope": [],
            "exclusions": [],
            "approved_draft_event_id": sent["payload"].get("approved_draft_event_id"),
        }
        sent_id = sent["event_id"]
        return {
            "memory_id": "sent-offer-" + sent_id,
            "memory_revision": sent["revision"],
            "category": "OFFER_HISTORY", "key": "offer_history.sent." + sent_id,
            "value": value, "epistemic_status": "SYSTEM_VERIFIED",
            "source_event_ids": [sent_id], "evidence_span": text,
            "confidence": None, "observed_at": sent["payload"]["sent_at"],
            "valid_until": None, "supersedes_memory_id": None, "active": True,
        }

    def get_student_memory(self, student_id: str) -> dict:
        events = self.list_events(student_id, event_type="STUDENT_MEMORY_ITEM")
        sent_events = self.list_events(student_id, event_type="HUMAN_SENT")
        superseded = {event["payload"]["supersedes_memory_id"] for event in events
                      if event["payload"]["supersedes_memory_id"]}
        now = _utc_now()
        items = []
        for event in events:
            item = deepcopy(event["payload"])
            item["active"] = item["memory_id"] not in superseded and (
                item["valid_until"] is None or item["valid_until"] >= now
            )
            items.append(item)
        sent_items = [(sent, self._sent_offer_memory(sent)) for sent in sent_events]
        sent_items = [(sent, item) for sent, item in sent_items if item is not None]
        items.extend(item for _, item in sent_items)
        return {"schema_version": "sales.student-memory.v1", "student_id": student_id,
                "memory_revision": len(events) + len(sent_items),
                "built_from_event_ids": [e["event_id"] for e in events] +
                                        [sent["event_id"] for sent, _ in sent_items],
                "items": items}

    def customer_visible_history(self, student_id: str) -> list[dict]:
        visible = []
        for event in self.list_events(student_id):
            if event["event_type"] == "INBOUND_RECEIVED":
                visible.append({"role": "student", "text": event["payload"]["raw_text"],
                                "event_id": event["event_id"]})
            elif event["event_type"] == "HUMAN_SENT":
                visible.append({"role": "sales", "text": event["payload"]["actual_sent_text"],
                                "event_id": event["event_id"]})
        return visible

    def latest_decision(self, student_id: str) -> dict | None:
        events = self.list_events(student_id, event_type="DECISION_READY")
        return events[-1] if events else None

    def assemble_turn_context(
        self, *, student_id: str, conversation_id: str,
        latest_observation_event_ids: list[str], idempotency_key: str,
        global_goal_version: str = "sales-business-goal.v2",
        policy_versions: list[str] | None = None,
        relevant_memory_ids: list[str] | None = None,
    ) -> dict:
        policy_versions = policy_versions or []
        relevant_memory_ids = relevant_memory_ids or []
        request = {"type": "TURN_CONTEXT_BUILT", "conversation_id": conversation_id,
                   "latest_observation_event_ids": latest_observation_event_ids,
                   "global_goal_version": global_goal_version,
                   "policy_versions": policy_versions, "relevant_memory_ids": relevant_memory_ids}

        def build() -> dict:
            snapshot = self.get_student_snapshot(student_id)
            memory = self.get_student_memory(student_id)
            observations = [self.get_event(event_id) for event_id in latest_observation_event_ids]
            bad_observations = any(event is None or event["student_id"] != student_id for event in observations)
            inbound_events = self.list_events(student_id, event_type="INBOUND_RECEIVED")
            latest_inbound = inbound_events[-1] if inbound_events else None
            latest_interpretation = (self.latest_inbound_interpretation(latest_inbound["event_id"])
                                     if latest_inbound else None)
            stale_observation = bool(latest_inbound and latest_inbound["event_id"] not in latest_observation_event_ids)
            permission = self.get_contact_permission(student_id)
            visible = self.customer_visible_history(student_id)
            active_items = [item for item in memory["items"] if item["active"]]
            essential_categories = {"BACKGROUND", "GOAL", "BUDGET", "PREFERENCE", "DECISION_ROLE", "CONTACT_PERMISSION",
                                    "OFFER_HISTORY", "COMMITMENT", "REJECTED_PATH"}
            mandatory = [item for item in active_items if item["category"] in essential_categories]
            selected_ids = {item["memory_id"] for item in mandatory} | set(relevant_memory_ids)
            selected = [item for item in active_items if item["memory_id"] in selected_ids]
            unresolved_conflicts = []
            confirmed_by_key: dict[str, set[str]] = {}
            for item in active_items:
                if item["epistemic_status"] in {"CUSTOMER_STATED", "HUMAN_CONFIRMED", "SYSTEM_VERIFIED"}:
                    confirmed_by_key.setdefault(item["key"], set()).add(_digest(item["value"]))
            unresolved_conflicts = [key for key, values in confirmed_by_key.items() if len(values) > 1]
            last_decision = self.latest_decision(student_id)
            previous_draft_ids = ({event["event_id"] for event in self.list_events(student_id, event_type="DRAFTED")
                                   if event["payload"]["decision_event_id"] == last_decision["event_id"]}
                                  if last_decision else set())
            previous_sent_event_ids = [event["event_id"] for event in self.list_events(student_id, event_type="HUMAN_SENT")
                                       if event["payload"].get("approved_draft_event_id") in previous_draft_ids]
            pending_offers = [event["event_id"] for event in self.list_events(student_id, event_type="DECISION_READY")
                              if event["payload"].get("offer", {}).get("state") == "CUSTOM_OFFER_PROPOSAL"
                              and self._latest_custom_offer_action(event["event_id"]) is None]
            offer_state = last_decision["payload"].get("offer", {}).get("state", "NONE") if last_decision else "NONE"
            if last_decision and offer_state == "CUSTOM_OFFER_PROPOSAL" and self._offer_approved(last_decision["event_id"]):
                offer_state = "APPROVED_CUSTOM_OFFER"
            completeness = (snapshot is not None and not bad_observations and not unresolved_conflicts
                            and not stale_observation and latest_inbound is not None and bool(policy_versions))
            context = {
                "schema_version": "sales.turn-context.v1", "context_id": _opaque_id(), "turn_id": _opaque_id(),
                "student_id": student_id,
                "student_snapshot_revision": snapshot["snapshot_revision"] if snapshot else 0,
                "student_memory_revision": memory["memory_revision"],
                "student_record_field_provenance": snapshot["field_provenance"] if snapshot else {},
                "global_goal": {"version": global_goal_version,
                                "statement": "Support an appropriate enrollment within verified facts, delivery authority, and the student's wishes."},
                "policy_versions": policy_versions,
                "current_state": {
                    "sales_stage": snapshot["snapshot"].get("sales", {}).get("stage", "NEW") if snapshot else "UNKNOWN",
                    "current_objection": snapshot["snapshot"].get("sales", {}).get("current_objection") if snapshot else None,
                    "contact_permission": permission,
                    "offer_state": offer_state,
                },
                "latest_observation_event_ids": latest_observation_event_ids,
                "latest_observations": observations,
                "latest_student_message_event_id": latest_inbound["event_id"] if latest_inbound else None,
                "latest_student_raw_text": latest_inbound["payload"]["raw_text"] if latest_inbound else None,
                "latest_inbound_interpretation_event_id": (latest_interpretation["event_id"]
                                                          if latest_interpretation else None),
                "latest_inbound_interpretation_status": ("RECORDED" if latest_interpretation else "PENDING"),
                "latest_inbound_interpretation": (deepcopy(latest_interpretation["payload"])
                                                  if latest_interpretation else None),
                "customer_visible_history_event_ids": [item["event_id"] for item in visible],
                "customer_visible_history": visible,
                "mandatory_memory_ids": [item["memory_id"] for item in mandatory],
                "relevant_memory_ids": [item["memory_id"] for item in selected if item not in mandatory],
                "memory_items": selected,
                "working_memory": {
                    "current_hypotheses": last_decision["payload"].get("hypotheses", []) if last_decision else [],
                    "active_objective_id": last_decision["payload"].get("current_objective", {}).get("objective_id") if last_decision else None,
                    "previous_objective": deepcopy(last_decision["payload"].get("current_objective")) if last_decision else None,
                    "previous_action_plan": deepcopy(last_decision["payload"].get("action_plan")) if last_decision else None,
                    "previous_customer_message_sent_event_ids": previous_sent_event_ids,
                    "blocked_paths": [], "pending_approvals": pending_offers,
                    "open_questions": last_decision["payload"].get("unknowns", []) if last_decision else [],
                    "retrieved_evidence_ids": [],
                },
                "context_manifest": {
                    "omitted_noncritical_memory_count": len(active_items) - len(selected),
                    "truncated_customer_history": False,
                    "critical_context_complete": completeness,
                    "missing": (["student_snapshot"] if snapshot is None else [])
                               + (["latest_observation"] if bad_observations else [])
                               + (["latest_student_message"] if latest_inbound is None else [])
                               + (["latest_student_message_not_in_observations"] if stale_observation else [])
                               + (["policy_versions"] if not policy_versions else [])
                               + (["memory_conflict:" + key for key in unresolved_conflicts]),
                },
            }
            return self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="TURN_CONTEXT_BUILT", schema_version="sales.turn-context.v1",
                actor="SYSTEM", payload=context, idempotency_key=idempotency_key,
                request=request, causation_id=latest_observation_event_ids[-1] if latest_observation_event_ids else None,
            )

        return self._write(student_id, idempotency_key, request, build)

    def _offer_approved(self, proposal_decision_event_id: str) -> bool:
        proposal = self.get_event(proposal_decision_event_id)
        if proposal is None:
            return False
        return any(event["payload"].get("proposal_decision_event_id") == proposal_decision_event_id
                   for event in self.list_events(proposal["student_id"], event_type="CUSTOM_OFFER_APPROVED"))

    def _latest_custom_offer_action(self, proposal_decision_event_id: str) -> dict | None:
        proposal = self.get_event(proposal_decision_event_id)
        if proposal is None:
            return None
        return next((event for event in reversed(self.list_events(proposal["student_id"]))
                     if event["event_type"] in {"CUSTOM_OFFER_APPROVED", "CUSTOM_OFFER_REVIEW"}
                     and event["payload"].get("proposal_decision_event_id") == proposal_decision_event_id), None)

    def _valid_custom_approval(self, approval: dict, offer: dict, student_id: str) -> None:
        data = approval["payload"]
        approved = data.get("approved_offer")
        if not isinstance(approved, dict) or approval["student_id"] != student_id:
            raise ContractViolation("custom_offer_approval_invalid")
        if (data.get("offer_id") != offer.get("offer_id") or
                data.get("offer_version") != offer.get("offer_version") or
                approved.get("offer_id") != data.get("offer_id") or
                approved.get("offer_version") != data.get("offer_version") or
                approved.get("source_proposal_id") != data.get("proposal_decision_event_id") or
                approved.get("student_id") != student_id or
                approved.get("offer_state") != "APPROVED_CUSTOM_OFFER"):
            raise ContractViolation("custom_offer_approval_mismatch")
        proposal = self._require(data["proposal_decision_event_id"], "DECISION_READY", student_id)
        if proposal["payload"].get("offer", {}).get("state") != "CUSTOM_OFFER_PROPOSAL":
            raise ContractViolation("custom_offer_source_not_proposal")
        now = datetime.now(timezone.utc)
        approvals = approved.get("approved_by")
        if (not _nonempty_strings(approved.get("approved_scope")) or
                not _nonempty_strings(approved.get("approved_exclusions"), allow_empty=True) or
                type(approved.get("approved_price")) is not int or approved["approved_price"] < 0 or
                not _nonempty_strings(approved.get("approved_payment_terms")) or
                ("approved_delivery_conditions" in approved and
                 not _nonempty_strings(approved["approved_delivery_conditions"], allow_empty=True)) or
                "valid_until" not in approved or
                not isinstance(approvals, list) or len(approvals) != 3 or
                any(not isinstance(item, dict) or item.get("role") not in
                    ("PRODUCT", "DELIVERY", "PRICING") or
                    not isinstance(item.get("actor"), str) or not item["actor"].strip() or
                    (_offer_time(item.get("approved_at")) is None or
                     _offer_time(item.get("approved_at")) < _offer_time(proposal["occurred_at"]) or
                     _offer_time(item.get("approved_at")) > now)
                    for item in approvals) or
                {item["role"] for item in approvals} != {"PRODUCT", "DELIVERY", "PRICING"}):
            raise ContractViolation("custom_offer_approval_invalid")
        start = _offer_time(approved.get("valid_from"))
        end = _offer_time(approved.get("valid_until")) if approved.get("valid_until") is not None else None
        if start is None or start > now or (approved.get("valid_until") is not None and end is None) or (
                end is not None and (end <= start or now >= end)):
            raise ContractViolation("custom_offer_not_current")
        versions = [event for event in self.list_events(student_id, event_type="CUSTOM_OFFER_APPROVED")
                    if event["payload"].get("offer_id") == data["offer_id"]]
        if versions and max(event["payload"]["offer_version"] for event in versions) != data["offer_version"]:
            raise ContractViolation("custom_offer_version_not_current")
        if any(event["revision"] > approval["revision"]
               and event["payload"].get("offer_id") == data["offer_id"]
               for event in self.list_events(student_id, event_type="CUSTOM_OFFER_REVIEW")):
            raise ContractViolation("custom_offer_requires_reapproval")

    def _passed_gate(self, subject_event_id: str, stage: str) -> bool:
        subject = self.get_event(subject_event_id)
        if subject is None:
            return False
        gates = [event for event in self.list_events(subject["student_id"], event_type="GATE_RESULT")
                 if event["payload"]["subject_event_id"] == subject_event_id
                 and event["payload"]["gate_stage"] == stage]
        return bool(gates and gates[-1]["payload"]["passed"]
                    and gates[-1]["payload"]["required_action"] == "ALLOW")

    def record_gate_result(
        self, *, subject_event_id: str, gate_stage: str, passed: bool,
        hard_violations: list[str], warnings: list[str], required_action: str,
        idempotency_key: str, checked_program_evidence_ids: list[str] | None = None,
        checked_offer_id: str | None = None, checked_offer_version: int | None = None,
    ) -> dict:
        subject = self.get_event(subject_event_id)
        if subject is None:
            raise ContractViolation("gate_subject_missing")
        student_id = subject["student_id"]
        request = {"type": "GATE_RESULT", "subject_event_id": subject_event_id,
                   "gate_stage": gate_stage, "passed": passed,
                   "hard_violations": hard_violations, "warnings": warnings,
                   "required_action": required_action,
                   "checked_program_evidence_ids": checked_program_evidence_ids or [],
                   "checked_offer_id": checked_offer_id, "checked_offer_version": checked_offer_version}

        def build() -> dict:
            expected_type = {"PRE_CONVERSATION": "DECISION_READY",
                             "POST_CONVERSATION": "DRAFTED"}.get(gate_stage)
            if expected_type is None or subject["event_type"] != expected_type:
                raise ContractViolation("invalid_gate_stage_or_subject")
            if required_action not in {"ALLOW", "REVISE", "APPROVE", "HANDOFF", "STOP"}:
                raise ContractViolation("invalid_gate_required_action")
            if passed and (hard_violations or required_action != "ALLOW"):
                raise ContractViolation("passing_gate_cannot_have_hard_violation")
            return self._append(
                student_id=student_id, conversation_id=subject["conversation_id"],
                event_type="GATE_RESULT", schema_version="sales.gate-result.v1",
                actor="SYSTEM", payload={"subject_event_id": subject_event_id,
                "gate_stage": gate_stage, "passed": passed,
                "hard_violations": hard_violations, "warnings": warnings,
                "checked_program_evidence_ids": checked_program_evidence_ids or [],
                "checked_offer_id": checked_offer_id,
                "checked_offer_version": checked_offer_version,
                "required_action": required_action},
                idempotency_key=idempotency_key, request=request,
                causation_id=subject_event_id, correlation_id=subject["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def record_decision(
        self, *, student_id: str, conversation_id: str, payload: dict,
        idempotency_key: str,
    ) -> dict:
        request = {"type": "DECISION_READY", "conversation_id": conversation_id, "payload": payload}

        def build() -> dict:
            context_id = payload.get("turn_context_id")
            contexts = [event for event in self.list_events(student_id, event_type="TURN_CONTEXT_BUILT")
                        if event["payload"].get("context_id") == context_id]
            if not contexts:
                raise ContractViolation("decision_requires_turn_context")
            context = contexts[-1]
            latest_context = self.list_events(student_id, event_type="TURN_CONTEXT_BUILT")[-1]
            if context["event_id"] != latest_context["event_id"]:
                raise ContractViolation("turn_context_is_not_current")
            latest_inbound = self.list_events(student_id, event_type="INBOUND_RECEIVED")
            if latest_inbound and latest_inbound[-1]["revision"] > context["revision"]:
                raise ContractViolation("new_student_message_after_turn_context")
            manifest = context["payload"]["context_manifest"]
            if not manifest["critical_context_complete"]:
                raise ContractViolation("critical_context_incomplete")
            self._require_context_contact_permission_current(student_id, context)
            if payload.get("student_memory_revision") != context["payload"]["student_memory_revision"]:
                raise ContractViolation("stale_student_memory_revision")
            if payload.get("student_snapshot_revision") != context["payload"]["student_snapshot_revision"]:
                raise ContractViolation("stale_student_snapshot_revision")
            interpretation_id = payload.get("inbound_interpretation_event_id")
            if interpretation_id is not None:
                inbound_id = context["payload"]["latest_student_message_event_id"]
                current_interpretation = self.latest_inbound_interpretation(inbound_id)
                if current_interpretation is None or current_interpretation["event_id"] != interpretation_id:
                    raise ContractViolation("stale_inbound_interpretation")
            if self.get_student_memory(student_id)["memory_revision"] != payload["student_memory_revision"]:
                raise ContractViolation("student_memory_changed_after_context")
            snapshot = self.get_student_snapshot(student_id)
            if snapshot is None or snapshot["snapshot_revision"] != payload["student_snapshot_revision"]:
                raise ContractViolation("student_snapshot_changed_after_context")
            if conversation_id != context["conversation_id"]:
                raise ContractViolation("decision_context_conversation_mismatch")
            if payload.get("input_event_ids") != context["payload"]["latest_observation_event_ids"]:
                raise ContractViolation("decision_input_events_do_not_match_context")
            for field in ("hypotheses", "unknowns", "selected_strategy", "previous_objective_assessment",
                          "current_objective", "action_plan", "offer", "content_contract", "evidence_ids",
                          "stop_required", "human_approval_required"):
                if field not in payload:
                    raise ContractViolation(f"decision_missing_{field}")
            objective = payload["current_objective"]
            for field in ("objective_id", "goal", "why_now", "trigger_event_ids", "status",
                          "success_signals", "failure_signals", "attempt_count", "max_attempts"):
                if field not in objective:
                    raise ContractViolation(f"objective_missing_{field}")
            if objective["attempt_count"] < 1 or objective["max_attempts"] < objective["attempt_count"]:
                raise ContractViolation("objective_attempt_boundary_invalid")
            if objective["status"] not in {"ACTIVE", "STOPPED"}:
                raise ContractViolation("invalid_objective_status")
            if not objective["trigger_event_ids"] or not set(objective["trigger_event_ids"]).issubset(
                set(context["payload"]["latest_observation_event_ids"])
            ):
                raise ContractViolation("objective_triggers_not_in_context")
            if objective["status"] == "STOPPED" and payload["action_plan"].get("selected_action") is not None:
                raise ContractViolation("stopped_objective_cannot_have_sales_action")
            if payload["human_approval_required"] is not True:
                raise ContractViolation("v2_requires_human_approval")
            last_decision = self.latest_decision(student_id)
            assessment = payload["previous_objective_assessment"]
            if last_decision:
                previous = last_decision["payload"]["current_objective"]
                if assessment.get("objective_id") != previous["objective_id"]:
                    raise ContractViolation("previous_objective_assessment_mismatch")
                if objective.get("previous_objective_id") != previous["objective_id"]:
                    raise ContractViolation("previous_objective_link_mismatch")
                if objective["objective_id"] == previous["objective_id"]:
                    if assessment.get("result") not in {"CONTINUING", "UNKNOWN"}:
                        raise ContractViolation("blocked_or_completed_objective_cannot_continue")
                    if objective["attempt_count"] != previous["attempt_count"] + 1:
                        raise ContractViolation("continuing_objective_attempt_count_mismatch")
                elif assessment.get("result") not in {"ACHIEVED", "BLOCKED", "ABANDONED", "UNKNOWN"}:
                    raise ContractViolation("new_objective_requires_previous_assessment")
            elif assessment.get("objective_id") is not None:
                raise ContractViolation("previous_objective_does_not_exist")
            if self.get_contact_permission(student_id) == "DO_NOT_CONTACT" and not payload["stop_required"]:
                raise ContractViolation("do_not_contact_requires_stop")
            offer = payload["offer"]
            if offer.get("state") not in {"STANDARD_OFFER", "CUSTOM_OFFER_PROPOSAL", "APPROVED_CUSTOM_OFFER", "NONE"}:
                raise ContractViolation("invalid_offer_state")
            if offer["state"] == "APPROVED_CUSTOM_OFFER":
                approval_id = offer.get("approval_event_id")
                approval = self._require(approval_id, "CUSTOM_OFFER_APPROVED", student_id)
                self._valid_custom_approval(approval, offer, student_id)
            return self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="DECISION_READY", schema_version="sales.decision.v2.3",
                actor="DECISION_AGENT", payload=payload,
                idempotency_key=idempotency_key, request=request,
                causation_id=context["event_id"], correlation_id=context["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def review_custom_offer(
        self, *, proposal_decision_event_id: str, action: str, offer_id: str,
        comment: str, idempotency_key: str, edited_offer: dict | None = None,
        reviewed_by: str = "salesperson",
    ) -> dict:
        proposal = self._require(proposal_decision_event_id, "DECISION_READY")
        student_id = proposal["student_id"]
        request = {"type": "CUSTOM_OFFER_REVIEW", "proposal_decision_event_id": proposal_decision_event_id,
                   "action": action, "offer_id": offer_id, "comment": comment,
                   "edited_offer": edited_offer, "reviewed_by": reviewed_by}

        def build() -> dict:
            if proposal["payload"].get("offer", {}).get("state") != "CUSTOM_OFFER_PROPOSAL":
                raise ContractViolation("not_a_custom_offer_proposal")
            if action not in {"REQUEST_CHANGES", "REJECT"}:
                raise ContractViolation("invalid_custom_offer_review_action")
            if (not isinstance(offer_id, str) or not offer_id.strip() or
                    not isinstance(comment, str) or not comment.strip() or
                    not isinstance(reviewed_by, str) or not reviewed_by.strip() or
                    edited_offer is not None and not isinstance(edited_offer, dict)):
                raise ContractViolation("invalid_custom_offer_review")
            if self._latest_custom_offer_action(proposal_decision_event_id) is not None:
                raise ContractViolation("custom_offer_already_reviewed")
            source = proposal["payload"]["offer"].get("proposal")
            if not isinstance(source, dict):
                raise ContractViolation("custom_offer_source_proposal_required")
            prior = [event for event in self.list_events(student_id, event_type="CUSTOM_OFFER_APPROVED")
                     if event["payload"].get("offer_id") == offer_id]
            return self._append(
                student_id=student_id, conversation_id=proposal["conversation_id"],
                event_type="CUSTOM_OFFER_REVIEW", schema_version="sales.custom-offer-review.v1",
                actor="SALESPERSON", payload={"proposal_decision_event_id": proposal_decision_event_id,
                "proposal_original": deepcopy(source), "action": action, "offer_id": offer_id,
                "previous_approval_event_id": prior[-1]["event_id"] if prior else None,
                "next_offer_version": max((event["payload"]["offer_version"] for event in prior), default=0) + 1,
                "human_edited_offer": deepcopy(edited_offer), "comment": comment,
                "reviewed_by": reviewed_by, "reviewed_at": _utc_now()},
                idempotency_key=idempotency_key, request=request,
                causation_id=proposal_decision_event_id, correlation_id=proposal["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def approve_custom_offer(
        self, *, proposal_decision_event_id: str, offer_id: str, offer_version: int,
        approved_offer: dict, idempotency_key: str, approved_by: str = "pricing-owner",
        comment: str = "",
    ) -> dict:
        proposal = self._require(proposal_decision_event_id, "DECISION_READY")
        student_id = proposal["student_id"]
        request = {"type": "CUSTOM_OFFER_APPROVED", "proposal_decision_event_id": proposal_decision_event_id,
                   "offer_id": offer_id, "offer_version": offer_version, "approved_offer": approved_offer,
                   "approved_by": approved_by, "comment": comment}

        def build() -> dict:
            if proposal["payload"]["offer"]["state"] != "CUSTOM_OFFER_PROPOSAL":
                raise ContractViolation("not_a_custom_offer_proposal")
            source = proposal["payload"]["offer"].get("proposal")
            if (not isinstance(source, dict) or
                    not _nonempty_strings(source.get("proposed_scope")) or
                    not _nonempty_strings(source.get("explicit_exclusions"), allow_empty=True) or
                    type(source.get("proposed_price")) is not int or source["proposed_price"] < 0 or
                    not _nonempty_strings(source.get("payment_terms"))):
                raise ContractViolation("custom_offer_source_proposal_required")
            if (not isinstance(offer_id, str) or not offer_id.strip() or
                    type(offer_version) is not int or offer_version < 1 or
                    not isinstance(approved_offer, dict)):
                raise ContractViolation("invalid_approved_offer")
            if (approved_offer.get("offer_state") != "APPROVED_CUSTOM_OFFER" or
                    "valid_until" not in approved_offer or
                    approved_offer.get("offer_id") != offer_id or
                    approved_offer.get("offer_version") != offer_version or
                    approved_offer.get("student_id") != student_id or
                    approved_offer.get("source_proposal_id") != proposal_decision_event_id or
                    not _nonempty_strings(approved_offer.get("approved_scope")) or
                    not _nonempty_strings(approved_offer.get("approved_exclusions"), allow_empty=True) or
                    type(approved_offer.get("approved_price")) is not int or
                    approved_offer["approved_price"] < 0 or
                    not _nonempty_strings(approved_offer.get("approved_payment_terms")) or
                    ("approved_delivery_conditions" in approved_offer and
                     not _nonempty_strings(approved_offer["approved_delivery_conditions"], allow_empty=True)) or
                    not isinstance(comment, str)):
                raise ContractViolation("invalid_approved_offer")
            now = datetime.now(timezone.utc)
            start = _offer_time(approved_offer.get("valid_from"))
            end = _offer_time(approved_offer.get("valid_until")) if approved_offer.get("valid_until") is not None else None
            if (start is None or start > now or end is not None and (end <= start or now >= end) or
                    approved_offer.get("valid_until") is not None and end is None):
                raise ContractViolation("invalid_custom_offer_validity")
            approvals = approved_offer.get("approved_by")
            if not isinstance(approvals, list) or len(approvals) != 3 or (
                    {item.get("role") for item in approvals if isinstance(item, dict)
                     and isinstance(item.get("role"), str)}
                    != {"PRODUCT", "DELIVERY", "PRICING"}):
                raise ContractViolation("custom_offer_roles_missing")
            proposal_time = _offer_time(proposal["occurred_at"])
            for item in approvals:
                if not isinstance(item, dict) or not isinstance(item.get("actor"), str) or not item["actor"].strip():
                    raise ContractViolation("custom_offer_approver_invalid")
                signed_at = _offer_time(item.get("approved_at"))
                if signed_at is None or signed_at < proposal_time or signed_at > now:
                    raise ContractViolation("custom_offer_approval_time_invalid")
            prior = [event for event in self.list_events(student_id, event_type="CUSTOM_OFFER_APPROVED")
                     if event["payload"].get("offer_id") == offer_id]
            if offer_version != (max((event["payload"]["offer_version"] for event in prior), default=0) + 1):
                raise ContractViolation("custom_offer_version_not_next")
            if self._offer_approved(proposal_decision_event_id):
                raise ContractViolation("custom_offer_proposal_already_approved")
            if self._latest_custom_offer_action(proposal_decision_event_id) is not None:
                raise ContractViolation("custom_offer_already_reviewed")
            previous_approval = max(prior, key=lambda event: event["payload"]["offer_version"]) if prior else None
            human_edits = {
                "approved_scope": deepcopy(approved_offer["approved_scope"]),
                "approved_exclusions": deepcopy(approved_offer["approved_exclusions"]),
                "approved_price": approved_offer["approved_price"],
                "approved_payment_terms": deepcopy(approved_offer["approved_payment_terms"]),
                "valid_from": approved_offer["valid_from"],
                "valid_until": approved_offer["valid_until"],
            }
            if "approved_delivery_conditions" in approved_offer:
                human_edits["approved_delivery_conditions"] = deepcopy(
                    approved_offer["approved_delivery_conditions"])
            return self._append(
                student_id=student_id, conversation_id=proposal["conversation_id"],
                event_type="CUSTOM_OFFER_APPROVED", schema_version="sales.custom-offer-approval.v1",
                actor="SALESPERSON", payload={"proposal_decision_event_id": proposal_decision_event_id,
                "offer_id": offer_id, "offer_version": offer_version, "approved_offer": approved_offer,
                "proposal_original": deepcopy(source), "human_edited_offer": human_edits,
                "previous_approval_event_id": previous_approval["event_id"] if previous_approval else None,
                "action": "APPROVE", "approved_by": approved_by, "comment": comment},
                idempotency_key=idempotency_key, request=request,
                causation_id=proposal_decision_event_id, correlation_id=proposal["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def create_draft(
        self, *, decision_event_id: str, messages: list[dict], idempotency_key: str,
        material_contract_hash: str | None = None, style_transformations: list[str] | None = None,
    ) -> dict:
        decision = self._require(decision_event_id, "DECISION_READY")
        student_id = decision["student_id"]
        request = {"type": "DRAFTED", "decision_event_id": decision_event_id, "messages": messages,
                   "material_contract_hash": material_contract_hash,
                   "style_transformations": style_transformations or []}

        def build() -> dict:
            context = self._require(decision["causation_id"], "TURN_CONTEXT_BUILT", student_id)
            self._require_context_contact_permission_current(student_id, context)
            interpretation_id = decision["payload"].get("inbound_interpretation_event_id")
            if interpretation_id is not None:
                latest = self.latest_inbound_interpretation(context["payload"]["latest_student_message_event_id"])
                if latest is None or latest["event_id"] != interpretation_id:
                    raise ContractViolation("stale_inbound_interpretation")
            offer_state = decision["payload"]["offer"]["state"]
            if offer_state == "CUSTOM_OFFER_PROPOSAL":
                raise ContractViolation("unapproved_custom_offer_cannot_enter_customer_draft")
            if offer_state == "APPROVED_CUSTOM_OFFER":
                self._valid_custom_approval(
                    self._require(decision["payload"]["offer"].get("approval_event_id"),
                                  "CUSTOM_OFFER_APPROVED", student_id),
                    decision["payload"]["offer"], student_id)
            if decision["payload"].get("stop_required") or (
                decision["payload"]["current_objective"]["status"] == "STOPPED"
            ) or self.get_contact_permission(student_id) == "DO_NOT_CONTACT":
                raise ContractViolation("stopped_or_do_not_contact_cannot_create_customer_draft")
            inbound = self.list_events(student_id, event_type="INBOUND_RECEIVED")
            if inbound and inbound[-1]["revision"] > decision["revision"]:
                raise ContractViolation("new_student_message_invalidates_old_decision_draft")
            if not self._passed_gate(decision_event_id, "PRE_CONVERSATION"):
                raise ContractViolation("pre_conversation_gate_must_pass")
            if not messages or any(m.get("type") not in {"text", "sticker_suggestion"} for m in messages):
                raise ContractViolation("invalid_customer_messages")
            rendered = "\n".join(m.get("content", "") for m in messages if m["type"] == "text")
            if not rendered.strip():
                raise ContractViolation("draft_requires_text")
            prior_drafts = [e for e in self.list_events(student_id, event_type="DRAFTED")
                            if e["correlation_id"] == decision["correlation_id"]]
            return self._append(
                student_id=student_id, conversation_id=decision["conversation_id"],
                event_type="DRAFTED", schema_version="sales.customer-draft.v1",
                actor="CONVERSATION_AGENT", payload={"decision_event_id": decision_event_id,
                "draft_revision": len(prior_drafts) + 1, "messages": messages, "rendered_text": rendered,
                "material_contract_hash": material_contract_hash or _digest(decision["payload"]["content_contract"]),
                "style_transformations": style_transformations or []},
                idempotency_key=idempotency_key, request=request,
                causation_id=decision_event_id, correlation_id=decision["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def review_draft(
        self, *, draft_event_id: str, action: str, idempotency_key: str,
        feedback_types: list[str] | None = None, comment: str = "", reviewed_by: str = "salesperson",
    ) -> dict:
        draft = self._require(draft_event_id, "DRAFTED")
        student_id = draft["student_id"]
        feedback_types = feedback_types or []
        request = {"type": "REVIEW", "draft_event_id": draft_event_id, "action": action,
                   "feedback_types": feedback_types, "comment": comment, "reviewed_by": reviewed_by}

        def build() -> dict:
            if action not in {"APPROVE", "REQUEST_CHANGES", "REJECT"}:
                raise ContractViolation("invalid_review_action")
            if action == "APPROVE" and self._has_newer_inbound(draft):
                raise ContractViolation("draft_stale_after_new_inbound")
            reviews = [e for e in self.list_events(student_id, event_type="REVIEW")
                       if e["payload"]["draft_event_id"] == draft_event_id]
            if reviews:
                raise ContractViolation("draft_already_reviewed")
            if action == "APPROVE" and not self._passed_gate(draft_event_id, "POST_CONVERSATION"):
                raise ContractViolation("post_conversation_gate_must_pass_before_approval")
            decision_types = {"FACT_ERROR", "PERMISSION_ERROR", "POLICY_ERROR", "STRATEGY_ERROR"}
            style_types = {"TONE", "NATURALNESS", "LENGTH", "WORDING"}
            if any(value not in decision_types | style_types for value in feedback_types):
                raise ContractViolation("invalid_feedback_type")
            route = "BOTH" if set(feedback_types) & decision_types and set(feedback_types) & style_types else (
                "DECISION_AGENT" if set(feedback_types) & decision_types else (
                    "CONVERSATION_AGENT" if set(feedback_types) & style_types else "NONE"))
            return self._append(
                student_id=student_id, conversation_id=draft["conversation_id"],
                event_type="REVIEW", schema_version="sales.review-event.v1", actor="SALESPERSON",
                payload={"draft_event_id": draft_event_id, "action": action,
                         "feedback_types": feedback_types, "comment": comment, "route_to": route,
                         "reviewed_by": reviewed_by, "reviewed_at": _utc_now(),
                         "reflection": None},
                idempotency_key=idempotency_key, request=request,
                causation_id=draft_event_id, correlation_id=draft["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def record_review_reflection(
        self, *, review_event_id: str, failure_type: str, what_was_wrong: str,
        evidence: list[str], revision_plan: list[str], applies_to: str,
        idempotency_key: str,
    ) -> dict:
        review = self._require(review_event_id, "REVIEW")
        student_id = review["student_id"]
        request = {"type": "REVIEW_REFLECTION", "review_event_id": review_event_id,
                   "failure_type": failure_type, "what_was_wrong": what_was_wrong,
                   "evidence": evidence, "revision_plan": revision_plan,
                   "applies_to": applies_to}

        def build() -> dict:
            if failure_type not in {"FACT", "POLICY", "STRATEGY", "NO_PROGRESS",
                                    "TONE", "NATURALNESS", "OTHER"}:
                raise ContractViolation("invalid_reflection_failure_type")
            if applies_to not in {"CURRENT_DRAFT", "CURRENT_STUDENT", "ORGANIZATION_CANDIDATE"}:
                raise ContractViolation("invalid_reflection_scope")
            if not what_was_wrong.strip() or not evidence or not revision_plan:
                raise ContractViolation("reflection_requires_reason_evidence_and_plan")
            return self._append(
                student_id=student_id, conversation_id=review["conversation_id"],
                event_type="REVIEW_REFLECTION", schema_version="sales.review-reflection.v1",
                actor="DECISION_AGENT" if review["payload"]["route_to"] in {
                    "DECISION_AGENT", "BOTH"
                } else "CONVERSATION_AGENT",
                payload={"review_event_id": review_event_id, "failure_type": failure_type,
                         "what_was_wrong": what_was_wrong, "evidence": evidence,
                         "revision_plan": revision_plan, "applies_to": applies_to,
                         "learning_status": "UNVERIFIED"},
                idempotency_key=idempotency_key, request=request,
                causation_id=review_event_id, correlation_id=review["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def draft_status(self, draft_event_id: str) -> str:
        draft = self._require(draft_event_id, "DRAFTED")
        events = self.list_events(draft["student_id"])
        if any(e["event_type"] == "HUMAN_SENT" and e["payload"].get("approved_draft_event_id") == draft_event_id
               for e in events):
            return "HUMAN_SENT"
        reviews = [e for e in events if e["event_type"] == "REVIEW"
                   and e["payload"]["draft_event_id"] == draft_event_id]
        if not reviews:
            return "DRAFTED"
        return {"APPROVE": "APPROVED", "REQUEST_CHANGES": "REVIEW_CHANGES_REQUESTED",
                "REJECT": "REJECTED"}[reviews[-1]["payload"]["action"]]

    def _has_newer_inbound(self, draft: dict) -> bool:
        return any(
            event["conversation_id"] == draft["conversation_id"]
            and event["revision"] > draft["revision"]
            for event in self.list_events(draft["student_id"], event_type="INBOUND_RECEIVED")
        )

    def record_sent(
        self, *, draft_event_id: str | None = None, actual_sent_text: str, idempotency_key: str,
        sent_by: str = "salesperson", sent_at: str | None = None,
        channel_message_id: str | None = None, student_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict:
        draft = self._require(draft_event_id, "DRAFTED") if draft_event_id else None
        if draft:
            if student_id is not None and student_id != draft["student_id"]:
                raise ContractViolation("sent_student_mismatch")
            if conversation_id is not None and conversation_id != draft["conversation_id"]:
                raise ContractViolation("sent_conversation_mismatch")
            student_id = draft["student_id"]
            conversation_id = draft["conversation_id"]
        elif not student_id or not conversation_id:
            raise ContractViolation("manual_send_requires_student_and_conversation")
        assert student_id is not None and conversation_id is not None
        request = {"type": "HUMAN_SENT", "draft_event_id": draft_event_id,
                   "actual_sent_text": actual_sent_text, "sent_by": sent_by,
                   "sent_at": sent_at, "channel_message_id": channel_message_id,
                   "student_id": student_id, "conversation_id": conversation_id}

        def build() -> dict:
            if draft and self.draft_status(draft_event_id) != "APPROVED":
                raise ContractViolation("draft_must_be_approved_and_unsent")
            if draft and self._has_newer_inbound(draft):
                raise ContractViolation("draft_stale_after_new_inbound")
            if not actual_sent_text.strip():
                raise ContractViolation("actual_sent_text_required")
            approved_text = draft["payload"]["rendered_text"] if draft else None
            change_class = (sent_material_change_classification(approved_text, actual_sent_text)
                            if approved_text is not None else "NO_APPROVED_DRAFT")
            event = self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="HUMAN_SENT", schema_version="sales.sent-event.v1",
                actor="SALESPERSON", payload={"approved_draft_event_id": draft_event_id,
                "approved_messages": draft["payload"]["messages"] if draft else [],
                "actual_sent_messages": [{"type": "text", "content": actual_sent_text}],
                "actual_sent_text": actual_sent_text, "sent_at": sent_at or _utc_now(),
                "sent_by": sent_by, "channel_message_id": channel_message_id,
                "diff_from_approved": (
                    None if draft is None or approved_text == actual_sent_text else "CHANGED_BY_HUMAN"
                ),
                "material_change_detected": change_class == "POSSIBLE_MATERIAL",
                "material_change_classification": change_class},
                idempotency_key=idempotency_key, request=request,
                causation_id=draft_event_id,
                correlation_id=draft["correlation_id"] if draft else None,
            )
            self._cancel_reminders(student_id, {"SEND_DUE"}, event)
            return event

        return self._write(student_id, idempotency_key, request, build)

    def record_follow_up_consent(
        self, *, inbound_event_id: str, evidence_span: str,
        agreed_due_at: str, idempotency_key: str,
        recorded_by: str = "salesperson",
    ) -> dict:
        inbound = self._require(inbound_event_id, "INBOUND_RECEIVED")
        student_id = inbound["student_id"]
        request = {"type": "FOLLOW_UP_CONSENT", "inbound_event_id": inbound_event_id,
                   "evidence_span": evidence_span, "agreed_due_at": agreed_due_at,
                   "recorded_by": recorded_by}

        def build() -> dict:
            if not evidence_span or evidence_span not in inbound["payload"]["raw_text"]:
                raise ContractViolation("follow_up_consent_requires_student_evidence")
            return self._append(
                student_id=student_id, conversation_id=inbound["conversation_id"],
                event_type="FOLLOW_UP_CONSENT", schema_version="sales.follow-up-consent.v1",
                actor="SALESPERSON", payload={"inbound_event_id": inbound_event_id,
                "evidence_span": evidence_span, "agreed_due_at": agreed_due_at,
                "recorded_by": recorded_by},
                idempotency_key=idempotency_key, request=request,
                causation_id=inbound_event_id, correlation_id=inbound["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def record_internal_notification(
        self, *, student_id: str, conversation_id: str,
        notification_type: str, details: dict, source_event_ids: list[str],
        idempotency_key: str,
    ) -> dict:
        """Service-only internal notice; never a student-visible message."""
        request = {"type": "INTERNAL_NOTIFICATION", "conversation_id": conversation_id,
                   "notification_type": notification_type, "details": details,
                   "source_event_ids": source_event_ids}

        def build() -> dict:
            if notification_type not in {"PROGRAM_DB_UPDATE_REQUIRED", "TOOL_UNAVAILABLE",
                                         "APPROVAL_REQUIRED", "FACT_CONFLICT"}:
                raise ContractViolation("invalid_internal_notification_type")
            if not source_event_ids or any(
                (event := self.get_event(event_id)) is None or event["student_id"] != student_id
                for event_id in source_event_ids
            ):
                raise ContractViolation("notification_source_missing_or_other_student")
            return self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="INTERNAL_NOTIFICATION", schema_version="sales.internal-notification.v1",
                actor="SYSTEM", payload={"notification_type": notification_type,
                                         "details": details, "source_event_ids": source_event_ids},
                idempotency_key=idempotency_key, request=request,
                causation_id=source_event_ids[-1],
            )

        return self._write(student_id, idempotency_key, request, build)

    def record_tool_trace(
        self, *, student_id: str, conversation_id: str, tool_name: str,
        call_id: str, input_summary: dict, status: str,
        returned_evidence_ids: list[str], adopted_evidence_ids: list[str],
        source_version: str | None, limitations: list[str],
        idempotency_key: str, causation_id: str | None = None,
        response_status: str | None = None, error_code: str | None = None,
    ) -> dict:
        """Service-only trace of a Tool call, without raw sensitive Tool output."""
        request = {"type": "TOOL_TRACE", "conversation_id": conversation_id,
                   "tool_name": tool_name, "call_id": call_id, "input_summary": input_summary,
                   "status": status, "returned_evidence_ids": returned_evidence_ids,
                   "adopted_evidence_ids": adopted_evidence_ids,
                   "source_version": source_version, "limitations": limitations,
                   "response_status": response_status, "error_code": error_code,
                   "causation_id": causation_id}

        def build() -> dict:
            if not tool_name or not call_id or status not in {
                "OK", "NO_RESULTS", "UNAVAILABLE", "INVALID", "ERROR", "STALE"
            }:
                raise ContractViolation("invalid_tool_trace")
            if response_status is not None and response_status not in {
                "OK", "NO_RESULTS", "UNAVAILABLE", "INVALID_REQUEST", "POLICY_BLOCKED", "ERROR", "STALE"
            }:
                raise ContractViolation("invalid_tool_response_status")
            if error_code is not None and (
                not isinstance(error_code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code)
            ):
                raise ContractViolation("invalid_tool_error_code")
            if len(set(returned_evidence_ids)) != len(returned_evidence_ids) or not set(
                adopted_evidence_ids
            ).issubset(set(returned_evidence_ids)):
                raise ContractViolation("adopted_evidence_must_be_returned")
            if causation_id:
                cause = self.get_event(causation_id)
                if cause is None or cause["student_id"] != student_id:
                    raise ContractViolation("tool_trace_cause_missing_or_other_student")
            return self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="TOOL_TRACE", schema_version="sales.tool-trace.v1",
                actor="SYSTEM", payload={"tool_name": tool_name, "call_id": call_id,
                "input_summary": input_summary, "status": status,
                "returned_evidence_ids": returned_evidence_ids,
                "adopted_evidence_ids": adopted_evidence_ids,
                "source_version": source_version, "limitations": limitations,
                "response_status": response_status, "error_code": error_code},
                idempotency_key=idempotency_key, request=request,
                causation_id=causation_id,
            )

        return self._write(student_id, idempotency_key, request, build)

    def create_reminder(
        self, *, student_id: str, conversation_id: str, reminder_type: str,
        based_on_event_id: str, idempotency_key: str, due_at: str,
        reason: str = "",
    ) -> dict:
        request = {"type": "REMINDER_CREATED", "conversation_id": conversation_id,
                   "reminder_type": reminder_type, "based_on_event_id": based_on_event_id,
                   "due_at": due_at, "reason": reason}

        def build() -> dict:
            based_on = self.get_event(based_on_event_id)
            if based_on is None or based_on["student_id"] != student_id:
                raise ContractViolation("reminder_basis_missing_or_other_student")
            if reminder_type not in {"REVIEW_DUE", "SEND_DUE", "NEW_INBOUND", "FOLLOW_UP_DUE"}:
                raise ContractViolation("invalid_reminder_type")
            permission = self.get_contact_permission(student_id)
            if permission == "DO_NOT_CONTACT" and reminder_type in {"SEND_DUE", "FOLLOW_UP_DUE"}:
                raise ContractViolation("do_not_contact_blocks_sales_reminder")
            if reminder_type == "REVIEW_DUE" and self.draft_status(based_on_event_id) != "DRAFTED":
                raise ContractViolation("review_reminder_requires_unreviewed_draft")
            if reminder_type == "SEND_DUE":
                if self.draft_status(based_on_event_id) != "APPROVED":
                    raise ContractViolation("send_reminder_requires_approved_unsent_draft")
                if any(e["event_type"] == "INBOUND_RECEIVED" and e["revision"] > based_on["revision"]
                       for e in self.list_events(student_id)):
                    raise ContractViolation("new_inbound_invalidates_old_draft_reminder")
                snapshot = self.get_student_snapshot(student_id)
                sales = snapshot["snapshot"].get("sales", {}) if snapshot else {}
                if sales.get("next_action_status") != "CONFIRMED" or sales.get("next_action_at") != due_at:
                    raise ContractViolation("send_reminder_requires_confirmed_time")
            if reminder_type == "FOLLOW_UP_DUE":
                consent = self._require(based_on_event_id, "FOLLOW_UP_CONSENT", student_id)
                snapshot = self.get_student_snapshot(student_id)
                sales = snapshot["snapshot"].get("sales", {}) if snapshot else {}
                if sales.get("next_action_status") != "CONFIRMED" or sales.get("next_action_at") != due_at:
                    raise ContractViolation("follow_up_requires_confirmed_time")
                if permission not in {"ALLOWED", "LIMITED"}:
                    raise ContractViolation("follow_up_requires_contact_permission")
                if consent["payload"]["agreed_due_at"] != due_at:
                    raise ContractViolation("follow_up_due_time_differs_from_consent")
                inbound = self.list_events(student_id, event_type="INBOUND_RECEIVED")
                if inbound and inbound[-1]["revision"] > consent["revision"]:
                    raise ContractViolation("new_inbound_invalidates_old_follow_up")
            return self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="REMINDER_CREATED", schema_version="sales.reminder.v1",
                actor="SYSTEM", payload={"reminder_type": reminder_type, "status": "SCHEDULED",
                "due_at": due_at, "based_on_event_ids": [based_on_event_id], "reason": reason,
                "cancelled_by_event_id": None}, idempotency_key=idempotency_key,
                request=request, causation_id=based_on_event_id,
            )

        return self._write(student_id, idempotency_key, request, build)

    def _cancel_reminders(self, student_id: str, kinds: set[str], cause: dict) -> None:
        for event in self.list_events(student_id, event_type="REMINDER_CREATED"):
            if event["payload"]["reminder_type"] not in kinds or self.reminder_status(event["event_id"]) != "SCHEDULED":
                continue
            self._append_side_effect(
                student_id=student_id, conversation_id=event["conversation_id"],
                event_type="REMINDER_CANCELLED", schema_version="sales.reminder.v1",
                payload={"reminder_event_id": event["event_id"], "cancelled_by_event_id": cause["event_id"]},
                causation_id=cause["event_id"], correlation_id=event["correlation_id"],
                idempotency_key=f"cancel:{event['event_id']}:{cause['event_id']}",
            )

    def reminder_status(self, reminder_event_id: str) -> str:
        reminder = self._require(reminder_event_id, "REMINDER_CREATED")
        cancellations = [e for e in self.list_events(reminder["student_id"], event_type="REMINDER_CANCELLED")
                         if e["payload"]["reminder_event_id"] == reminder_event_id]
        return "CANCELLED" if cancellations else "SCHEDULED"

    def record_progress_assessment(
        self, *, inbound_event_id: str, predicted_signal: str,
        supporting_spans: list[str], confidence: float, can_continue: bool,
        idempotency_key: str, reply_to_sent_event_id: str | None = None,
        human_label: str | None = None,
    ) -> dict:
        inbound = self._require(inbound_event_id, "INBOUND_RECEIVED")
        student_id = inbound["student_id"]
        request = {"type": "PROGRESS_ASSESSMENT", "inbound_event_id": inbound_event_id,
                   "predicted_signal": predicted_signal, "supporting_spans": supporting_spans,
                   "confidence": confidence, "can_continue": can_continue,
                   "reply_to_sent_event_id": reply_to_sent_event_id, "human_label": human_label}

        def build() -> dict:
            signals = {"COMMITMENT_SIGNAL", "OBJECTION_CLARIFIED", "ENGAGED_WITH_NEW_INFORMATION",
                       "NEUTRAL", "DEFERRED", "EXIT", "DO_NOT_CONTACT", "UNCLEAR"}
            if predicted_signal not in signals or (human_label is not None and human_label not in signals):
                raise ContractViolation("invalid_progress_signal")
            if not 0 <= confidence <= 1:
                raise ContractViolation("invalid_progress_confidence")
            if predicted_signal not in {"UNCLEAR"} and not supporting_spans:
                raise ContractViolation("progress_requires_student_evidence")
            if any(span not in inbound["payload"]["raw_text"] for span in supporting_spans):
                raise ContractViolation("progress_evidence_not_in_student_message")
            if reply_to_sent_event_id:
                sent = self._require(reply_to_sent_event_id, "HUMAN_SENT", student_id)
                if sent["revision"] >= inbound["revision"]:
                    raise ContractViolation("reply_target_must_precede_inbound")
                inbound_time = _offer_time(inbound["payload"].get("source_occurred_at"))
                sent_time = _offer_time(sent["payload"].get("sent_at"))
                if inbound_time is not None and sent_time is not None and inbound_time <= sent_time:
                    raise ContractViolation("reply_target_sent_after_inbound")
            if predicted_signal in {"EXIT", "DO_NOT_CONTACT"} and can_continue:
                raise ContractViolation("exit_cannot_be_marked_continue")
            return self._append(
                student_id=student_id, conversation_id=inbound["conversation_id"],
                event_type="PROGRESS_ASSESSMENT", schema_version="sales.progress-assessment.v1",
                actor="DECISION_AGENT", payload={"inbound_event_id": inbound_event_id,
                "reply_to_sent_event_id": reply_to_sent_event_id,
                "predicted_signal": predicted_signal, "supporting_spans": supporting_spans,
                "confidence": confidence, "can_continue": can_continue, "human_label": human_label},
                idempotency_key=idempotency_key, request=request,
                causation_id=inbound_event_id, correlation_id=inbound["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def label_progress_assessment(
        self, *, assessment_event_id: str, human_label: str, reason: str,
        labeled_by: str, idempotency_key: str,
    ) -> dict:
        assessment = self._require(assessment_event_id, "PROGRESS_ASSESSMENT")
        student_id = assessment["student_id"]
        request = {"type": "PROGRESS_HUMAN_LABEL", "assessment_event_id": assessment_event_id,
                   "human_label": human_label, "reason": reason, "labeled_by": labeled_by}

        def build() -> dict:
            signals = {"COMMITMENT_SIGNAL", "OBJECTION_CLARIFIED", "ENGAGED_WITH_NEW_INFORMATION",
                       "NEUTRAL", "DEFERRED", "EXIT", "DO_NOT_CONTACT", "UNCLEAR"}
            if human_label not in signals:
                raise ContractViolation("invalid_progress_signal")
            if not isinstance(reason, str) or not reason.strip() or not isinstance(labeled_by, str) or not labeled_by.strip():
                raise ContractViolation("progress_label_requires_reason_and_actor")
            label = self._append(
                student_id=student_id, conversation_id=assessment["conversation_id"],
                event_type="PROGRESS_HUMAN_LABEL", schema_version="sales.progress-human-label.v1",
                actor="SALESPERSON", payload={"assessment_event_id": assessment_event_id,
                "inbound_event_id": assessment["payload"]["inbound_event_id"],
                "human_label": human_label, "reason": reason.strip(), "labeled_by": labeled_by.strip()},
                idempotency_key=idempotency_key, request=request, causation_id=assessment_event_id,
                correlation_id=assessment["correlation_id"],
            )
            if human_label == "DO_NOT_CONTACT" and self.get_contact_permission(student_id) != "DO_NOT_CONTACT":
                permission = self._append_side_effect(
                    student_id=student_id, conversation_id=assessment["conversation_id"],
                    event_type="CONTACT_PERMISSION_SET", schema_version="sales.contact-permission.v1",
                    payload={"permission": "DO_NOT_CONTACT", "source_event_id": label["event_id"]},
                    causation_id=label["event_id"], correlation_id=assessment["correlation_id"],
                    idempotency_key=f"progress-label-stop:{label['event_id']}",
                )
                self._cancel_reminders(student_id, {"SEND_DUE", "FOLLOW_UP_DUE"}, permission)
            return label

        return self._write(student_id, idempotency_key, request, build)

    def effective_progress_label(self, assessment_event_id: str) -> dict:
        assessment = self._require(assessment_event_id, "PROGRESS_ASSESSMENT")
        labels = [event for event in self.list_events(assessment["student_id"], event_type="PROGRESS_HUMAN_LABEL")
                  if event["payload"]["assessment_event_id"] == assessment_event_id]
        latest = labels[-1] if labels else None
        return {"assessment_event_id": assessment_event_id,
                "predicted_signal": assessment["payload"]["predicted_signal"],
                "human_label": latest["payload"]["human_label"] if latest else assessment["payload"]["human_label"],
                "label_event_id": latest["event_id"] if latest else None}

    def record_case_reflection(
        self, *, student_id: str, conversation_id: str, observed_outcome: str,
        possible_explanation: str, alternative_explanations: list[str],
        limitations: list[str], idempotency_key: str,
        based_on_event_ids: list[str],
    ) -> dict:
        request = {"type": "CASE_REFLECTION", "conversation_id": conversation_id,
                   "observed_outcome": observed_outcome,
                   "possible_explanation": possible_explanation,
                   "alternative_explanations": alternative_explanations,
                   "limitations": limitations, "based_on_event_ids": based_on_event_ids}

        def build() -> dict:
            if not based_on_event_ids or not limitations:
                raise ContractViolation("reflection_requires_sources_and_limitations")
            if any((event := self.get_event(event_id)) is None or event["student_id"] != student_id
                   for event_id in based_on_event_ids):
                raise ContractViolation("reflection_source_missing_or_other_student")
            return self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="CASE_REFLECTION", schema_version="sales.case-reflection.v1",
                actor="DECISION_AGENT", payload={"observed_outcome": observed_outcome,
                "possible_explanation": possible_explanation,
                "alternative_explanations": alternative_explanations,
                "limitations": limitations, "learning_status": "UNVERIFIED",
                "applies_to": "CURRENT_STUDENT", "based_on_event_ids": based_on_event_ids},
                idempotency_key=idempotency_key, request=request,
                causation_id=based_on_event_ids[-1],
            )

        return self._write(student_id, idempotency_key, request, build)

    def record_commercial_outcome(
        self, *, student_id: str, conversation_id: str, outcome_type: str,
        idempotency_key: str, recorded_by: str, source_type: str = "SALESPERSON_INPUT",
        amount: float | None = None, currency: str = "CNY",
        offer_id: str | None = None, offer_version: int | None = None,
        source_reference: str | None = None, reason_code: str | None = None,
        verification_status: str = "SELF_REPORTED", corrects_event_id: str | None = None,
    ) -> dict:
        request = {"type": "COMMERCIAL_OUTCOME", "conversation_id": conversation_id,
                   "outcome_type": outcome_type, "recorded_by": recorded_by,
                   "source_type": source_type, "amount": amount, "currency": currency,
                   "offer_id": offer_id, "offer_version": offer_version,
                   "source_reference": source_reference, "reason_code": reason_code,
                   "verification_status": verification_status,
                   "corrects_event_id": corrects_event_id}

        def build() -> dict:
            types = {"CONTRACT_SIGNED", "DEPOSIT_RECEIVED", "PAYMENT_RECEIVED", "FULLY_PAID",
                     "DEAL_LOST", "CANCELLED", "REFUND_ISSUED"}
            if outcome_type not in types:
                raise ContractViolation("invalid_commercial_outcome_type")
            if source_type not in {"SALESPERSON_INPUT", "PAYMENT_SYSTEM"}:
                raise ContractViolation("invalid_commercial_source_type")
            if verification_status not in {"SELF_REPORTED", "EVIDENCE_ATTACHED", "PAYMENT_SYSTEM_VERIFIED"}:
                raise ContractViolation("invalid_commercial_verification_status")
            if verification_status == "PAYMENT_SYSTEM_VERIFIED" and source_type != "PAYMENT_SYSTEM":
                raise ContractViolation("payment_verification_requires_payment_system")
            if outcome_type in {"DEPOSIT_RECEIVED", "PAYMENT_RECEIVED", "FULLY_PAID", "REFUND_ISSUED"}:
                if amount is None or not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0:
                    raise ContractViolation("payment_or_refund_requires_nonnegative_amount")
            if corrects_event_id:
                self._require(corrects_event_id, "COMMERCIAL_OUTCOME", student_id)
            return self._append(
                student_id=student_id, conversation_id=conversation_id,
                event_type="COMMERCIAL_OUTCOME", schema_version="sales.commercial-outcome.v1",
                actor="SALESPERSON" if source_type == "SALESPERSON_INPUT" else "SYSTEM",
                payload={"offer_id": offer_id, "offer_version": offer_version,
                "outcome_type": outcome_type, "amount": amount, "currency": currency,
                "source_type": source_type, "source_reference": source_reference,
                "recorded_by": recorded_by, "reason_code": reason_code,
                "verification_status": verification_status,
                "corrects_event_id": corrects_event_id},
                idempotency_key=idempotency_key, request=request,
                causation_id=corrects_event_id,
            )

        return self._write(student_id, idempotency_key, request, build)

    def commercial_ledger(self, student_id: str) -> dict:
        events = self.list_events(student_id, event_type="COMMERCIAL_OUTCOME")
        corrections = {event["payload"]["corrects_event_id"] for event in events
                       if event["payload"]["corrects_event_id"]}
        active = [event for event in events if event["event_id"] not in corrections]
        received = sum(event["payload"]["amount"] or 0 for event in active
                       if event["payload"]["outcome_type"] in {"DEPOSIT_RECEIVED", "PAYMENT_RECEIVED"})
        refunded = sum(event["payload"]["amount"] or 0 for event in active
                       if event["payload"]["outcome_type"] == "REFUND_ISSUED")
        return {"student_id": student_id, "signed": any(e["payload"]["outcome_type"] == "CONTRACT_SIGNED"
                                                   for e in active),
                "received_amount": received, "refunded_amount": refunded,
                "net_received_amount": received - refunded,
                "active_event_ids": [event["event_id"] for event in active]}

    def record_intervention_decision(
        self, *, trigger_event_id: str, policy_version: str, risk_level: str,
        risk_reasons: list[str], uncertainty: float, recommended_review_focus: list[str],
        route_to: str, idempotency_key: str, decision_source: str = "FIXED_RULE",
        hard_gate_reasons: list[str] | None = None,
        predicted_review_result: str = "UNKNOWN", mandatory_review: bool = True,
    ) -> dict:
        trigger = self.get_event(trigger_event_id)
        if trigger is None:
            raise ContractViolation("intervention_trigger_missing")
        student_id = trigger["student_id"]
        hard_gate_reasons = hard_gate_reasons or []
        request = {"type": "INTERVENTION_DECISION", "trigger_event_id": trigger_event_id,
                   "policy_version": policy_version, "risk_level": risk_level,
                   "risk_reasons": risk_reasons, "uncertainty": uncertainty,
                   "recommended_review_focus": recommended_review_focus,
                   "route_to": route_to, "decision_source": decision_source,
                   "hard_gate_reasons": hard_gate_reasons,
                   "predicted_review_result": predicted_review_result,
                   "mandatory_review": mandatory_review}

        def build() -> dict:
            if risk_level not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"} or not 0 <= uncertainty <= 1:
                raise ContractViolation("invalid_intervention_risk")
            if route_to not in {"SALESPERSON", "PRICING_OWNER", "DELIVERY_OWNER", "PRODUCT_OWNER"}:
                raise ContractViolation("invalid_intervention_route")
            if decision_source not in {"FIXED_RULE", "LEARNED_POLICY", "BOTH"}:
                raise ContractViolation("invalid_intervention_decision_source")
            if predicted_review_result not in {"APPROVE", "REQUEST_CHANGES", "REJECT", "UNKNOWN"}:
                raise ContractViolation("invalid_predicted_review_result")
            if not mandatory_review:
                raise ContractViolation("v2_requires_mandatory_review")
            return self._append(
                student_id=student_id, conversation_id=trigger["conversation_id"],
                event_type="INTERVENTION_DECISION", schema_version="sales.intervention-decision.v1",
                actor="SYSTEM", payload=request | {"mandatory_review": True},
                idempotency_key=idempotency_key, request=request,
                causation_id=trigger_event_id, correlation_id=trigger["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)

    def record_hitl_outcome(
        self, *, intervention_decision_event_id: str, review_event_id: str,
        subject_event_id: str, actual_review_action: str,
        human_work: dict, ai_work: dict, adjudication: dict,
        cost_observations: list[dict], learning_eligibility: str,
        idempotency_key: str,
    ) -> dict:
        intervention = self._require(intervention_decision_event_id, "INTERVENTION_DECISION")
        student_id = intervention["student_id"]
        request = {"type": "HITL_OUTCOME", "intervention_decision_event_id": intervention_decision_event_id,
                   "review_event_id": review_event_id, "subject_event_id": subject_event_id,
                   "actual_review_action": actual_review_action,
                   "human_work": human_work, "ai_work": ai_work, "adjudication": adjudication,
                   "cost_observations": cost_observations,
                   "learning_eligibility": learning_eligibility}

        def build() -> dict:
            review = self._require(review_event_id, "REVIEW", student_id)
            self._require(subject_event_id, "DRAFTED", student_id)
            if (intervention["payload"]["trigger_event_id"] != subject_event_id or
                    intervention["conversation_id"] != review["conversation_id"]):
                raise ContractViolation("hitl_intervention_trigger_mismatch")
            if review["payload"]["draft_event_id"] != subject_event_id or (
                review["payload"]["action"] != actual_review_action
            ):
                raise ContractViolation("hitl_review_mismatch")
            if any(event["payload"]["review_event_id"] == review_event_id or
                   event["payload"]["intervention_decision_event_id"] == intervention_decision_event_id
                   for event in self.list_events(student_id, event_type="HITL_OUTCOME")):
                raise ContractViolation("duplicate_hitl_outcome")
            if actual_review_action not in {"APPROVE", "REQUEST_CHANGES", "REJECT"}:
                raise ContractViolation("invalid_hitl_review_action")
            if adjudication.get("status") not in {"PENDING", "OWNER_CONFIRMED", "PANEL_CONFIRMED", "NOT_ADJUDICABLE"}:
                raise ContractViolation("invalid_adjudication_status")
            if adjudication.get("conclusion") not in {"AI_CORRECT", "HUMAN_CORRECT", "BOTH_PARTLY_CORRECT",
                                                       "BOTH_INCORRECT", "PREFERENCE_ONLY", "UNKNOWN"}:
                raise ContractViolation("invalid_adjudication_conclusion")
            if adjudication["status"] == "PENDING" and adjudication["conclusion"] != "UNKNOWN":
                raise ContractViolation("pending_adjudication_cannot_assign_correctness")
            if learning_eligibility not in {"NOT_REVIEWED", "CURRENT_STUDENT_ONLY", "APPROVED_FOR_ORG_LEARNING",
                                            "EXCLUDED_HOLDOUT", "REJECTED"}:
                raise ContractViolation("invalid_learning_eligibility")
            if learning_eligibility == "APPROVED_FOR_ORG_LEARNING" and adjudication["status"] not in {
                "OWNER_CONFIRMED", "PANEL_CONFIRMED"
            }:
                raise ContractViolation("org_learning_requires_adjudication")
            if human_work.get("active_seconds") is not None and human_work["active_seconds"] < 0:
                raise ContractViolation("invalid_human_work_time")
            if any(item.get("cost_type") in {"AI_ERROR_REWORK", "HUMAN_ERROR_REWORK"}
                   and item.get("attribution") != "UNKNOWN"
                   for item in cost_observations) and adjudication["status"] == "PENDING":
                raise ContractViolation("error_cost_attribution_requires_adjudication")
            return self._append(
                student_id=student_id, conversation_id=intervention["conversation_id"],
                event_type="HITL_OUTCOME", schema_version="sales.hitl-outcome.v1",
                actor="SYSTEM", payload={"intervention_decision_event_id": intervention_decision_event_id,
                "review_event_id": review_event_id, "subject_event_id": subject_event_id,
                "actual_review_action": actual_review_action, "human_work": human_work,
                "ai_work": ai_work, "adjudication": adjudication,
                "cost_observations": cost_observations,
                "learning_eligibility": learning_eligibility},
                idempotency_key=idempotency_key, request=request,
                causation_id=review_event_id, correlation_id=review["correlation_id"],
            )

        return self._write(student_id, idempotency_key, request, build)
