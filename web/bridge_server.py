"""Same-origin, loopback-only HTTP exercise for the synthetic V2 workspace."""

from __future__ import annotations

import json
import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sales_agent.v2_offline_api import OfflineOnlyError, OfflineWorkspaceApi  # noqa: E402
from sales_agent.v2_pipeline import LocalOllamaJSONProvider, OpenAICompatibleProvider  # noqa: E402
from sales_agent.v2_tools.offline_public_cases import OfflinePublicCasesSource  # noqa: E402
from sales_agent.v2_tools.official_program_source import PinnedOfficialProgramSource  # noqa: E402
from sales_agent.v2_tools.program_snapshot import ApprovedProgramSnapshot  # noqa: E402
from sales_agent.v2_tools.delegated_program import load_delegated_program_sources  # noqa: E402

WEB = Path(__file__).resolve().parent
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
          "/api.js": "api.js", "/bridge_api.js": "bridge_api.js", "/walkthrough.js": "walkthrough.js",
          "/styles.css": "styles.css", "/overrides.css": "overrides.css",
          "/manifest.webmanifest": "manifest.webmanifest"}
MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".webmanifest": "application/manifest+json"}


def load_api_config(path: Path) -> dict:
    """Read a private, local-only provider config without printing credentials."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("api_config_must_be_a_regular_file")
    stat = path.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077 or stat.st_size > 8192:
        raise ValueError("api_config_must_be_owner_only_0600_and_small")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("api_config_invalid_json") from exc
    if not isinstance(config, dict) or set(config) != {"base_url", "model", "api_key"}:
        raise ValueError("api_config_fields_required")
    if any(not isinstance(config[field], str) or not config[field].strip()
           for field in ("base_url", "model", "api_key")):
        raise ValueError("api_config_values_required")
    if "YOUR_" in config["api_key"] or "EXAMPLE" in config["api_key"].upper():
        raise ValueError("api_config_placeholder_key")
    return config


def load_private_secret(path: Path) -> bytes:
    """Load an audit-ID key without exposing it in diagnostics or model context."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("private_secret_must_be_a_regular_file")
    stat = path.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077 or not 32 <= stat.st_size <= 4096:
        raise ValueError("private_secret_must_be_owner_only_0600_and_32_to_4096_bytes")
    return path.read_bytes()


def scripted_turn(inbound_id: str, inbound_raw: str, previous_objective_id: str | None = None,
                  text: str = "Which part of your application do you need the most help with?",
                  review: dict | None = None) -> dict:
    """A visible canned fixture, never a model or customer conversation."""
    observation_ids = [inbound_id, review["event_id"]] if review else [inbound_id]
    # A strategy rejection starts a new objective; reusing the previous ID
    # would incorrectly claim a continued attempt and fail the runtime gate.
    objective_id = "objective-demo-" + (review["event_id"] if review else inbound_id)
    decision = {
        # The canned fixture preserves the synthetic student's exact words;
        # interpretation is a separate event, never a rewrite of the inbound.
        "normalized_meaning": inbound_raw,
        "normalized_meaning_spans": [inbound_raw],
        "hypotheses": [{"code": "VALUE_UNCLEAR", "confidence": 0.5,
                        "supporting_spans": [inbound_raw], "contradicting_evidence": []}],
        "unknowns": ["service needs"],
        "selected_strategy": {"strategy_code": "CLARIFY_NEED", "reason": "The synthetic script only clarifies needs",
                              "alternatives_rejected": []},
        "previous_objective_assessment": {"objective_id": previous_objective_id,
                                          "result": "UNKNOWN" if previous_objective_id else None,
                                          "observation_event_ids": observation_ids, "reason": None},
        "current_objective": {"objective_id": objective_id,
                              "previous_objective_id": previous_objective_id, "goal": "Clarify service needs",
                              "why_now": "The synthetic script received human revision feedback" if review else "The synthetic script received a new message",
                              "trigger_event_ids": observation_ids,
                              "status": "ACTIVE", "success_signals": ["Student identifies a service area"],
                              "failure_signals": ["Student explicitly exits"], "attempt_count": 1, "max_attempts": 2},
        "action_plan": {"selected_action": "Ask which service area matters", "expected_observation": "A specific need",
                        "fallback_if_blocked": "Human review"},
        "offer": {"state": "NONE", "offer_id": None, "offer_version": None, "proposal": None},
        "content_contract": {"must_include": [], "may_include": [], "must_not_include": [],
                             "semantic_draft": text, "desired_next_step": "Explain the need",
                             "communication_emphasis": "BALANCED"},
        "evidence_ids": [], "program_claims": [], "stop_required": False,
        "handoff_reason": None, "human_approval_required": True,
    }
    script = {"demo_only": True, "tool_plan": {"requests": []}, "decision": decision,
              "conversation": {"messages": [{"type": "text", "content": text}],
                               "style_transformations": []}}
    if review:
        feedback = (review["payload"].get("feedback_types") or ["OTHER"])[0]
        failure = {"FACT_ERROR": "FACT", "POLICY_ERROR": "POLICY",
                   "STRATEGY_ERROR": "STRATEGY"}.get(feedback, "NATURALNESS")
        comment = review["payload"]["comment"]
        script["review_reflection"] = {
            "failure_type": failure, "what_was_wrong": "Synthetic human revision feedback needs to be addressed",
            "evidence": [comment[:160]], "revision_plan": ["Recheck the current draft against human feedback"],
            "applies_to": "CURRENT_DRAFT",
        }
    return script


