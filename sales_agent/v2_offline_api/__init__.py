"""DEMO/OFFLINE Python counterpart of ``web/api.js``'s SalesWorkspaceApi.

There is deliberately no HTTP listener, authentication claim, channel adapter,
or model endpoint. Only scripted deterministic providers and an in-memory
V2RuntimeStore are accepted. Inputs must be synthetic; this marker is an
operator assertion, not a way to verify that pasted text contains no PII.
"""

from .adapter import OfflineWorkspaceApi, OfflineOnlyError

__all__ = ["OfflineWorkspaceApi", "OfflineOnlyError"]
