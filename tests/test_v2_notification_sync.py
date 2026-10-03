"""Synthetic, transport-free notification authorization and privacy checks."""

from dataclasses import replace
import unittest

from sales_agent.v2_notifications import (
    GENERIC_PAYLOAD, NotificationContext, NotificationDelivery,
    TargetUnregistered,
)


class FakeStore:
    def __init__(self, context):
        self.context = context
        self.intents = {}
        self.loads = 0
        self.after_reserve = None
        self.revoked = []

    def load_context(self, reminder_id, device_id):
        self.loads += 1
        return self.context

    def reserve_once(self, intent_id):
        if intent_id in self.intents:
            return False
        self.intents[intent_id] = "RESERVED"
        if self.after_reserve:
            self.after_reserve()
        return True

    def finish(self, intent_id, status):
        self.intents[intent_id] = status

    def revoke_target(self, device_id, target_id):
        self.revoked.append((device_id, target_id))
        self.context = replace(self.context, target_active=False)


class FakeSender:
    def __init__(self, error=None, result="synthetic-provider-message-id"):
        self.calls = []
        self.error = error
        self.result = result

    def send(self, *, target_kind, target_id, payload):
        self.calls.append((target_kind, target_id, payload))
        if self.error:
            raise self.error
        return self.result


def context(**changes):
    return replace(NotificationContext(
        employee_id="employee-1", employee_active=True,
        device_id="device-1", device_employee_id="employee-1", device_active=True,
        target_id="synthetic-fid-1", target_kind="FID", target_active=True,
        student_id="student-1", grant_employee_id="employee-1",
        grant_student_id="student-1", grant_active=True,
        reminder_id="reminder-1", reminder_student_id="student-1",
        reminder_eligible=True,
    ), **changes)


def test_authorized_send_uses_only_generic_payload_and_one_intent():
    store = FakeStore(context())
    sender = FakeSender()
    delivery = NotificationDelivery(store=store, sender=sender)
    assert delivery.deliver(reminder_id="reminder-1", device_id="device-1") == "SENT_TO_PROVIDER"
    assert store.loads == 2
    assert sender.calls == [("FID", "synthetic-fid-1", GENERIC_PAYLOAD)]
    assert GENERIC_PAYLOAD.data == ()
    assert "student" not in repr(GENERIC_PAYLOAD).lower()
    assert "platform" not in repr(GENERIC_PAYLOAD).lower()
    assert list(store.intents.values()) == ["SENT_TO_PROVIDER"]
    assert delivery.deliver(reminder_id="reminder-1", device_id="device-1") == "ALREADY_RESERVED"
    assert len(sender.calls) == 1

    store = FakeStore(context())
    sender = FakeSender(result="")
    delivery = NotificationDelivery(store=store, sender=sender)
    assert delivery.deliver(reminder_id="reminder-1", device_id="device-1") == "OUTCOME_UNKNOWN"
    assert delivery.deliver(reminder_id="reminder-1", device_id="device-1") == "ALREADY_RESERVED"
    assert len(sender.calls) == 1


def test_missing_employee_device_binding_grant_or_reminder_never_sends():
    for change in (
        {"employee_active": False}, {"device_active": False},
        {"device_employee_id": "other"}, {"target_active": False},
        {"target_kind": "TOPIC"}, {"grant_active": False},
        {"grant_employee_id": "other"}, {"grant_student_id": "other"},
        {"reminder_student_id": "other"}, {"reminder_eligible": False},
        {"target_id": ""},
    ):
        store = FakeStore(context(**change))
        sender = FakeSender()
        assert NotificationDelivery(store=store, sender=sender).deliver(
            reminder_id="reminder-1", device_id="device-1") == "BLOCKED", change
        assert not sender.calls and not store.intents


def test_rechecks_grant_reminder_and_rotated_target_immediately_before_send():
    for change in ({"grant_active": False}, {"reminder_eligible": False},
                   {"device_active": False}, {"target_id": "rotated-fid"}):
        store = FakeStore(context())
        store.after_reserve = lambda change=change: setattr(
            store, "context", replace(store.context, **change))
        sender = FakeSender()
        assert NotificationDelivery(store=store, sender=sender).deliver(
            reminder_id="reminder-1", device_id="device-1") == "SUPPRESSED", change
        assert store.loads == 2
        assert list(store.intents.values()) == ["SUPPRESSED"]
        assert not sender.calls


def test_unregistered_target_revoked_and_unknown_outcome_not_retried():
    store = FakeStore(context())
    sender = FakeSender(TargetUnregistered())
    delivery = NotificationDelivery(store=store, sender=sender)
    assert delivery.deliver(reminder_id="reminder-1", device_id="device-1") == "INVALID_TARGET"
    assert store.revoked == [("device-1", "synthetic-fid-1")]
    assert list(store.intents.values()) == ["INVALID_TARGET"]
    assert delivery.deliver(reminder_id="reminder-1", device_id="device-1") == "BLOCKED"
    assert len(sender.calls) == 1

    store = FakeStore(context(target_kind="TOKEN", target_id="synthetic-token"))
    sender = FakeSender(TimeoutError())
    delivery = NotificationDelivery(store=store, sender=sender)
    assert delivery.deliver(reminder_id="reminder-1", device_id="device-1") == "OUTCOME_UNKNOWN"
    assert delivery.deliver(reminder_id="reminder-1", device_id="device-1") == "ALREADY_RESERVED"
    assert len(sender.calls) == 1


def test_invalid_ids_fail_before_store_or_sender():
    store = FakeStore(context())
    sender = FakeSender()
    delivery = NotificationDelivery(store=store, sender=sender)
    for reminder_id, device_id in (("", "device-1"), ("reminder-1", "../device"),
                                   ("raw student text", "device-1")):
        assert delivery.deliver(reminder_id=reminder_id, device_id=device_id) == "BLOCKED"
    assert store.loads == 0 and not sender.calls


class NotificationBoundaryTests(unittest.TestCase):
    """Run the same transport-free checks with the standard library runner."""

    def test_authorized_send(self):
        test_authorized_send_uses_only_generic_payload_and_one_intent()

    def test_denied_context(self):
        test_missing_employee_device_binding_grant_or_reminder_never_sends()

    def test_context_rechecked(self):
        test_rechecks_grant_reminder_and_rotated_target_immediately_before_send()

    def test_uncertain_and_unregistered_targets(self):
        test_unregistered_target_revoked_and_unknown_outcome_not_retried()

    def test_invalid_ids(self):
        test_invalid_ids_fail_before_store_or_sender()
