"""Pinned Monitoring-only runner. Real execution requires separate owner permission."""
import sys
from datetime import datetime
import json as jsonlib
import os
import re
from zoneinfo import ZoneInfo

import requests

from agente_gestion.adapters.sources.trello import TrelloSourceAdapter
from agente_gestion.adapters.sources.actions import GitHubActionsSourceAdapter
from agente_gestion.adapters.sources.http_transport import BoundedGetTransport
from agente_gestion.ports.source_errors import SourceError, SourceProvider
from agente_gestion.domain.tasks import TaskMappingConfig, calculate_task_metrics
from agente_gestion.domain.code import match_ci_for_commit
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.agents.monitoring import ProjectMonitoringAgent
from agente_gestion.agents.context import ContextBuilder
from agente_gestion.application.generation import GlobalBudgetTracker, StageInvocationCoordinator
from agente_gestion.application.validation import ValidationError

PROJECT, BOARD = "HORMIGESTION", "6a957e9173d4d13d0c5db608"
OWNER, REPO = "Josu-F1", "hormigestion-backend"
SHA = "5412744023c4b3961be9cd783609423d12280f64"
TIMEZONE, MODEL = "America/Guayaquil", "gemini-3.5-flash-lite"
BOARD_URL = f"https://api.trello.com/1/boards/{BOARD}"
REPO_URL = f"https://api.github.com/repos/{OWNER}/{REPO}"
COMMIT_URL, ACTIONS_URL = f"{REPO_URL}/commits/{SHA}", f"{REPO_URL}/actions/runs"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


class ScopedGet(BoundedGetTransport):
    """Exact endpoint allowlist plus a shared availability/CI request budget."""
    def __init__(self, provider, urls, maximum):
        super().__init__(provider=provider, timeout_seconds=15)
        self.urls, self.maximum, self.count = urls, maximum, 0

    def get(self, url, params=None, headers=None, timeout_seconds=None):
        if url not in self.urls or self.count >= self.maximum:
            raise ValueError("Source scope or budget rejected")
        self.count += 1
        return super().get(url, params=params, headers=headers, timeout_seconds=15)


class OneShotGemini:
    """Consume the callback before HTTP: provider TypeError fallbacks cannot resend."""
    def __init__(self, metadata):
        self.count, self.metadata = 0, metadata

    def __call__(self, url, headers, payload=None, json=None, timeout=30, **kwargs):
        if self.count or url != GEMINI_URL:
            raise RuntimeError("Generation already consumed or endpoint rejected")
        self.count += 1
        body = payload if payload is not None else json
        context = jsonlib.loads(body["contents"][0]["parts"][0]["text"].split(
            "Evidence Context:\n", 1)[1])
        self.metadata["estimated_budget"] = {key: value for key, value in context["context_metadata"].items()
                                             if type(value) is int}
        self.metadata["outbound_fields"] = {key: type(value).__name__ for key, value in context.items()}
        self.metadata["outbound_task_fields"] = {
            key: type(value).__name__ for row in context["tasks"] for key, value in row.items()}
        self.metadata["outbound_ci_fields"] = {
            key: type(value).__name__ for key, value in (context["ci_result"] or {}).items()}
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(url, headers=headers, json=body, timeout=30, allow_redirects=False)
            try:
                if 300 <= response.status_code < 400:
                    raise RuntimeError("Redirect rejected")
                data = response.json()
                usage = data.get("usageMetadata", {})
                self.metadata["usage"] = {key: usage[key] for key in (
                    "promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount")
                    if type(usage.get(key)) is int}
                return {"status_code": response.status_code, "body": data}
            finally:
                response.close()