def dispatch(api: OfflineWorkspaceApi, method: str, path: str, body: dict | None = None):
    parts = path.strip("/").split("/")
    if method == "GET" and path == "/api/health":
        return {"environment": "DEMO_OFFLINE", "bridge": "localhost", "synthetic_only": True,
                "agent_mode": api.agent_mode}
    if parts[:2] != ["api", "students"]:
        raise KeyError("unknown_route")
    if len(parts) == 2:
        if method == "GET": return api.listStudents()
        if method == "POST": return api.createStudent(body)
    if len(parts) < 3 or not parts[2]:
        raise KeyError("unknown_route")
    sid = parts[2]
    if len(parts) == 3:
        if method == "GET": return api.getWorkspace(sid)
        if method == "PUT": return api.updateStudent(sid, body)
    if len(parts) != 4 or method != "POST":
        raise KeyError("unknown_route")
    action = parts[3]
    if not isinstance(body, dict) or body.get("demo_only") is not True:
        raise OfflineOnlyError("DEMO_OFFLINE_synthetic_assertion_required")
    if action == "inbound": return api.recordInbound(sid, body)
    if action == "internal": return api.discussInternal(sid, body)
    if action == "review":
        if set(body) - {"demo_only", "comment", "feedback_type"}:
            raise OfflineOnlyError("unsupported_demo_review_field")
        return api.reviewDraft(sid, body)
    if action == "approve":
        if body != {"demo_only": True}:
            raise OfflineOnlyError("unsupported_demo_approval_field")
        return api.approveDraft(sid)
    if action == "offer": return api.approveCustomOffer(sid, body)
    if action == "offer-review": return api.reviewCustomOffer(sid, body)
    if action == "actual-sent": return api.recordActualSent(sid, body)
    if action == "commercial-outcome": return api.recordCommercialOutcome(sid, body)
    if action == "progress-label": return api.labelProgressAssessment(sid, body)
    if action == "decision":
        if body != {"demo_only": True}:
            raise OfflineOnlyError("DEMO_OFFLINE_synthetic_assertion_required")
        if api.agent_mode == "SCRIPTED":
            workspace = api.getWorkspace(sid)
            inbound = next((e for e in reversed(workspace["events"])
                            if e["event_type"] == "INBOUND_RECEIVED"), None)
            if not inbound:
                raise OfflineOnlyError("inbound_required")
            # Existing adapter gates run before consuming the scripted provider response.
            previous_id = (workspace["decision"] or {}).get("current_objective", {}).get("objective_id")
            reviews = [event for event in workspace["events"] if event["event_type"] == "REVIEW"]
            review = reviews[-1] if reviews and reviews[-1]["payload"]["action"] == "REQUEST_CHANGES" else None
            if review and any(event["event_type"] == "INBOUND_RECEIVED" and
                              event["revision"] > review["revision"] for event in workspace["events"]):
                review = None
            revised_text = "To help me understand, which part of your application would you most like us to help with?" if review else "Which part of your application do you need the most help with?"
            api.queue_script(sid, scripted_turn(inbound["event_id"], inbound["payload"]["raw_text"],
                                                previous_id, revised_text, review))
        try:
            return api.requestDecision(sid)
        except Exception:
            # A gate may reject before the adapter consumes this fixture.
            if api.agent_mode == "SCRIPTED":
                api._scripts[sid].clear()
            raise
    raise KeyError("unknown_route")


