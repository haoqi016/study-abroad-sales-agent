"""Local, fail-closed security primitives for a future V2 service boundary.

These functions are not wired into the synthetic loopback bridge. In particular,
they do not verify Firebase tokens or authorize any current HTTP request.
"""

from .access import (
    AccessDenied,
    AccessPolicy,
    Action,
    Device,
    Employee,
    IdentityEvidence,
    ScopedActor,
)
from .deployment import check_deployment_config
from .entry import GuardedWorkspaceDispatcher
from .data import (
    Classification, DataBoundaryError, DataUsePolicy, Purpose,
    audit_reason_for, prepare_model_payload, redacted_audit_metadata,
)
from .firebase_verifier import (
    EnrolledTokenBinding, FirebaseIdTokenVerifier, FirebaseVerificationError,
    token_fingerprint,
)

__all__ = [
    "AccessDenied", "AccessPolicy", "Action", "Device", "Employee",
    "IdentityEvidence", "ScopedActor", "GuardedWorkspaceDispatcher",
    "check_deployment_config",
    "Classification", "DataBoundaryError", "DataUsePolicy", "Purpose",
    "audit_reason_for", "prepare_model_payload", "redacted_audit_metadata",
    "EnrolledTokenBinding", "FirebaseIdTokenVerifier", "FirebaseVerificationError",
    "token_fingerprint",
]
