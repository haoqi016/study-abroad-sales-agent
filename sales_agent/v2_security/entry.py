"""Guard a prospective service dispatcher before any private backend call.

The current loopback bridge does not use this seam. A deployed service must
wire its verified token adapter and server-owned directory into AccessPolicy,
then make this dispatcher the only path to the private store.
"""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from .access import AccessDenied, AccessPolicy, Action, ScopedActor


Backend = Callable[[ScopedActor, str, str, dict | None], Any]

_POST_ACTIONS = {
    "inbound", "internal", "review", "approve", "actual-sent",
    "commercial-outcome", "progress-label", "decision",
}
_CLIENT_AUTHORITY_FIELDS = {
    "actor", "employee_id", "session_id", "device_id", "roles", "confirmed_roles",
    "reviewed_by", "approved_by", "student_id",
}
_STUDENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def _contains_client_authority(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(_CLIENT_AUTHORITY_FIELDS.intersection(value)) or any(
            _contains_client_authority(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_client_authority(item) for item in value)
    return False


class GuardedWorkspaceDispatcher:
    """Authorize exact student routes, then call a backend with trusted actor.

    List/create and combined Offer approval remain closed until dedicated
    workflows exist. No request body can assert a role or operator identity.
    """

    def __init__(self, policy: AccessPolicy, backend: Backend) -> None:
        self._policy = policy
        self._backend = backend

    def dispatch(
        self, bearer_token: str, method: str, path: str, body: dict | None = None,
    ) -> Any:
        if not isinstance(path, str) or "?" in path or "#" in path or "//" in path:
            raise AccessDenied("exact_route_required")
        parts = path.split("/")
        if len(parts) not in (4, 5) or parts[:3] != ["", "api", "students"]:
            raise AccessDenied("route_not_authorized")
        student_id = parts[3]
        if not _STUDENT_ID.fullmatch(student_id):
            raise AccessDenied("student_scope_required")
        if body is not None:
            if not isinstance(body, dict):
                raise AccessDenied("object_body_required")
            if _contains_client_authority(body):
                raise AccessDenied("client_authority_forbidden")
        if len(parts) == 4 and method == "GET" and body is None:
            action = Action.READ
        elif len(parts) == 4 and method == "PUT" and body is not None:
            action = Action.WRITE
        elif (len(parts) == 5 and method == "POST" and body is not None
              and parts[4] in _POST_ACTIONS):
            action = Action.WRITE
        else:
            raise AccessDenied("route_not_authorized")
        actor = self._policy.authorize(bearer_token, student_id=student_id, action=action)
        return self._backend(actor, method, path, body)

    def approve_offer_role(
        self, bearer_token: str, *, student_id: str, offer_id: str,
        offer_version: str,
        role: Action, record_approval: Callable[[ScopedActor], Any],
    ) -> Any:
        """Record one server-verified role decision for an exact Offer version.

        The callback must enforce Offer state and persist the role decision.
        This method does not approve a combined Offer or send a draft.
        """

        if role not in {Action.APPROVE_PRODUCT, Action.APPROVE_DELIVERY, Action.APPROVE_PRICING}:
            raise AccessDenied("approval_role_required")
        actor = self._policy.authorize(
            bearer_token, student_id=student_id, action=role,
            offer_id=offer_id, offer_version=offer_version,
        )
        return record_approval(actor)
