"""Firebase Admin ID-token adapter with a server-owned device/session binding.

The Admin SDK performs signature and revocation validation. This module never
parses an unverified JWT. An enrolled binding is looked up by a digest of the
exact verified bearer token; client actor/session/device claims are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
import re
from typing import Any, Callable, Mapping, Protocol

from .access import IdentityEvidence


_PROJECT_ID = re.compile(r"[a-z][a-z0-9-]*[a-z0-9]\Z")


class FirebaseVerificationError(ValueError):
    """SDK verification, claims or enrolled binding failed closed."""


class AdminIdTokenVerifier(Protocol):
    def __call__(
        self, token: str, *, check_revoked: bool, clock_skew_seconds: int,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class EnrolledTokenBinding:
    """Server-owned enrollment for one exact Firebase ID token.

    Enrollment and any device proof are outside this module. Refreshed ID
    tokens need a new binding before the backend can accept them.
    """

    token_sha256: str
    uid: str
    session_id: str
    device_id: str
    active: bool
    expires_at: datetime


def token_fingerprint(token: str) -> str:
    """Lookup key only; never use a digest as proof of device possession."""

    if not isinstance(token, str) or not token.strip():
        raise FirebaseVerificationError("firebase_id_token_required")
    return sha256(token.encode("utf-8")).hexdigest()


def _timestamp(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise FirebaseVerificationError("invalid_" + field)
    return value


class FirebaseIdTokenVerifier:
    """Supply IdentityEvidence to AccessPolicy after SDK and binding checks.

    `binding_lookup` must read server-owned current state on every request.
    The injected SDK function must be the official Admin `verify_id_token`
    against the configured app; the classmethod below binds that function.
    """

    def __init__(
        self, *, project_id: str,
        sdk_verify_id_token: AdminIdTokenVerifier,
        binding_lookup: Callable[[str], EnrolledTokenBinding | None],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            raise ValueError("firebase_project_id_required")
        if not callable(sdk_verify_id_token) or not callable(binding_lookup):
            raise ValueError("sdk_verifier_and_binding_lookup_required")
        self._project_id = project_id
        self._sdk_verify = sdk_verify_id_token
        self._binding_lookup = binding_lookup
        self._now = now

    @classmethod
    def for_admin_app(
        cls, *, project_id: str, app: Any,
        binding_lookup: Callable[[str], EnrolledTokenBinding | None],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> "FirebaseIdTokenVerifier":
        """Bind the official Firebase Admin SDK to an initialized app.

        The caller owns SDK initialization, credentials and cloud configuration.
        No credentials are loaded by this module.
        """

        if app is None:
            raise ValueError("initialized_firebase_app_required")
        from firebase_admin import auth  # type: ignore[import-not-found]

        def verify(token: str, *, check_revoked: bool, clock_skew_seconds: int) -> Mapping[str, Any]:
            return auth.verify_id_token(
                token, app=app, check_revoked=check_revoked,
                clock_skew_seconds=clock_skew_seconds,
            )

        return cls(
            project_id=project_id, sdk_verify_id_token=verify,
            binding_lookup=binding_lookup, now=now,
        )

    def verify(self, bearer_token: str) -> IdentityEvidence:
        digest = token_fingerprint(bearer_token)
        try:
            claims = self._sdk_verify(
                bearer_token, check_revoked=True, clock_skew_seconds=0,
            )
        except Exception as exc:
            # Includes invalid/expired/revoked/disabled tokens and SDK/network
            # failures. Do not expose token content or SDK error details.
            raise FirebaseVerificationError("firebase_token_rejected") from exc
        if not isinstance(claims, Mapping):
            raise FirebaseVerificationError("firebase_claims_required")
        uid = claims.get("uid")
        subject = claims.get("sub")
        issuer = f"https://securetoken.google.com/{self._project_id}"
        if (not isinstance(uid, str) or not uid or subject != uid
                or claims.get("iss") != issuer or claims.get("aud") != self._project_id):
            raise FirebaseVerificationError("firebase_claims_mismatch")
        issued = _timestamp(claims.get("iat"), "iat")
        expires = _timestamp(claims.get("exp"), "exp")
        authenticated = _timestamp(claims.get("auth_time"), "auth_time")
        current = self._now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise FirebaseVerificationError("timezone_required")
        now_seconds = current.timestamp()
        if authenticated > issued or issued > now_seconds or expires <= now_seconds or expires <= issued:
            raise FirebaseVerificationError("firebase_token_time_invalid")
        try:
            binding = self._binding_lookup(digest)
        except Exception as exc:
            raise FirebaseVerificationError("session_binding_unavailable") from exc
        if (not isinstance(binding, EnrolledTokenBinding) or not binding.active
                or not isinstance(binding.token_sha256, str)
                or not compare_digest(binding.token_sha256, digest)
                or binding.uid != uid or not binding.session_id or not binding.device_id):
            raise FirebaseVerificationError("session_binding_required")
        if (not isinstance(binding.expires_at, datetime)
                or binding.expires_at.tzinfo is None or binding.expires_at.utcoffset() is None
                or binding.expires_at <= current):
            raise FirebaseVerificationError("session_binding_expired")
        return IdentityEvidence(
            subject=uid, issuer=issuer, audience=self._project_id,
            session_id=binding.session_id, device_id=binding.device_id,
            issued_at=datetime.fromtimestamp(issued, tz=timezone.utc),
            expires_at=datetime.fromtimestamp(expires, tz=timezone.utc),
        )
