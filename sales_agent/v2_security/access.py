"""Identity and student-scope checks for a prospective trusted backend.

Only a server-controlled verifier may produce IdentityEvidence. Claims, roles,
student IDs and device IDs supplied in a JSON request body are never authority.
The caller must run this gate on every request before touching the V2 store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Protocol


class AccessDenied(PermissionError):
    """A request lacks a valid identity, device, role or student grant."""


class Action(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    EXPORT = "EXPORT"
    APPROVE_PRODUCT = "APPROVE_PRODUCT"
    APPROVE_DELIVERY = "APPROVE_DELIVERY"
    APPROVE_PRICING = "APPROVE_PRICING"


_ROLE_FOR_ACTION = {
    Action.READ: "SALESPERSON",
    Action.WRITE: "SALESPERSON",
    Action.EXPORT: "DATA_STEWARD",
    Action.APPROVE_PRODUCT: "PRODUCT",
    Action.APPROVE_DELIVERY: "DELIVERY",
    Action.APPROVE_PRICING: "PRICING",
}


@dataclass(frozen=True)
class IdentityEvidence:
    """Output of an external, trusted token verifier; never parse client JSON into this."""

    subject: str
    issuer: str
    audience: str
    session_id: str
    device_id: str
    issued_at: datetime
    expires_at: datetime


class IdentityVerifier(Protocol):
    def verify(self, bearer_token: str) -> IdentityEvidence: ...


@dataclass(frozen=True)
class Employee:
    employee_id: str
    active: bool
    roles: frozenset[str]
    student_ids: frozenset[str]
    sessions_revoked_before: datetime | None = None


@dataclass(frozen=True)
class Device:
    device_id: str
    employee_id: str
    active: bool
    sessions_revoked_before: datetime | None = None


@dataclass(frozen=True)
class ScopedActor:
    employee_id: str
    device_id: str
    session_id: str
    student_id: str
    action: Action
    offer_id: str | None = None
    offer_version: str | None = None


class AccessPolicy:
    """Validate a verified token against current server-owned grants.

    Employee and device lookups must query authoritative state on each request;
    caching them across revocations would invalidate the revocation guarantee.
    """

    def __init__(
        self,
        *,
        verifier: IdentityVerifier,
        employee_lookup: Callable[[str], Employee | None],
        device_lookup: Callable[[str], Device | None],
        issuer: str,
        audience: str,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not issuer or not audience:
            raise ValueError("issuer_and_audience_required")
        self._verifier = verifier
        self._employee_lookup = employee_lookup
        self._device_lookup = device_lookup
        self._issuer = issuer
        self._audience = audience
        self._now = now

    def authorize(
        self, bearer_token: str, *, student_id: str, action: Action,
        offer_id: str | None = None,
        offer_version: str | None = None,
    ) -> ScopedActor:
        if not isinstance(bearer_token, str) or not bearer_token.strip():
            raise AccessDenied("identity_required")
        if not isinstance(student_id, str) or not student_id.strip():
            raise AccessDenied("student_scope_required")
        if not isinstance(action, Action):
            raise AccessDenied("known_action_required")
        if action in {Action.APPROVE_PRODUCT, Action.APPROVE_DELIVERY, Action.APPROVE_PRICING}:
            if (not isinstance(offer_id, str) or not offer_id.strip()
                    or not isinstance(offer_version, str) or not offer_version.strip()):
                raise AccessDenied("offer_identity_and_version_required")
        elif offer_id is not None or offer_version is not None:
            raise AccessDenied("unexpected_offer_identity")
        try:
            identity = self._verifier.verify(bearer_token)
        except Exception as exc:
            raise AccessDenied("invalid_identity") from exc
        if not isinstance(identity, IdentityEvidence):
            raise AccessDenied("invalid_identity")
        current = self._now()
        if any(value.tzinfo is None or value.utcoffset() is None
               for value in (identity.issued_at, identity.expires_at, current)):
            raise AccessDenied("timezone_required")
        if (not identity.subject or not identity.session_id or not identity.device_id
                or identity.issuer != self._issuer or identity.audience != self._audience
                or identity.issued_at > current or identity.expires_at <= current
                or identity.expires_at <= identity.issued_at):
            raise AccessDenied("invalid_identity_claims")
        employee = self._employee_lookup(identity.subject)
        device = self._device_lookup(identity.device_id)
        if (employee is None or not employee.active or employee.employee_id != identity.subject
                or device is None or not device.active or device.device_id != identity.device_id
                or device.employee_id != identity.subject):
            raise AccessDenied("employee_or_device_revoked")
        for revoked_before in (employee.sessions_revoked_before, device.sessions_revoked_before):
            if revoked_before is not None:
                if revoked_before.tzinfo is None or identity.issued_at <= revoked_before:
                    raise AccessDenied("session_revoked")
        if student_id not in employee.student_ids:
            raise AccessDenied("student_scope_denied")
        if _ROLE_FOR_ACTION[action] not in employee.roles:
            raise AccessDenied("role_denied")
        return ScopedActor(
            employee_id=employee.employee_id,
            device_id=device.device_id,
            session_id=identity.session_id,
            student_id=student_id,
            action=action,
            offer_id=offer_id,
            offer_version=offer_version,
        )