def validation_diagnostics(errors):
    """One bounded, allowlisted diagnostic per recorded validator item; never echo input."""
    diagnostics = []
    schema_fields = ("summary", "task_state_claims", "risks", "suggested_actions",
                     "referenced_task_ids", "evidence_ids", "ci_status_note")
    patterns = (
        (r"Referenced task ID '[\s\S]*' does not exist in available task context\.",
         "unknown_task_reference", ["referenced_task_ids"], "Task reference is outside supplied context."),
        (r"Referenced evidence ID '[\s\S]*' does not exist in supplied context\.",
         "unknown_evidence_reference", ["evidence_ids"], "Evidence reference is outside supplied context."),
        (r"Task state claim '[\s\S]*' contradicts deterministic TaskCard\.state\.",
         "task_state_mismatch", ["task_state_claims"], "Task state contradicts deterministic state."),
        (r"Mentioned task '[\s\S]*' must occur in referenced_task_ids and evidence_ids\.",
         "missing_task_grounding", ["referenced_task_ids", "evidence_ids"],
         "Mentioned task requires both task and evidence references."),
        (
            r"Duplicate referenced task ID in draft: '[\s\S]*'\.",
            "duplicate_task_reference",
            ["referenced_task_ids"],
            "Task reference occurs more than once.",
        ),
        (
            r"Duplicate referenced evidence ID in draft: '[\s\S]*'\.",
            "duplicate_evidence_reference",
            ["evidence_ids"],
            "Evidence reference occurs more than once.",
        ),
        (
            r"Insufficient task-status attribution after multiple task mentions; cite the exact task ID or request additional context\.",
            "ambiguous_task_status_attribution",
            ["summary", "risks", "suggested_actions", "ci_status_note"],
            "Narrative task status requires explicit attribution.",
        ),
        (
            r"Narrative task '[\s\S]*' state "
            r"'(?:in_progress|completed|pending|unknown|blocked)' "
            r"contradicts deterministic "
            r"'(?:en_curso|completada|pending|bloqueada|desconocido)'\.",
            "narrative_task_state_mismatch",
            ["summary", "risks", "suggested_actions", "ci_status_note"],
            "Narrative task state contradicts deterministic state.",
        ),
        (
            r"Narrative references an omitted task outside selected evidence; request additional context\.",
            "omitted_task_reference",
            ["summary", "risks", "suggested_actions", "ci_status_note"],
            "Narrative refers to a task outside selected evidence.",
        ),
    )
    for error in errors:
        code, fields, message = "unclassified_validation_error", [], "Unclassified validator rejection."
        if isinstance(error, str):
            if re.match(r"\ASchema validation error: \d+ validation errors? for MonitoringDraftSchema\n", error):
                # Pydantic aggregates multiple field errors into one validator item.
                # Match only static top-level paths and markers, not input_value or dynamic paths.
                missing, invalid = [], []
                for field in schema_fields:
                    marker = re.search(r"(?m)^" + re.escape(field) + r"\n  ([^\n]+)", error)
                    if marker and "[type=missing," in marker[1] and marker[1].startswith("Field required "):
                        missing.append(field)
                    elif marker and re.search(r"\[type=(?:list_type|string_type|model_type),", marker[1]):
                        invalid.append(field)
                if missing:
                    code, fields, message = "missing_required_fields", missing, "Required schema fields are missing."
                else:
                    code, fields, message = "invalid_schema", invalid, "Output does not satisfy Monitoring schema."
            else:
                for pattern, known_code, known_fields, description in patterns:
                    if re.fullmatch(pattern, error):
                        code, fields, message = known_code, known_fields, description
                        break
        diagnostics.append({"code": code, "fields": fields, "message": message})
    return diagnostics


