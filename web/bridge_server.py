"""Same-origin, loopback-only HTTP exercise for the synthetic V2 workspace."""

from __future__ import annotations

import json
import argparse
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sales_agent.v2_offline_api import OfflineOnlyError, OfflineWorkspaceApi  # noqa: E402
from sales_agent.v2_pipeline import LocalOllamaJSONProvider  # noqa: E402

WEB = Path(__file__).resolve().parent
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
          "/api.js": "api.js", "/bridge_api.js": "bridge_api.js",
          "/styles.css": "styles.css", "/overrides.css": "overrides.css",
          "/manifest.webmanifest": "manifest.webmanifest"}
MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".webmanifest": "application/manifest+json"}


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
    parser.add_argument("--sqlite-file", type=Path,
                        help="Explicit local SQLite file for synthetic workspace persistence")
    args = parser.parse_args()
    factory = (lambda: LocalOllamaJSONProvider(model=args.ollama_model,
                                               timeout_seconds=args.ollama_timeout)) if args.ollama_model else None
    with OfflineWorkspaceApi(local_provider_factory=factory, db_path=args.sqlite_file) as api:
        server = HTTPServer(("127.0.0.1", args.port), make_handler(api))
        print(f"Synthetic workspace ({api.agent_mode}): http://127.0.0.1:{args.port}/?bridge=offline", flush=True)
        try: server.serve_forever()
        finally: server.server_close()


if __name__ == "__main__":
    main()
