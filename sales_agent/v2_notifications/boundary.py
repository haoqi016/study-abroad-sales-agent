"""Server-owned reminder authorization before a generic device notification.

This module has no Firebase dependency, network client, credentials, or bridge
integration. The injected store must read authoritative current state and make
reserve_once atomic and durable in a future deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Protocol


@dataclass(frozen=True)
class NotificationPayload:
    title: str
    body: str
    data: tuple[tuple[str, str], ...] = ()


# Never project student names, platform IDs, messages, or drafts to lockscreen.
GENERIC_PAYLOAD = NotificationPayload(
    title="Sales workspace", body="You have a reminder to review."
)


@dataclass(frozen=True)
class NotificationContext:
    """One fresh, server-owned join of employee, device, target, grant, reminder."""

    employee_id: str
    employee_active: bool
    device_id: str
    device_employee_id: str
    device_active: bool
    target_id: str  # FID or legacy registration token; private transport value.
    target_kind: str  # FID or TOKEN.
    target_active: bool
    student_id: str
    grant_employee_id: str
    grant_student_id: str
    grant_active: bool
    reminder_id: str
    reminder_student_id: str
    reminder_eligible: bool


class NotificationStore(Protocol):
    def load_context(self, reminder_id: str, device_id: str) -> NotificationContext | None: ...
    def reserve_once(self, intent_id: str) -> bool: ...
    def finish(self, intent_id: str, status: str) -> None: ...
    def revoke_target(self, device_id: str, target_id: str) -> None: ...


class NotificationSender(Protocol):
    def send(self, *, target_kind: str, target_id: str,
             payload: NotificationPayload) -> str: ...


class TargetUnregistered(Exception):
    """Injected sender reports an FCM invalid/unregistered target."""


_ID = re.compile(r"[A-Za-z0-9_-]{1,120}\Z")


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _authorized(context: NotificationContext | None, reminder_id: str,
                device_id: str) -> bool:
    return bool(
        isinstance(context, NotificationContext)
        and _nonempty(context.employee_id) and _nonempty(context.student_id)
        and _nonempty(context.target_id)
        and context.device_id == device_id
        and context.reminder_id == reminder_id
        and context.employee_active and context.device_active
        and context.device_employee_id == context.employee_id
        and context.target_active and context.target_kind in {"FID", "TOKEN"}
        and context.grant_active and context.grant_employee_id == context.employee_id
        and context.grant_student_id == context.student_id
        and context.reminder_student_id == context.student_id
        and context.reminder_eligible
    )


class NotificationDelivery:
    """One-attempt send intent. Uncertain transport outcomes never auto-retry."""

    def __init__(self, *, store: NotificationStore, sender: NotificationSender):
        self._store = store
        self._sender = sender

    def deliver(self, *, reminder_id: str, device_id: str) -> str:
        if not (isinstance(reminder_id, str) and _ID.fullmatch(reminder_id)
                and isinstance(device_id, str) and _ID.fullmatch(device_id)):
            return "BLOCKED"
        initial = self._store.load_context(reminder_id, device_id)
        if not _authorized(initial, reminder_id, device_id):
            return "BLOCKED"
        # The pair is the logical notification, so callers cannot bypass
        # deduplication by changing a supplied idempotency key.
        intent_id = hashlib.sha256(
            f"{len(reminder_id)}:{reminder_id}:{device_id}".encode()
        ).hexdigest()
        if not self._store.reserve_once(intent_id):
            return "ALREADY_RESERVED"
        try:
            current = self._store.load_context(reminder_id, device_id)
            if not _authorized(current, reminder_id, device_id) or current != initial:
                self._store.finish(intent_id, "SUPPRESSED")
                return "SUPPRESSED"
            # No caller-provided display text or student identifier reaches FCM.
            provider_id = self._sender.send(target_kind=current.target_kind,
                                            target_id=current.target_id,
                                            payload=GENERIC_PAYLOAD)
            if not _nonempty(provider_id):
                raise ValueError("provider outcome unavailable")
        except TargetUnregistered:
            self._store.revoke_target(device_id, initial.target_id)
            self._store.finish(intent_id, "INVALID_TARGET")
            return "INVALID_TARGET"
        except Exception:
            # A timeout may occur after FCM accepted a message; do not retry.
            self._store.finish(intent_id, "OUTCOME_UNKNOWN")
            return "OUTCOME_UNKNOWN"
        self._store.finish(intent_id, "SENT_TO_PROVIDER")
        return "SENT_TO_PROVIDER"
