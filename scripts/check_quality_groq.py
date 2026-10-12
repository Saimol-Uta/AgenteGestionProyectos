"""Isolated Quality/Groq check; offline tests are not real acceptance.

Input JSON: project_id, draft (MonitoringDraft), snippets (CodeDiffSnippet[]),
rules (QualityRule[]), evidence_ids (string[]). No source acquisition or storage.
Conservative local limits, not advertised provider limits; Quality keeps 768 output.
"""
import argparse
import json
from json import loads
import os
from pathlib import Path

import requests
from pydantic import SecretStr
from agente_gestion.adapters.llm.groq import GroqProvider
from agente_gestion.agents.context import ContextBuilder
from agente_gestion.agents.quality import CodeQualityAgent
from agente_gestion.application.generation import GlobalBudgetTracker, StageInvocationCoordinator
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.report import MonitoringDraft

MODEL = "llama-3.3-70b-versatile"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class OnePost:
    """Accept adapter fallback signatures, but consume the cap BEFORE HTTP."""
    def __init__(self):
        self.count = 0

    def __call__(self, url, headers, payload=None, json=None, timeout=30, **kwargs):
        if self.count or url != ENDPOINT:
            raise RuntimeError("transport stopped")
        self.count += 1
        response = requests.post(url, headers=headers,
                                 json=payload if payload is not None else json,
                                 timeout=30, allow_redirects=False)
        if 300 <= response.status_code < 400:
            raise RuntimeError("redirect stopped")
        body = response.json()
        if 200 <= response.status_code < 300:
            choices = body.get("choices", [])
            if choices and choices[0].get("finish_reason") != "length":
                content = loads(choices[0].get("message", {}).get("content", ""))
                if type(content) is not dict or type(content.get("findings")) is not list:
                    raise ValueError("output contract")
        return {"status_code": response.status_code, "body": body}


def parse_input(data):
    if type(data) is not dict or set(data) != {
            "project_id", "draft", "snippets", "rules", "evidence_ids"}:
        raise ValueError("input contract")
    if type(data["project_id"]) is not str or not data["project_id"].strip():
        raise ValueError("project required")
    for key in ("snippets", "rules", "evidence_ids"):
        if type(data[key]) is not list:
            raise ValueError("list required")
    if any(type(e) is not str or not e.strip() for e in data["evidence_ids"]):
        raise ValueError("evidence identifiers required")

    def domain(model, value):
        if type(value) is not dict or set(value) - set(model.model_fields):
            raise ValueError("domain fields")
        # JSON strict mode accepts domain enum strings, but not numeric coercions.
        return model.model_validate_json(json.dumps(value), strict=True)

    draft = domain(MonitoringDraft, data["draft"])
    snippets = [domain(CodeDiffSnippet, item) for item in data["snippets"]]
    rules = [domain(QualityRule, item) for item in data["rules"]]
    if any(not s.repository.strip() or not s.commit_sha.strip() or not s.file_path.strip()
           or not s.lines or not s.content.strip() for s in snippets):
        raise ValueError("snippet provenance required")
    if any(not r.id.strip() for r in rules) or len({r.id for r in rules}) != len(rules):
        raise ValueError("rule identifiers required")
    return draft, snippets, rules


def run(data, *, allow_real=False):
    transport = OnePost()
    coordinator = StageInvocationCoordinator(GlobalBudgetTracker(max_calls=1),
                                             max_transient_retries=0, max_repair_attempts=0)
    report = {"status": "failed", "reason": "input_rejected", "model": MODEL,
              "posts": 0, "attempts": 0, "candidate_count": 0,
              "provider_status": None, "prompt_tokens": None, "completion_tokens": None,
              "context_tokens": 8192, "evidence_tokens": 4096, "output_tokens": 768,
              "timeout_seconds": 30, "retries": 0, "repairs": 0}
    try:
        draft, snippets, rules = parse_input(data)
        if not snippets:
            report.update(status="skipped", reason="no_diffs")
            return report
        report["reason"] = "remote_not_authorized"
        if not allow_real or os.environ.get("ALLOW_REMOTE_AI", "").lower() != "true":
            return report
        report["reason"] = "missing_key"
        if not os.environ.get("GROQ_API_KEY"):
            return report
        key = SecretStr(os.environ["GROQ_API_KEY"])
        provider = GroqProvider(api_key=key.get_secret_value(), model=MODEL,
                                allow_remote_ai=True, context_tokens=8192,
                                max_output_tokens=768, transport=transport)
        agent = CodeQualityAgent(provider, model=MODEL, coordinator=coordinator,
                                 context_builder=ContextBuilder(default_budget_tokens=4096),
                                 max_output_tokens=768, timeout_seconds=30)
        report["reason"] = "generation_rejected"
        findings = agent.run(data["project_id"], draft, snippets, rules=rules,
                             evidence_ids=data["evidence_ids"])
        if any(f.requires_verification is not True for f in findings):
            raise ValueError("candidate authority rejected")
        report.update(status="validated", reason="candidate_only", candidate_count=len(findings))
    except Exception:
        # Never serialize exception text, model output, input or validator diagnostics.
        pass
    finally:
        report["posts"] = transport.count
        report["attempts"] = coordinator.budget_tracker.total_calls_made
        if coordinator.recorded_attempts:
            attempt = coordinator.recorded_attempts[-1]
            report["provider_status"] = attempt.status.value
            for field in ("prompt_tokens", "completion_tokens"):
                count = getattr(attempt, field)
                report[field] = count if type(count) is int and count >= 0 else None
    return report


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("arguments rejected")


def main(argv=None):
    parser = SafeParser(description="Isolated Quality check; real calls require fresh human permission.")
    parser.add_argument("--input-json", required=True, help="Explicit captured/static domain JSON, not credentials")
    parser.add_argument("--allow-real", action="store_true", help="Also requires ALLOW_REMOTE_AI=true")
    try:
        args = parser.parse_args(argv)
        path = Path(args.input_json)
        protected = {".ssh", ".aws", ".config", ".kube", ".gnupg", "secrets"}
        resolved = path.resolve()
        if (path.is_symlink() or resolved.suffix.lower() != ".json"
                or protected.intersection(p.lower() for p in resolved.parts)
                or any(word in part.lower() for part in resolved.parts
                       for word in ("credential", "secret", "token", ".env"))):
            raise ValueError("protected input")
        with open(resolved, encoding="utf-8") as stream:
            raw = stream.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("input size")
        report = run(json.loads(raw), allow_real=args.allow_real)
    except Exception:
        report = {"status": "failed", "reason": "input_rejected", "posts": 0, "attempts": 0}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in {"validated", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