def main(argv=None) -> int:
    # Avoid argparse locale/terminal environment lookups, including on --help.
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--help"]:
        print("Usage: check_monitoring_gemini.py --due-soon-days N [--allow-real]\n"
              "N: required nonnegative owner-selected test-only window, not project policy.\n"
              "Real calls require separate authorization and ALLOW_REMOTE_AI=true.")
        return 0
    allow_real = "--allow-real" in arguments
    try:
        if allow_real:
            arguments.remove("--allow-real")
        if len(arguments) != 2 or arguments[0] != "--due-soon-days":
            raise ValueError
        due_soon_days = int(arguments[1])
        if due_soon_days < 0:
            raise ValueError
    except ValueError:
        print(jsonlib.dumps({"error_category": "invalid_arguments", "generation_status": "not_started"}))
        return 2
    metadata = {"project": PROJECT, "board": BOARD, "repository": f"{OWNER}/{REPO}",
                "source_commit_sha": SHA, "timezone": TIMEZONE, "model": MODEL,
                "state_strategy": "date_fields", "source_is_complete": None,
                "due_soon_days": due_soon_days, "window_scope": "test_only",
                "request_counts": {"trello": 0, "github": 0, "gemini": 0},
                "request_limits": {"trello": 10, "github": 3, "gemini": 1},
                "timeouts_seconds": {"sources": 15, "gemini": 30},
                "context_tokens": 8192, "context_budget": 4096, "max_output_tokens": 2048,
                "retries": 0, "repairs": 0, "generation_status": "not_started", "validation_errors": []}
    trello_http = github_http = coordinator = None
    gemini_http = None
    code = 1
    try:
        if not allow_real or os.environ.get("ALLOW_REMOTE_AI", "").lower() != "true":
            metadata["error_category"] = "permission_required"
            return code
        cutoff = datetime.now(ZoneInfo(TIMEZONE))
        metadata["cutoff"] = cutoff.isoformat()
        credentials = {name: os.environ.get(name, "") for name in
                       ("TRELLO_API_KEY", "TRELLO_TOKEN", "GEMINI_API_KEY")}
        if not all(credentials.values()):
            metadata["error_category"] = "credentials_missing"
            return code
        token = os.environ.get("GITHUB_TOKEN") or None
        github_http = ScopedGet(SourceProvider.github_actions, {COMMIT_URL, ACTIONS_URL}, 3)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        commit = github_http.get(COMMIT_URL, headers=headers).body
        if not isinstance(commit, dict) or commit.get("sha") != SHA:
            metadata["error_category"] = "commit_unavailable"
            return code
        trello_http = ScopedGet(SourceProvider.trello,
                                {BOARD_URL + "/lists/all", BOARD_URL + "/cards/all",
                                 BOARD_URL + "/members"}, 10)
        trello = TrelloSourceAdapter(board_id=BOARD, api_key=credentials["TRELLO_API_KEY"],
            token=credentials["TRELLO_TOKEN"], transport=trello_http, state_strategy="date_fields",
            mapping_config=TaskMappingConfig(blocked_labels=[], included_list_ids=None),
            timezone_name=TIMEZONE, clock=lambda: cutoff)
        tasks = trello.get_tasks(PROJECT)
        actions = GitHubActionsSourceAdapter(owner=OWNER, repo=REPO, token=token, commit_sha=SHA,
                    transport=github_http, max_runs=200, max_pages=2, max_requests=2)
        runs = actions.get_ci_results(PROJECT)
        if not actions.last_collection_complete:
            metadata["error_category"] = "source_partial"
            return code
        ci = match_ci_for_commit(runs, SHA)
        evidence_ids = [e.provenance.id for e in actions.get_evidence(PROJECT)
                        if ci is not None and e.provenance.external_id == ci.id]
        gemini_http = OneShotGemini(metadata)
        provider = GeminiProvider(api_key=credentials["GEMINI_API_KEY"], model=MODEL,
                    allow_remote_ai=True, context_tokens=8192, max_output_tokens=8192, transport=gemini_http)
        coordinator = StageInvocationCoordinator(budget_tracker=GlobalBudgetTracker(max_calls=1),
                                                max_transient_retries=0, max_repair_attempts=0)
        agent = ProjectMonitoringAgent(provider=provider, model=MODEL, coordinator=coordinator,
                    context_builder=ContextBuilder(default_budget_tokens=4096), max_generation_tokens=2048)
        agent.run(PROJECT, calculate_task_metrics(tasks, cutoff, timezone_name=TIMEZONE,
                  due_soon_days=due_soon_days), tasks, ci_result=ci, evidence_ids=evidence_ids,
                  source_is_complete=None, source_commit_sha=SHA)
        metadata["generation_status"] = "validated"
        code = 0
    except SourceError as error:
        metadata["error_category"] = "source_" + error.category.value
    except ValidationError:
        metadata["error_category"] = "validation_failed"
    except Exception:
        metadata["error_category"] = "execution_failed"
    finally:
        metadata["request_counts"] = {"trello": trello_http.count if trello_http else 0,
                                      "github": github_http.count if github_http else 0,
                                      "gemini": gemini_http.count if gemini_http else 0}
        if coordinator and coordinator.recorded_attempts:
            attempt = coordinator.recorded_attempts[-1]
            metadata["provider_status"] = attempt.status.value
            metadata["generation_status"] = ("validated" if code == 0 else
                "validation_failed" if attempt.status.value == "complete" else attempt.status.value)
            metadata["validation_passed"] = attempt.validation_passed
            if attempt.status.value == "complete" and attempt.validation_passed is False:
                metadata["validation_errors"] = validation_diagnostics(attempt.validation_errors)
        print(jsonlib.dumps(metadata, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
