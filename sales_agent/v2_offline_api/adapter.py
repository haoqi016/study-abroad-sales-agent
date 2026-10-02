"""Narrow, testable SalesWorkspaceApi mapping for synthetic local exercises."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Callable
from uuid import uuid4

from sales_agent.v2_pipeline import DeterministicOfflineProvider, JSONProvider, ToolPorts, V2SalesPipeline
from sales_agent.v2_runtime import V2RuntimeStore
from .progress import assess_explicit_reply


class OfflineOnlyError(ValueError):
    """An operation is outside this synthetic-only boundary."""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OfflineOnlyError(f"{name}_required")
    return value.strip()


def _aware_datetime(value: object) -> datetime | None:
    """Parse only timezone-qualified ISO timestamps; naive times are ambiguous."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


class OfflineWorkspaceApi:
    """Method names mirror the JS interface; output is a local Python view.

    ``scripted_turns`` maps a local student ID to FIFO scripted provider
    responses. Each script may contain ``tool_plan``, ``decision``,
    ``conversation``, and ``review_reflection`` dictionaries. All gates are computed by V2SalesPipeline;
    no public method accepts a gate result or a ``passed`` flag.
    """

    _DB_MARKER = "sales-v2-synthetic-offline-workspace-v1"

    def __init__(self, *, local_provider_factory: Callable[[], JSONProvider] | None = None,
                 db_path: str | Path | None = None) -> None:
        path = self._prepare_db_path(db_path) if db_path is not None else ":memory:"
        self._store = V2RuntimeStore(path)
        self._students: dict[str, str] = {}
        self._scripts: dict[str, list[dict]] = {}
        self._local_provider_factory = local_provider_factory
        self.agent_mode = "LOCAL_OLLAMA" if local_provider_factory else "SCRIPTED"
        if db_path is not None:
            try:
                self._restore_students()
            except Exception:
                self._store.close()
                raise

    @classmethod
    def _prepare_db_path(cls, db_path: str | Path) -> str:
        if not str(db_path).strip() or str(db_path) == ":memory:":
            raise OfflineOnlyError("explicit_sqlite_file_required")
        path = Path(db_path).expanduser().absolute()
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise OfflineOnlyError("regular_sqlite_file_required")
        if not path.parent.is_dir():
            raise OfflineOnlyError("sqlite_parent_directory_required")
        existed = path.exists()
        try:
            if not existed:
                try:
                    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError as exc:
                    raise OfflineOnlyError("sqlite_file_appeared_during_startup") from exc
                else:
                    os.close(descriptor)
            # A marker identifies this adapter's database; never migrate or
            # initialize an unrelated runtime, CRM, Cases, or Program file.
            with sqlite3.connect(path.as_uri() + f"?mode={'ro' if existed else 'rw'}", uri=True) as db:
                if existed:
                    row = db.execute("SELECT value FROM offline_workspace_meta WHERE key='identity'").fetchone()
                    if row is None or row[0] != cls._DB_MARKER:
                        raise OfflineOnlyError("not_synthetic_offline_workspace_db")
                else:
                    db.execute("CREATE TABLE offline_workspace_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    db.execute("INSERT INTO offline_workspace_meta VALUES ('identity', ?)", (cls._DB_MARKER,))
        except sqlite3.Error as exc:
            raise OfflineOnlyError("not_synthetic_offline_workspace_db") from exc
        return str(path)

    def _restore_students(self) -> None:
        # Only event records are authoritative. The dictionary is a rebuilt
        # lookup index, never an independently persisted student registry.
        try:
            rows = self._store._db.execute("SELECT DISTINCT student_id FROM events ORDER BY student_id")
            for row in rows:
                student_id = row[0]
                events = self._store.list_events(student_id)
                if any(event["student_id"] != student_id or event["revision"] != index
                       for index, event in enumerate(events, start=1)):
                    raise OfflineOnlyError("invalid_synthetic_workspace_history")
                snapshots = [event for event in events if event["event_type"] == "STUDENT_SNAPSHOT"]
                if not snapshots or len({event["conversation_id"] for event in events}) != 1:
                    raise OfflineOnlyError("invalid_synthetic_workspace_history")
                for event in snapshots:
                    snapshot = event["payload"]["snapshot"]
                    self._assert_synthetic(snapshot)
                    name = _text(snapshot.get("display_name"), "display_name")
                    if "合成" not in name and "DEMO" not in name.upper():
                        raise OfflineOnlyError("invalid_synthetic_workspace_history")
                    if snapshot.get("student_id") != student_id:
                        raise OfflineOnlyError("invalid_synthetic_workspace_history")
                self._students[student_id] = snapshots[-1]["conversation_id"]
        except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
            raise OfflineOnlyError("invalid_synthetic_workspace_history") from exc

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "OfflineWorkspaceApi":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _conversation(self, student_id: str) -> str:
        try:
            return self._students[student_id]
        except KeyError as exc:
            raise OfflineOnlyError("unknown_demo_student") from exc

    @staticmethod
    def _assert_synthetic(data: dict) -> None:
        if not isinstance(data, dict) or data.get("demo_only") is not True:
            raise OfflineOnlyError("DEMO_OFFLINE_synthetic_assertion_required")
        # Contact identifiers have no legitimate role in this local exercise.
        forbidden = {"phone", "email", "wechat_id", "platform_handle", "real_name", "government_id"}
        def contains_forbidden(value: object) -> bool:
            if isinstance(value, dict):
                return any(key in forbidden or contains_forbidden(item) for key, item in value.items())
            if isinstance(value, list):
                return any(contains_forbidden(item) for item in value)
            return False
        if contains_forbidden(data):
            raise OfflineOnlyError("contact_or_identity_field_forbidden")

    def queue_script(self, student_id: str, script: dict) -> None:
        if self._local_provider_factory is not None:
            raise OfflineOnlyError("scripts_disabled_in_local_model_mode")
        self._conversation(student_id)
        self._assert_synthetic(script)
        if set(script) - {"demo_only", "tool_plan", "decision", "conversation", "review_reflection"}:
            raise OfflineOnlyError("invalid_offline_script")
        if any(not isinstance(value, dict) for key, value in script.items() if key != "demo_only"):
            raise OfflineOnlyError("invalid_offline_script")
        self._scripts.setdefault(student_id, []).append(deepcopy(script))

    def _pipeline(self, student_id: str) -> V2SalesPipeline:
        if self._local_provider_factory is not None:
            provider = self._local_provider_factory()
            return V2SalesPipeline(store=self._store, decision_provider=provider,
                                   conversation_provider=provider, tools=ToolPorts())
        queue = self._scripts.get(student_id, [])
        if not queue:
            raise OfflineOnlyError("scripted_offline_response_required")
        script = queue.pop(0)
        decision = DeterministicOfflineProvider({
            key: [script[key]] for key in ("tool_plan", "decision", "review_reflection") if key in script
        })
        conversation = DeterministicOfflineProvider({
            key: [script[key]] for key in ("conversation", "review_reflection") if key in script
        })
        return V2SalesPipeline(store=self._store, decision_provider=decision,
                               conversation_provider=conversation, tools=ToolPorts())

    def createStudent(self, record: dict) -> dict:
        self._assert_synthetic(record)
        if set(record) - {"demo_only", "display_name", "source", "education", "targets", "sales", "field_sources"}:
            raise OfflineOnlyError("unsupported_demo_student_field")
        nested = {"source": {"channel"},
                  "education": {"undergraduate_university_raw", "undergraduate_tier", "major_raw",
                                "score_raw", "score_band", "current_year"},
                  "targets": {"countries", "universities", "programs_or_majors"},
                  "sales": {"stage", "current_objection", "decision_maker", "contact_permission",
                            "next_action_at", "next_action_reason", "next_action_status"}}
        if any(key in record and (not isinstance(record[key], dict) or set(record[key]) - fields)
               for key, fields in nested.items()):
            raise OfflineOnlyError("unsupported_demo_student_field")
        name = _text(record.get("display_name"), "display_name")
        if "合成" not in name and "DEMO" not in name.upper():
            raise OfflineOnlyError("synthetic_display_name_required")
        student_id, conversation_id = uuid4().hex, uuid4().hex
        sales = deepcopy(record.get("sales") or {})
        if (record.get("source") or {}).get("channel", "OTHER") not in {"XIAOHONGSHU", "WECHAT", "REFERRAL", "OTHER"}:
            raise OfflineOnlyError("invalid_source_channel")
        if sales.get("contact_permission", "UNKNOWN") not in {"UNKNOWN", "ALLOWED", "LIMITED", "DO_NOT_CONTACT"}:
            raise OfflineOnlyError("invalid_contact_permission")
        if sales.get("next_action_status") not in {None, "SUGGESTED", "CONFIRMED", "COMPLETED", "SNOOZED", "CANCELLED"}:
            raise OfflineOnlyError("invalid_next_action_status")
        next_action_at = sales.get("next_action_at")
        if next_action_at is not None:
            try:
                if datetime.fromisoformat(next_action_at.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError
            except (AttributeError, TypeError, ValueError) as exc:
                raise OfflineOnlyError("invalid_next_action_at") from exc
        snapshot = {
            "student_id": student_id, "display_name": name,
            "source": {"channel": (record.get("source") or {}).get("channel", "OTHER")},
            "education": deepcopy(record.get("education") or {}),
            "targets": deepcopy(record.get("targets") or {}),
            "sales": {"stage": sales.get("stage", "NEW"),
                      "current_objection": sales.get("current_objection"),
                      "decision_maker": sales.get("decision_maker"),
                      "contact_permission": sales.get("contact_permission", "UNKNOWN"),
                      "next_action_at": next_action_at,
                      "next_action_reason": sales.get("next_action_reason"),
                      "next_action_status": sales.get("next_action_status")},
            "demo_only": True,
        }
        self._store.upsert_student_snapshot(student_id=student_id, conversation_id=conversation_id,
                                           snapshot=snapshot, field_sources=record.get("field_sources"),
                                           idempotency_key=f"create:{student_id}")
        self._students[student_id] = conversation_id
        return self.getWorkspace(student_id)

    def updateStudent(self, student_id: str, patch: dict) -> dict:
        conversation_id = self._conversation(student_id)
        self._assert_synthetic(patch)
        current = self._store.get_student_snapshot(student_id)
        if patch.get("expected_revision") != current["snapshot_revision"]:
            raise OfflineOnlyError("stale_student_snapshot_revision")
        allowed = {"demo_only", "expected_revision", "display_name", "source", "education",
                   "targets", "sales", "field_sources"}
        nested = {"source": {"channel"},
                  "education": {"undergraduate_university_raw", "undergraduate_tier", "major_raw",
                                "score_raw", "score_band", "current_year"},
                  "targets": {"countries", "universities", "programs_or_majors"},
                  "sales": {"stage", "current_objection", "decision_maker", "contact_permission",
                            "next_action_at", "next_action_reason", "next_action_status"}}
        if (set(patch) - allowed or any(
            key in patch and (not isinstance(patch[key], dict) or set(patch[key]) - fields)
            for key, fields in nested.items()
        )):
            raise OfflineOnlyError("unsupported_snapshot_patch")
        snapshot = current["snapshot"]
        if "display_name" in patch:
            name = _text(patch["display_name"], "display_name")
            if "合成" not in name and "DEMO" not in name.upper():
                raise OfflineOnlyError("synthetic_display_name_required")
            snapshot["display_name"] = name
        for key in nested:
            snapshot[key].update(deepcopy(patch.get(key) or {}))
        if snapshot["source"].get("channel") not in {"XIAOHONGSHU", "WECHAT", "REFERRAL", "OTHER"}:
            raise OfflineOnlyError("invalid_source_channel")
        if snapshot["sales"].get("contact_permission") not in {"UNKNOWN", "ALLOWED", "LIMITED", "DO_NOT_CONTACT"}:
            raise OfflineOnlyError("invalid_contact_permission")
        if snapshot["sales"].get("next_action_status") not in {None, "SUGGESTED", "CONFIRMED", "COMPLETED", "SNOOZED", "CANCELLED"}:
            raise OfflineOnlyError("invalid_next_action_status")
        next_action_at = snapshot["sales"].get("next_action_at")
        if next_action_at is not None:
            try:
                if datetime.fromisoformat(next_action_at.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError
            except (AttributeError, TypeError, ValueError) as exc:
                raise OfflineOnlyError("invalid_next_action_at") from exc
        self._store.upsert_student_snapshot(student_id=student_id, conversation_id=conversation_id,
                                           snapshot=snapshot, expected_revision=current["snapshot_revision"],
                                           field_sources=patch.get("field_sources"),
                                           idempotency_key=f"update:{uuid4().hex}")
        return self.getWorkspace(student_id)

    def listStudents(self) -> list[dict]:
        return [self._summary(student_id) for student_id in self._students]

    def _summary(self, student_id: str) -> dict:
        workspace = self.getWorkspace(student_id)
        return {key: workspace[key] for key in ("student_id", "display_name", "source", "education",
                                               "targets", "sales", "revision", "draft_status",
                                               "has_pending_offer", "latest_event_type", "demo_only")}

    def getWorkspace(self, student_id: str) -> dict:
        self._conversation(student_id)
        snapshot = self._store.get_student_snapshot(student_id)
        view = deepcopy(snapshot["snapshot"])
        # Snapshot fields are editable CRM input; contact permission is derived
        # from the event ledger so an explicit stop cannot appear as ALLOWED.
        view["sales"]["contact_permission"] = self._store.get_contact_permission(student_id)
        provenance = snapshot["field_provenance"]
        events = self._store.list_events(student_id)
        decision = self._store.latest_decision(student_id)
        drafts = [event for event in events if event["event_type"] == "DRAFTED"]
        latest = drafts[-1] if drafts else None
        offer = decision["payload"].get("offer") if decision else None
        offer_action = self._store._latest_custom_offer_action(decision["event_id"]) if (
            decision and offer and offer.get("state") == "CUSTOM_OFFER_PROPOSAL") else None
        approval = next((event for event in reversed(events) if event["event_type"] == "CUSTOM_OFFER_APPROVED"
                         and decision and (
                             event["payload"]["proposal_decision_event_id"] == decision["event_id"] or
                             event["event_id"] == offer.get("approval_event_id")
                         )), None) if offer else None
        offer_view = deepcopy(offer) if offer else None
        if offer_action and offer_view:
            offer_view["review_action"] = offer_action["payload"].get(
                "action", "APPROVE" if offer_action["event_type"] == "CUSTOM_OFFER_APPROVED" else None)
            offer_view["review_event_id"] = offer_action["event_id"]
        if approval and offer_view:
            offer_view.update(deepcopy(approval["payload"]["approved_offer"]))
            offer_view["state"] = "APPROVED_CUSTOM_OFFER"
            offer_view["approval_event_id"] = approval["event_id"]
        return {
            **view, "revision": snapshot["snapshot_revision"], "field_provenance": provenance,
            "events": events, "memory": self._store.get_student_memory(student_id)["items"],
            "decision": deepcopy(decision["payload"]) if decision else None,
            "offer": offer_view,
            "drafts": [{"draft_id": draft["event_id"], "revision": draft["payload"]["draft_revision"],
                        "messages": draft["payload"]["messages"], "text": draft["payload"]["rendered_text"],
                        "status": self._effective_draft_status(draft)} for draft in drafts],
            "approved_draft_id": next((draft["event_id"] for draft in reversed(drafts)
                                       if self._store.draft_status(draft["event_id"]) == "APPROVED"
                                       and not self._newer_inbound(draft)), None),
            "sent": [event for event in events if event["event_type"] == "HUMAN_SENT"],
            "commercial_ledger": self._store.commercial_ledger(student_id),
            "progress_assessments": [event for event in events if event["event_type"] == "PROGRESS_ASSESSMENT"],
            "progress_labels": [event for event in events if event["event_type"] == "PROGRESS_HUMAN_LABEL"],
            "effective_progress_labels": [self._store.effective_progress_label(event["event_id"])
                                          for event in events if event["event_type"] == "PROGRESS_ASSESSMENT"],
            "case_reflections": [event for event in events if event["event_type"] == "CASE_REFLECTION"],
            "draft_status": self._effective_draft_status(latest) if latest else "NONE",
            "has_pending_offer": bool(offer and offer["state"] == "CUSTOM_OFFER_PROPOSAL"
                                      and offer_action is None),
            "latest_event_type": next((event["event_type"] for event in reversed(events)
                                       if event["event_type"] not in {"PROGRESS_ASSESSMENT",
                                                                        "PROGRESS_HUMAN_LABEL",
                                                                        "CASE_REFLECTION",
                                                                        "CONTACT_PERMISSION_SET"}), None),
            "demo_only": True, "environment": "DEMO_OFFLINE",
        }

    def _newer_inbound(self, event: dict) -> bool:
        return any(item["revision"] > event["revision"] for item in
                   self._store.list_events(event["student_id"], event_type="INBOUND_RECEIVED"))

    def _effective_draft_status(self, draft: dict) -> str:
        status = self._store.draft_status(draft["event_id"])
        return "STALE" if status in {"DRAFTED", "APPROVED"} and self._newer_inbound(draft) else status

    def recordInbound(self, student_id: str, message: dict) -> dict:
        conversation_id = self._conversation(student_id)
        self._assert_synthetic(message)
        if set(message) - {"demo_only", "raw_text", "channel", "occurred_at", "idempotency_key"}:
            raise OfflineOnlyError("unsupported_demo_inbound_field")
        raw = _text(message.get("raw_text"), "raw_text")
        inbound = self._store.record_inbound(student_id=student_id, conversation_id=conversation_id,
                                             raw_text=raw, channel=message.get("channel", "OTHER"),
                                             source_occurred_at=message.get("occurred_at"),
                                             recorded_by="demo-operator",
                                             idempotency_key=message.get("idempotency_key") or uuid4().hex)
        signal, spans, confidence, can_continue = assess_explicit_reply(inbound["payload"]["raw_text"])
        if signal == "DO_NOT_CONTACT":
            self._store.set_contact_permission(
                student_id=student_id, conversation_id=conversation_id,
                permission="DO_NOT_CONTACT", source_event_id=inbound["event_id"],
                idempotency_key=f"student-do-not-contact:{inbound['event_id']}")
        # Preserve a standalone assessment when there is recorded send history,
        # but only bind it to a send when reliable source time proves ordering.
        sent = [event for event in self._store.list_events(student_id, event_type="HUMAN_SENT")
                if event["conversation_id"] == conversation_id and event["revision"] < inbound["revision"]]
        source_time = _aware_datetime(inbound["payload"].get("source_occurred_at"))
        prior_sends = ([(sent_time, event) for event in sent
                        if (sent_time := _aware_datetime(event["payload"].get("sent_at"))) is not None
                        and sent_time < source_time]
                       if source_time is not None else [])
        latest_prior_send = max(prior_sends, key=lambda item: item[0])[1] if prior_sends else None
        if sent:
            assessment = self._store.record_progress_assessment(
                inbound_event_id=inbound["event_id"],
                reply_to_sent_event_id=latest_prior_send["event_id"] if latest_prior_send else None,
                predicted_signal=signal, supporting_spans=spans, confidence=confidence,
                can_continue=can_continue, human_label=None,
                idempotency_key=f"progress:{inbound['event_id']}")
            if latest_prior_send and signal == "OBJECTION_CLARIFIED":
                self._store.record_case_reflection(
                    student_id=student_id, conversation_id=conversation_id,
                    observed_outcome="The student clarified the objection: " + spans[0],
                    possible_explanation="The current reply explicitly clarified the objection; reassess the active objective next turn.",
                    alternative_explanations=["One reply cannot establish that the prior message caused this clarification."],
                    limitations=["Based only on the student's current words; not verified by a person.", "This is not evidence of a signed contract, payment, or strategy effectiveness."],
                    based_on_event_ids=[latest_prior_send["event_id"], inbound["event_id"], assessment["event_id"]],
                    idempotency_key=f"case-reflection:{inbound['event_id']}")
        return self.getWorkspace(student_id)

    def labelProgressAssessment(self, student_id: str, label: dict) -> dict:
        self._conversation(student_id)
        self._assert_synthetic(label)
        if set(label) - {"demo_only", "assessment_event_id", "human_label", "reason", "idempotency_key"}:
            raise OfflineOnlyError("unsupported_demo_progress_label_field")
        assessment_id = _text(label.get("assessment_event_id"), "assessment_event_id")
        assessment = self._store.get_event(assessment_id)
        if assessment is None or assessment["student_id"] != student_id or assessment["event_type"] != "PROGRESS_ASSESSMENT":
            raise OfflineOnlyError("progress_assessment_not_found_for_student")
        self._store.label_progress_assessment(
            assessment_event_id=assessment_id,
            human_label=_text(label.get("human_label"), "human_label"),
            reason=_text(label.get("reason"), "reason"), labeled_by="demo-operator",
            idempotency_key=label.get("idempotency_key") or uuid4().hex)
        return self.getWorkspace(student_id)

    def discussInternal(self, student_id: str, note: dict) -> dict:
        conversation_id = self._conversation(student_id)
        self._assert_synthetic(note)
        if set(note) - {"demo_only", "raw_text", "idempotency_key"}:
            raise OfflineOnlyError("unsupported_demo_note_field")
        self._store.record_internal_note(student_id=student_id, conversation_id=conversation_id,
                                        text=_text(note.get("raw_text"), "internal_note"),
                                        recorded_by="demo-operator", idempotency_key=note.get("idempotency_key") or uuid4().hex)
        return self.getWorkspace(student_id)

    def requestDecision(self, student_id: str) -> dict:
        conversation_id = self._conversation(student_id)
        if self._store.get_contact_permission(student_id) == "DO_NOT_CONTACT":
            raise OfflineOnlyError("do_not_contact")
        inbound = self._store.list_events(student_id, event_type="INBOUND_RECEIVED")
        if not inbound:
            raise OfflineOnlyError("inbound_required")
        assessments = self._store.list_events(student_id, event_type="PROGRESS_ASSESSMENT")
        latest_assessments = [event for event in assessments
                              if event["payload"]["inbound_event_id"] == inbound[-1]["event_id"]]
        latest_label = (self._store.effective_progress_label(latest_assessments[-1]["event_id"])["human_label"]
                        if latest_assessments else None)
        latest_signal = latest_label or (latest_assessments[-1]["payload"]["predicted_signal"]
                                         if latest_assessments else None)
        if (any(event["payload"]["predicted_signal"] == "DO_NOT_CONTACT" for event in assessments)
                or latest_signal in {"EXIT", "DO_NOT_CONTACT"}):
            raise OfflineOnlyError("student_exit_or_do_not_contact_requires_human_review")
        current = self.getWorkspace(student_id)
        if current["has_pending_offer"]:
            raise OfflineOnlyError("custom_offer_approval_required")
        sent = self._store.list_events(student_id, event_type="HUMAN_SENT")
        if sent and inbound[-1]["revision"] < sent[-1]["revision"]:
            raise OfflineOnlyError("new_inbound_required_after_recorded_send")
        if current["draft_status"] in {"DRAFTED", "APPROVED"} and not self._newer_inbound(
            self._store.get_event(current["drafts"][-1]["draft_id"])):
            raise OfflineOnlyError("current_draft_requires_review_or_send")
        reviews = self._store.list_events(student_id, event_type="REVIEW")
        if reviews and reviews[-1]["payload"]["action"] == "REQUEST_CHANGES":
            source_draft = self._store.get_event(reviews[-1]["payload"]["draft_event_id"])
            if source_draft and not self._newer_inbound(source_draft):
                result = self._pipeline(student_id).revise_draft(
                    review_event_id=reviews[-1]["event_id"], idempotency_key=uuid4().hex)
                return {"pipeline": asdict(result), "workspace": self.getWorkspace(student_id)}
        result = self._pipeline(student_id).run_turn(
            student_id=student_id, conversation_id=conversation_id,
            inbound_event_id=inbound[-1]["event_id"], idempotency_key=uuid4().hex)
        return {"pipeline": asdict(result), "workspace": self.getWorkspace(student_id)}

    def reviewDraft(self, student_id: str, review: dict) -> dict:
        self._conversation(student_id)
        self._assert_synthetic(review)
        if set(review) - {"demo_only", "comment", "feedback_type"}:
            raise OfflineOnlyError("unsupported_demo_review_field")
        draft = self._latest_unreviewed(student_id)
        intervention = self._intervention_for_draft(student_id, draft["event_id"])
        comment = _text(review.get("comment"), "review_comment")
        feedback_type = review.get("feedback_type", "NATURALNESS")
        reviewed = self._store.review_draft(draft_event_id=draft["event_id"], action="REQUEST_CHANGES",
                                            feedback_types=[feedback_type], comment=comment,
                                            reviewed_by="demo-operator", idempotency_key=uuid4().hex)
        self._record_hitl_outcome(intervention, reviewed, draft)
        return self.getWorkspace(student_id)

    def _intervention_for_draft(self, student_id: str, draft_event_id: str) -> dict:
        interventions = self._store.list_events(student_id, event_type="INTERVENTION_DECISION")
        intervention = next((event for event in reversed(interventions)
                             if event["payload"]["trigger_event_id"] == draft_event_id), None)
        if intervention is None:
            raise OfflineOnlyError("draft_intervention_required")
        return intervention

    def _record_hitl_outcome(self, intervention: dict, review: dict, draft: dict) -> None:
        self._store.record_hitl_outcome(
            intervention_decision_event_id=intervention["event_id"],
            review_event_id=review["event_id"], subject_event_id=draft["event_id"],
            actual_review_action=review["payload"]["action"],
            human_work={"started_at": None, "completed_at": None,
                        "active_seconds": None, "revision_cycles": None},
            ai_work={"model_calls": None, "input_tokens": None, "output_tokens": None,
                     "latency_ms": None, "provider_cost_amount": None, "currency": None},
            adjudication={"status": "PENDING", "conclusion": "UNKNOWN", "error_types": [],
                          "decided_by": [], "evidence_event_ids": []},
            cost_observations=[], learning_eligibility="CURRENT_STUDENT_ONLY",
            idempotency_key=f"hitl-outcome:{review['event_id']}",
        )

    def _latest_unreviewed(self, student_id: str) -> dict:
        drafts = self._store.list_events(student_id, event_type="DRAFTED")
        if not drafts or self._store.draft_status(drafts[-1]["event_id"]) != "DRAFTED" or self._newer_inbound(drafts[-1]):
            raise OfflineOnlyError("current_unreviewed_draft_required")
        return drafts[-1]

    def approveDraft(self, student_id: str) -> dict:
        self._conversation(student_id)
        if self._store.get_contact_permission(student_id) != "ALLOWED":
            raise OfflineOnlyError("explicit_contact_permission_required")
        if self.getWorkspace(student_id)["has_pending_offer"]:
            raise OfflineOnlyError("custom_offer_approval_required")
        draft = self._latest_unreviewed(student_id)
        intervention = self._intervention_for_draft(student_id, draft["event_id"])
        reviewed = self._store.review_draft(draft_event_id=draft["event_id"], action="APPROVE",
                                            reviewed_by="demo-operator", idempotency_key=uuid4().hex)
        self._record_hitl_outcome(intervention, reviewed, draft)
        return self.getWorkspace(student_id)

    def approveCustomOffer(self, student_id: str, approval: dict) -> dict:
        self._conversation(student_id)
        self._assert_synthetic(approval)
        if set(approval) - {"demo_only", "offer_id", "offer_version", "price", "scope",
                            "exclusions", "payment_terms", "valid_until", "confirmed_roles",
                            "delivery_conditions", "comment"}:
            raise OfflineOnlyError("unsupported_demo_offer_field")
        decision = self._store.latest_decision(student_id)
        if not decision or decision["payload"].get("offer", {}).get("state") != "CUSTOM_OFFER_PROPOSAL":
            raise OfflineOnlyError("pending_custom_offer_required")
        if not self.getWorkspace(student_id)["has_pending_offer"]:
            last_action = self._store._latest_custom_offer_action(decision["event_id"])
            raise OfflineOnlyError("custom_offer_already_approved" if last_action and
                                   last_action["event_type"] == "CUSTOM_OFFER_APPROVED"
                                   else "custom_offer_already_reviewed")
        if self._store.get_contact_permission(student_id) != "ALLOWED":
            raise OfflineOnlyError("explicit_contact_permission_required")
        price = approval.get("price")
        if type(price) is not int or price < 0:
            raise OfflineOnlyError("approved_integer_price_required")
        if set(approval.get("confirmed_roles") or []) != {"PRODUCT", "DELIVERY", "PRICING"}:
            raise OfflineOnlyError("all_custom_offer_roles_must_be_confirmed")
        timestamp = datetime.now(timezone.utc).isoformat()
        self._store.approve_custom_offer(
            proposal_decision_event_id=decision["event_id"], offer_id=_text(approval.get("offer_id"), "offer_id"),
            offer_version=approval.get("offer_version", 1), approved_offer={
                "offer_state": "APPROVED_CUSTOM_OFFER",
                "offer_id": approval["offer_id"], "offer_version": approval.get("offer_version", 1),
                "student_id": student_id, "source_proposal_id": decision["event_id"],
                "approved_scope": deepcopy(approval.get("scope")),
                "approved_exclusions": deepcopy(approval.get("exclusions")),
                "approved_price": price,
                "approved_payment_terms": deepcopy(approval.get("payment_terms")),
                **({"approved_delivery_conditions": deepcopy(approval["delivery_conditions"])}
                   if "delivery_conditions" in approval else {}),
                "valid_from": timestamp, "valid_until": approval.get("valid_until"),
                "approved_by": [{"actor": "demo-operator", "role": role, "approved_at": timestamp}
                                for role in ("PRODUCT", "DELIVERY", "PRICING")]},
            approved_by="demo-operator", comment=approval.get("comment", ""),
            idempotency_key=uuid4().hex)
        return self.getWorkspace(student_id)

    def reviewCustomOffer(self, student_id: str, review: dict) -> dict:
        self._conversation(student_id)
        self._assert_synthetic(review)
        action = review.get("action")
        if action == "APPROVE":
            return self.approveCustomOffer(student_id, {key: value for key, value in review.items()
                                                       if key != "action"})
        if set(review) - {"demo_only", "action", "offer_id", "comment", "edited_offer"}:
            raise OfflineOnlyError("unsupported_demo_offer_review_field")
        if action not in {"REQUEST_CHANGES", "REJECT"}:
            raise OfflineOnlyError("invalid_custom_offer_review_action")
        decision = self._store.latest_decision(student_id)
        if not decision or decision["payload"].get("offer", {}).get("state") != "CUSTOM_OFFER_PROPOSAL":
            raise OfflineOnlyError("pending_custom_offer_required")
        if not self.getWorkspace(student_id)["has_pending_offer"]:
            raise OfflineOnlyError("custom_offer_already_reviewed")
        self._store.review_custom_offer(
            proposal_decision_event_id=decision["event_id"], action=action,
            offer_id=_text(review.get("offer_id"), "offer_id"),
            comment=_text(review.get("comment"), "comment"),
            edited_offer=deepcopy(review.get("edited_offer")),
            reviewed_by="demo-operator", idempotency_key=uuid4().hex)
        return self.getWorkspace(student_id)

    def recordActualSent(self, student_id: str, sent: dict) -> dict:
        conversation_id = self._conversation(student_id)
        self._assert_synthetic(sent)
        if set(sent) - {"demo_only", "confirmed_external_send", "actual_sent_text", "sent_at", "idempotency_key"}:
            raise OfflineOnlyError("unsupported_demo_sent_field")
        if sent.get("confirmed_external_send") is not True:
            raise OfflineOnlyError("external_send_confirmation_required")
        if self._store.get_contact_permission(student_id) != "ALLOWED":
            raise OfflineOnlyError("explicit_contact_permission_required")
        workspace = self.getWorkspace(student_id)
        draft_id = workspace["approved_draft_id"]
        if not draft_id:
            raise OfflineOnlyError("current_approved_draft_required")
        self._store.record_sent(draft_event_id=draft_id, student_id=student_id,
                               conversation_id=conversation_id,
                               actual_sent_text=_text(sent.get("actual_sent_text"), "actual_sent_text"),
                               sent_at=sent.get("sent_at"), sent_by="demo-operator",
                               idempotency_key=sent.get("idempotency_key") or uuid4().hex)
        return self.getWorkspace(student_id)

    def recordCommercialOutcome(self, student_id: str, outcome: dict) -> dict:
        conversation_id = self._conversation(student_id)
        self._assert_synthetic(outcome)
        allowed = {"demo_only", "outcome_type", "amount", "currency", "offer_id",
                   "offer_version", "reason_code", "corrects_event_id", "idempotency_key"}
        if set(outcome) - allowed:
            raise OfflineOnlyError("unsupported_demo_outcome_field")
        self._store.record_commercial_outcome(
            student_id=student_id, conversation_id=conversation_id,
            outcome_type=_text(outcome.get("outcome_type"), "outcome_type"),
            amount=outcome.get("amount"), currency=outcome.get("currency", "CNY"),
            offer_id=outcome.get("offer_id"), offer_version=outcome.get("offer_version"),
            reason_code=outcome.get("reason_code"), corrects_event_id=outcome.get("corrects_event_id"),
            source_type="SALESPERSON_INPUT", verification_status="SELF_REPORTED",
            recorded_by="demo-operator", idempotency_key=outcome.get("idempotency_key") or uuid4().hex)
        return self.getWorkspace(student_id)

    def listReminders(self) -> list[dict]:
        reminders = []
        now = datetime.now(timezone.utc)
        for student_id in self._students:
            for event in self._store.list_events(student_id, event_type="REMINDER_CREATED"):
                if self._store.reminder_status(event["event_id"]) != "SCHEDULED":
                    continue
                payload = event["payload"]
                try:
                    due = datetime.fromisoformat(payload["due_at"].replace("Z", "+00:00"))
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
                if due.tzinfo is None or due > now:
                    continue
                reminder_type = payload["reminder_type"]
                basis_id = payload["based_on_event_ids"][0]
                basis = self._store.get_event(basis_id)
                if basis is None or basis["student_id"] != student_id:
                    continue
                permission = self._store.get_contact_permission(student_id)
                if reminder_type in {"SEND_DUE", "FOLLOW_UP_DUE"}:
                    if permission not in {"ALLOWED", "LIMITED"}:
                        continue
                    snapshot = self._store.get_student_snapshot(student_id)
                    sales = snapshot["snapshot"].get("sales", {}) if snapshot else {}
                    if sales.get("next_action_status") != "CONFIRMED" or sales.get("next_action_at") != payload["due_at"]:
                        continue
                    if any(inbound["revision"] > basis["revision"]
                           for inbound in self._store.list_events(student_id, event_type="INBOUND_RECEIVED")):
                        continue
                    if reminder_type == "SEND_DUE" and self._store.draft_status(basis_id) != "APPROVED":
                        continue
                    if reminder_type == "FOLLOW_UP_DUE" and (
                        basis["event_type"] != "FOLLOW_UP_CONSENT" or
                        basis["payload"].get("agreed_due_at") != payload["due_at"]
                    ):
                        continue
                elif reminder_type == "REVIEW_DUE":
                    if self._store.draft_status(basis_id) != "DRAFTED" or any(
                        inbound["revision"] > basis["revision"]
                        for inbound in self._store.list_events(student_id, event_type="INBOUND_RECEIVED")
                    ):
                        continue
                elif reminder_type == "NEW_INBOUND":
                    inbound = self._store.list_events(student_id, event_type="INBOUND_RECEIVED")
                    if basis["event_type"] != "INBOUND_RECEIVED" or not inbound or inbound[-1]["event_id"] != basis_id:
                        continue
                reminders.append(event)
        return reminders
