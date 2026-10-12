"""Isolated Report/Gemini check; offline tests are not real acceptance.

Input JSON: project_id, cutoff_datetime, metrics, draft, confirmed_findings,
rejected_findings, insufficient_findings, pending_findings, policy_evaluations,
evidence_ids (and optional ci_result). No other agent execution or storage.
Conservative local limits: gemini-3.5-flash-lite, max 1 POST, output 768.
"""
import argparse
from datetime import datetime
import json
from json import loads
import os
from pathlib import Path
from typing import Optional

import requests
from pydantic import SecretStr
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.agents.report import ReportAgent
from agente_gestion.application.generation import GlobalBudgetTracker, StageInvocationCoordinator
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.policies import PolicyEvaluationResult
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.tasks import DeterministicTaskMetrics

MODEL = "gemini-3.5-flash-lite"
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
                    if type(content) is not dict or "summary" not in content or "recommendation" not in content:
                        raise ValueError("output contract")
        return {"status_code": response.status_code, "body": resp_json, "text": response.text}


def parse_input(data):
    required_keys = {
        "project_id", "cutoff_datetime", "metrics", "draft",
        "confirmed_findings", "rejected_findings", "insufficient_findings",
        "pending_findings", "policy_evaluations", "evidence_ids"
    }
    allowed_keys = required_keys | {"ci_result"}
    if type(data) is not dict or not required_keys.issubset(set(data)) or (set(data) - allowed_keys):
        raise ValueError("input contract")
    if type(data["project_id"]) is not str or not data["project_id"].strip():
        raise ValueError("project required")
    if type(data["cutoff_datetime"]) is not str or not data["cutoff_datetime"].strip():
        raise ValueError("cutoff required")
    try:
        cutoff = datetime.fromisoformat(data["cutoff_datetime"])
    except Exception:
        raise ValueError("cutoff format")

    for key in ("confirmed_findings", "rejected_findings", "insufficient_findings",
                "pending_findings", "evidence_ids"):
        if type(data[key]) is not list:
            raise ValueError("list required")
    if any(type(e) is not str or not e.strip() for e in data["evidence_ids"]):
        raise ValueError("evidence identifiers required")

    def domain(model_cls, value):
        if type(value) is not dict:
            raise ValueError("domain fields")
        return model_cls.model_validate_json(json.dumps(value), strict=True)

    metrics = domain(DeterministicTaskMetrics, data["metrics"])
    draft = domain(MonitoringDraft, data["draft"])
    policy_evaluations = domain(PolicyEvaluationResult, data["policy_evaluations"])
    confirmed_findings = [domain(CandidateFinding, item) for item in data["confirmed_findings"]]
    rejected_findings = [domain(CandidateFinding, item) for item in data["rejected_findings"]]
    insufficient_findings = [domain(CandidateFinding, item) for item in data["insufficient_findings"]]
    pending_findings = [domain(CandidateFinding, item) for item in data["pending_findings"]]
    ci_result: Optional[CIRunResult] = None
    if "ci_result" in data and data["ci_result"] is not None:
        ci_result = domain(CIRunResult, data["ci_result"])

    return (cutoff, metrics, draft, confirmed_findings, rejected_findings,
            insufficient_findings, pending_findings, policy_evaluations, ci_result)


def run(data, *, allow_real=False, model=MODEL):
    transport = OnePostGemini(model)
    coordinator = StageInvocationCoordinator(GlobalBudgetTracker(max_calls=1),
                                             max_transient_retries=0, max_repair_attempts=0)
    report = {"status": "failed", "reason": "input_rejected", "model": model,
              "posts": 0, "attempts": 0, "recommendation": None, "human_decision": None,
              "summary_length": 0, "recommendations_count": 0,
              "provider_status": None, "prompt_tokens": None, "completion_tokens": None,
              "context_tokens": 8192, "output_tokens": 768,
              "timeout_seconds": 30, "retries": 0, "repairs": 0}
    try:
        (cutoff, metrics, draft, confirmed_findings, rejected_findings,
         insufficient_findings, pending_findings,
         policy_evaluations, ci_result) = parse_input(data)

        report["reason"] = "remote_not_authorized"
        if not allow_real or os.environ.get("ALLOW_REMOTE_AI", "").lower() != "true":
            return report
        report["reason"] = "missing_key"
        if not os.environ.get("GEMINI_API_KEY"):
            return report
        key = SecretStr(os.environ["GEMINI_API_KEY"])
        provider = GeminiProvider(api_key=key.get_secret_value(), model=model,
                                  allow_remote_ai=True, context_tokens=8192,
                                  max_output_tokens=768, transport=transport)
        agent = ReportAgent(provider=provider, model=model, coordinator=coordinator)
        report["reason"] = "generation_rejected"
        final_report = agent.run(
            project_id=data["project_id"],
            cutoff_datetime=cutoff,
            metrics=metrics,
            draft=draft,
            confirmed_findings=confirmed_findings,
            rejected_findings=rejected_findings,
            insufficient_findings=insufficient_findings,
            pending_findings=pending_findings,
            policy_evaluations=policy_evaluations,
            ci_result=ci_result,
            evidence_ids=data["evidence_ids"],
        )
        report.update(
            status="validated",
            reason="report_generated",
            recommendation=final_report.recommendation,
            human_decision=final_report.human_decision.value if hasattr(final_report.human_decision, "value") else str(final_report.human_decision),
            summary_length=len(final_report.summary),
            recommendations_count=len(final_report.recommendations),
        )
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
    parser = SafeParser(description="Isolated Report check; real calls require fresh human permission.")
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

