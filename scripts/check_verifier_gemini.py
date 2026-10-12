"""Isolated Verifier/Gemini check; offline tests are not real acceptance.

Input JSON: project_id, findings (CandidateFinding[]), snippets (CodeDiffSnippet[]),
rules (QualityRule[]), evidence_ids (string[]). No other agent execution or storage.
Conservative local limits: gemini-3.8-flash, max 1 POST, output 1024.
"""
import argparse
import json
from json import loads
import os
from pathlib import Path

import requests
from pydantic import SecretStr
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.agents.context import ContextBuilder
from agente_gestion.agents.verifier import VerifierAgent
from agente_gestion.application.generation import GlobalBudgetTracker, StageInvocationCoordinator
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.policies import QualityRule

MODEL = "gemini-3.8-flash"
ENDPOINT_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class OnePostGemini:
    """Accept adapter fallback signatures, but consume the cap BEFORE HTTP."""
    def __init__(self, model):
        self.count = 0
        self.model = model
        self.endpoint = ENDPOINT_TEMPLATE.format(model=model)

    def __call__(self, url, headers, payload=None, json=None, timeout=30, **kwargs):
        if self.count or url != self.endpoint:
            raise RuntimeError("transport stopped")
        self.count += 1
        body = payload if payload is not None else json
        response = requests.post(url, headers=headers, json=body,
                                 timeout=30, allow_redirects=False)
        if 300 <= response.status_code < 400:
            raise RuntimeError("redirect stopped")
        resp_json = response.json() if response.text else {}
        if 200 <= response.status_code < 300:
            candidates = resp_json.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and "text" in parts[0]:
                    content = loads(parts[0]["text"])
                    if type(content) is not dict or type(content.get("verifications")) is not list:
                        raise ValueError("output contract")
        return {"status_code": response.status_code, "body": resp_json, "text": response.text}


def parse_input(data):
    if type(data) is not dict or set(data) != {
            "project_id", "findings", "snippets", "rules", "evidence_ids"}:
        raise ValueError("input contract")
    if type(data["project_id"]) is not str or not data["project_id"].strip():
        raise ValueError("project required")
    for key in ("findings", "snippets", "rules", "evidence_ids"):
        if type(data[key]) is not list:
            raise ValueError("list required")
    if any(type(e) is not str or not e.strip() for e in data["evidence_ids"]):
        raise ValueError("evidence identifiers required")

    def domain(model_cls, value):
        if type(value) is not dict or set(value) - set(model_cls.model_fields):
            raise ValueError("domain fields")
        return model_cls.model_validate_json(json.dumps(value), strict=True)

    findings = [domain(CandidateFinding, item) for item in data["findings"]]
    snippets = [domain(CodeDiffSnippet, item) for item in data["snippets"]]
    rules = [domain(QualityRule, item) for item in data["rules"]]
    if any(not s.repository.strip() or not s.commit_sha.strip() or not s.file_path.strip()
           or not s.lines or not s.content.strip() for s in snippets):
        raise ValueError("snippet provenance required")
    if any(not r.id.strip() for r in rules) or len({r.id for r in rules}) != len(rules):
        raise ValueError("rule identifiers required")
    if any(not f.id.strip() for f in findings) or len({f.id for f in findings}) != len(findings):
        raise ValueError("finding identifiers required")
    return findings, snippets, rules


def run(data, *, allow_real=False, model=MODEL):
    transport = OnePostGemini(model)
    coordinator = StageInvocationCoordinator(GlobalBudgetTracker(max_calls=1),
                                             max_transient_retries=0, max_repair_attempts=0)
    report = {"status": "failed", "reason": "input_rejected", "model": model,
              "posts": 0, "attempts": 0, "verification_count": 0,
              "verdicts": {},
              "provider_status": None, "prompt_tokens": None, "completion_tokens": None,
              "context_tokens": 1048576, "evidence_tokens": 4096, "output_tokens": 1024,
              "timeout_seconds": 30, "retries": 0, "repairs": 0}
    try:
        findings, snippets, rules = parse_input(data)
        if not findings:
            report.update(status="skipped", reason="no_findings")
            return report
        report["reason"] = "remote_not_authorized"
        if not allow_real or os.environ.get("ALLOW_REMOTE_AI", "").lower() != "true":
            return report
        report["reason"] = "missing_key"
        if not os.environ.get("GEMINI_API_KEY"):
            return report
        key = SecretStr(os.environ["GEMINI_API_KEY"])
        provider = GeminiProvider(api_key=key.get_secret_value(), model=model,
                                  allow_remote_ai=True, context_tokens=1048576,
                                  max_output_tokens=1024, transport=transport)
        agent = VerifierAgent(provider, model=model, coordinator=coordinator,
                              context_builder=ContextBuilder(default_budget_tokens=4096),
                              timeout_seconds=30)
        report["reason"] = "generation_rejected"
        verifications = agent.run(data["project_id"], findings, snippets, rules=rules,
                                  evidence_ids=data["evidence_ids"])
        verdict_counts = {}
        for v in verifications:
            val = v.verdict.value if hasattr(v.verdict, "value") else str(v.verdict)
            verdict_counts[val] = verdict_counts.get(val, 0) + 1
        report.update(status="validated", reason="verified_only",
                      verification_count=len(verifications), verdicts=verdict_counts)
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
    parser = SafeParser(description="Isolated Verifier check; real calls require fresh human permission.")
    parser.add_argument("--input-json", required=True, help="Explicit captured/static domain JSON, not credentials")
    parser.add_argument("--allow-real", action="store_true", help="Also requires ALLOW_REMOTE_AI=true")
    parser.add_argument("--model", default=MODEL, help="Gemini model identifier")
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
        report = run(json.loads(raw), allow_real=args.allow_real, model=args.model)
    except Exception:
        report = {"status": "failed", "reason": "input_rejected", "posts": 0, "attempts": 0}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in {"validated", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

