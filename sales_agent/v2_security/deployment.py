"""Static configuration gate; passing it is not deployment acceptance."""

from __future__ import annotations

from urllib.parse import urlsplit


def check_deployment_config(config: dict) -> tuple[str, ...]:
    """Return stable failure codes for required production security settings.

    This checks a proposed manifest only. It cannot inspect cloud IAM, TLS,
    backups, FCM delivery or data deletion and must not enable real data use.
    """

    if not isinstance(config, dict):
        return ("config_object_required",)
    issues: list[str] = []
    url = config.get("public_base_url")
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
    except ValueError:
        parsed = None
    if (parsed is None or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        issues.append("public_https_origin_required")
    project = config.get("firebase_project_id")
    if not isinstance(project, str) or not project.strip():
        issues.append("firebase_project_required")
    elif config.get("firestore_project_id") != project or config.get("fcm_project_id") != project:
        issues.append("service_project_mismatch")
    if config.get("cloud_run_auth_required") is not True:
        issues.append("cloud_run_auth_required")
    if config.get("cloud_run_public_invoker") is not False:
        issues.append("public_invoker_forbidden")
    if config.get("service_account_least_privilege_reviewed") is not True:
        issues.append("service_account_review_required")
    if config.get("lockscreen_content") != "GENERIC_ONLY":
        issues.append("generic_lockscreen_required")
    if config.get("backup_restore_drill_passed") is not True:
        issues.append("backup_restore_evidence_required")
    if config.get("budget_alert_enabled") is not True:
        issues.append("budget_alert_required")
    if config.get("retention_export_delete_plan_approved") is not True:
        issues.append("data_lifecycle_approval_required")
    if config.get("audit_logs_redacted") is not True:
        issues.append("redacted_audit_required")
    return tuple(issues)