def make_handler(api: OfflineWorkspaceApi):
    class Handler(BaseHTTPRequestHandler):
        def _trusted(self) -> bool:
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            return host == f"127.0.0.1:{self.server.server_port}" and origin in (None, expected)

        def _json(self, status: int, value: object) -> None:
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _handle(self) -> None:
            if not self._trusted():
                return self._json(403, {"error": "loopback_origin_required"})
            path = urlsplit(self.path).path
            if self.command == "GET" and path in STATIC:
                file = WEB / STATIC[path]
                raw = file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", MIME[file.suffix] + "; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(raw)
            if not path.startswith("/api/"):
                return self._json(404, {"error": "unknown_route"})
            try:
                body = None
                if self.command in ("POST", "PUT"):
                    if self.headers.get("X-Sales-Demo-Bridge") != "1":
                        return self._json(403, {"error": "same_origin_bridge_header_required"})
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        return self._json(415, {"error": "json_required"})
                    size = int(self.headers.get("Content-Length", "0"))
                    if size < 2 or size > 16384:
                        return self._json(413, {"error": "invalid_body_size"})
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        return self._json(400, {"error": "object_required"})
                result = dispatch(api, self.command, path, body)
                return self._json(200, result)
            except (OfflineOnlyError, ValueError, TypeError) as exc:
                return self._json(400, {"error": str(exc)})
            except KeyError:
                return self._json(404, {"error": "unknown_route"})

        def do_GET(self): self._handle()
        def do_POST(self): self._handle()
        def do_PUT(self): self._handle()
        def log_message(self, _format, *_args): pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Loopback-only synthetic Sales V2 workspace")
    parser.add_argument("port", nargs="?", type=int, default=8765)
    parser.add_argument("--ollama-model", help="Explicitly use a locally installed Ollama model")
    parser.add_argument("--ollama-timeout", type=float, default=180.0)
    parser.add_argument("--api-config", type=Path,
                        help="Private 0600 JSON config for a compatible chat/completions API")
    parser.add_argument("--api-timeout", type=float, default=120.0)
    parser.add_argument("--offline-public-cases", action="store_true",
                        help="Opt in to coarse external references for synthetic homework")
    parser.add_argument("--offline-public-cases-db", type=Path,
                        help="Explicit local synthetic reference SQLite source")
    parser.add_argument("--offline-public-cases-sha256",
                        help="Pinned SHA-256 of the synthetic reference SQLite source")
    parser.add_argument("--remote-public-cases", action="store_true",
                        help="Explicitly allow the coarse, source-labelled reference result to reach the remote API")
    parser.add_argument("--offline-public-cases-key-file", type=Path,
                        help="Optional local 0600 file with >=32 random bytes for stable audit IDs")
    parser.add_argument("--delegated-program-batch", type=Path,
                        help="Official-only batch directory with Owner delegation and current field receipts")
    parser.add_argument("--program-snapshot", type=Path,
                        help="Separately approved Sales-safe Program JSON snapshot")
    parser.add_argument("--program-approval", type=Path,
                        help="Owner approval manifest bound to the Program snapshot digest")
    parser.add_argument("--program-provenance-key-file", type=Path,
                        help="Local 0600 audit-ID key for the approved Program snapshot")
    parser.add_argument("--official-catalog", type=Path,
                        help="Pinned official-page field catalog bound to the approved Program snapshot")
    parser.add_argument("--official-approval", type=Path,
                        help="Owner field approval manifest bound to the official catalog digest")
    parser.add_argument("--sqlite-file", type=Path,
                        help="Explicit local SQLite file for synthetic workspace persistence")
    args = parser.parse_args()
    if args.ollama_model and args.api_config:
        parser.error("--ollama-model and --api-config are mutually exclusive")
    if args.api_timeout <= 0:
        parser.error("--api-timeout must be positive")
    if args.offline_public_cases and not (args.ollama_model or args.api_config):
        parser.error("--offline-public-cases requires an explicitly selected model")
    if args.offline_public_cases and not (args.offline_public_cases_db and args.offline_public_cases_sha256):
        parser.error("--offline-public-cases requires an explicit SQLite source and SHA-256")
    if (args.offline_public_cases_db or args.offline_public_cases_sha256) and not args.offline_public_cases:
        parser.error("case source flags require --offline-public-cases")
    if args.remote_public_cases and not (args.offline_public_cases and args.api_config):
        parser.error("--remote-public-cases requires --offline-public-cases and --api-config")
    if args.offline_public_cases and args.api_config and not args.remote_public_cases:
        parser.error("remote public references require explicit --remote-public-cases")
    if args.offline_public_cases_key_file and not args.offline_public_cases:
        parser.error("--offline-public-cases-key-file requires --offline-public-cases")
    program_flags = (args.program_snapshot, args.program_approval, args.program_provenance_key_file)
    if args.delegated_program_batch and (args.program_snapshot or args.program_approval or args.official_catalog or args.official_approval):
        parser.error("Delegated official batch cannot be combined with legacy Program approval flags")
    if args.delegated_program_batch and (not args.program_provenance_key_file or not (args.ollama_model or args.api_config)):
        parser.error("Delegated official batch requires private provenance key and explicit model")
    if not args.delegated_program_batch and any(program_flags) and not all(program_flags):
        parser.error("Program source requires snapshot, approval manifest, and private provenance key together")
    if all(program_flags) and not (args.ollama_model or args.api_config):
        parser.error("Program source requires an explicitly selected model")
    official_flags = (args.official_catalog, args.official_approval)
    if any(official_flags) and (not all(official_flags) or not all(program_flags)):
        parser.error("Official verification requires catalog, field approval, and approved Program snapshot together")
    secret = None
    if args.offline_public_cases_key_file:
        try:
            secret = load_private_secret(args.offline_public_cases_key_file)
        except ValueError as exc:
            parser.error(str(exc))
    cases_source = None
    if args.offline_public_cases:
        try:
            cases_source = OfflinePublicCasesSource(path=args.offline_public_cases_db,
                                                    expected_sha256=args.offline_public_cases_sha256,
                                                    secret=secret)
        except ValueError as exc:
            parser.error(str(exc))
    program_source = None
    if all(program_flags):
        try:
            program_source = ApprovedProgramSnapshot(
                args.program_snapshot, args.program_approval,
                provenance_secret=load_private_secret(args.program_provenance_key_file),
            )
        except (OSError, ValueError):
            parser.error("Program source approval or snapshot validation failed")
    official_source = None
    if all(official_flags):
        try:
            official_source = PinnedOfficialProgramSource(
                args.official_catalog, args.official_approval,
                program_source=program_source,
            )
        except (OSError, ValueError):
            parser.error("Official field approval or catalog validation failed")
    if args.delegated_program_batch:
        try:
            program_source, official_source = load_delegated_program_sources(
                args.delegated_program_batch,
                provenance_secret=load_private_secret(args.program_provenance_key_file),
            )
        except (OSError, ValueError):
            parser.error("Delegated Program authorization, batch mapping or fresh receipts invalid")
    if args.api_config:
        try:
            config = load_api_config(args.api_config)
            # Validate endpoint and model before the bridge begins accepting requests.
            provider = OpenAICompatibleProvider(**config, timeout_seconds=args.api_timeout)
        except ValueError as exc:
            parser.error(str(exc))
        factory = lambda: provider
        provider_mode = "REMOTE_API"
    elif args.ollama_model:
        factory = lambda: LocalOllamaJSONProvider(model=args.ollama_model,
                                                  timeout_seconds=args.ollama_timeout)
        provider_mode = "LOCAL_OLLAMA"
    else:
        factory = None
        provider_mode = "LOCAL_OLLAMA"
    with OfflineWorkspaceApi(local_provider_factory=factory, provider_mode=provider_mode,
                             db_path=args.sqlite_file,
                             offline_public_cases_source=cases_source,
                             allow_remote_public_cases=args.remote_public_cases,
                             program_source=program_source,
                             official_source=official_source) as api:
        server = HTTPServer(("127.0.0.1", args.port), make_handler(api))
        print(f"Synthetic workspace ({api.agent_mode}): http://127.0.0.1:{args.port}/?bridge=offline", flush=True)
        try: server.serve_forever()
        finally: server.server_close()


if __name__ == "__main__":
    main()
