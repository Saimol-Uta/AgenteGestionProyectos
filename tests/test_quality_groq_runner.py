"""Offline integration tests: real Quality agent, fake HTTP boundary."""
import importlib.util
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "quality_runner", Path(__file__).parents[1] / "scripts/check_quality_groq.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
SHA = "a" * 40
PRIVATE = "private-marker-not-for-output"


def fixture():
    return {
        "project_id": "synthetic-project",
        "draft": {"summary": PRIVATE, "task_state_claims": [], "risks": [],
                  "suggested_actions": [], "referenced_task_ids": [], "evidence_ids": []},
        "snippets": [{"repository": "synthetic/repo", "commit_sha": SHA,
                      "file_path": "src/example.py", "lines": [1],
                      "content": "query = f'SELECT * FROM users WHERE u = {user}'",
                      "evidence_scope": "diff", "is_complete": False}],
        "rules": [{"id": "SEC", "description": "Review SQL construction",
                   "metric_key": "open_critical_findings", "source": "review"}],
        "evidence_ids": [SHA],
    }


def candidate():
    return {"id": "candidate", "rule_id": "SEC", "claim": "SQL injection in query",
            "severity": "high", "evidence_ids": [SHA], "commit_sha": SHA,
            "file_path": "src/example.py", "line": 1, "confidence": 0.8,
            "requires_verification": True, "claim_basis": "visible_code"}


def response(finding=None, finish="stop", usage=None):
    body = {"choices": [{"finish_reason": finish, "message": {
        "content": json.dumps({"findings": [finding or candidate()]})}}]}
    if usage is not None:
        body["usage"] = usage
    return body


class QualityRunnerTests(unittest.TestCase):
    def execute(self, data=None, body=None, error=None, status=200, allow=True, key=True):
        calls = []

        def post(url, **kwargs):
            calls.append((url, kwargs))
            if error:
                raise error
            result = unittest.mock.Mock()
            result.status_code = status
            result.json.return_value = body if body is not None else response()
            return result

        env = {"ALLOW_REMOTE_AI": "true" if allow else "false"}
        if key:
            env["GROQ_API_KEY"] = "synthetic-key"
        with patch.dict("os.environ", env, clear=True), patch.object(runner.requests, "post", post):
            report = runner.run(data or fixture(), allow_real=allow)
        text = json.dumps(report)
        for private in (PRIVATE, "synthetic-key", SHA, "src/example.py", "SQL injection"):
            self.assertNotIn(private, text)
        return report, calls

    def test_valid_one_post_and_coherent_budgets(self):
        report, calls = self.execute(body=response(usage={"prompt_tokens": 100, "completion_tokens": 20}))
        self.assertEqual(report["status"], "validated")
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["prompt_tokens"], 100)
        self.assertEqual(len(calls), 1)
        url, kwargs = calls[0]
        self.assertEqual(url, runner.ENDPOINT)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertFalse(kwargs["allow_redirects"])
        body = kwargs["json"]
        self.assertEqual(body["model"], "llama-3.3-70b-versatile")
        self.assertEqual(body["max_tokens"], 768)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        context = json.loads(body["messages"][1]["content"])
        self.assertIn("query", json.dumps(context))
        self.assertIn(SHA, json.dumps(context))
        self.assertEqual(context["context_metadata"]["context_window_tokens"], 8192)
        self.assertLessEqual(context["context_metadata"]["budget_tokens"], 4096)

    def test_invalid_references_and_claims_stop(self):
        variants = [{"commit_sha": "wrong"}, {"file_path": "wrong.py"}, {"line": 2},
                    {"evidence_ids": ["invented"]}, {"rule_id": "invented"},
                    {"evidence_ids": []}, {"claim": ""}, {"requires_verification": False},
                    {"claim": "Missing input validation", "claim_basis": "file_absence"},
                    {"claim": "Coverage unavailable therefore tests missing"},
                    {"claim_basis": "policy_metric", "metric_key": "coverage_percentage",
                     "observed_value": 0}]
        for changes in variants:
            with self.subTest(changes=changes):
                finding = candidate() | changes
                report, calls = self.execute(body=response(finding))
                self.assertEqual(report["status"], "failed")
                self.assertEqual(len(calls), 1)
                self.assertEqual(report["attempts"], 1)

    def test_ci_policy_unknown_and_full_file_absence(self):
        data = fixture()
        data["rules"][0].update(source="ci", metric_key="coverage_percentage")
        report, _ = self.execute(data, response(candidate() | {"claim_basis": "policy_metric",
                                 "metric_key": "coverage_percentage", "observed_value": 0}))
        self.assertEqual(report["status"], "failed")
        data = fixture()
        data["snippets"][0].update(evidence_scope="full_file", is_complete=True, total_lines=1)
        report, calls = self.execute(data, response(candidate() | {
            "claim_basis": "file_absence", "claim": "Missing input validation"}))
        self.assertEqual(report["status"], "validated")
        self.assertEqual(len(calls), 1)

    def test_provider_failures_never_repeat_post(self):
        cases = [(response(finish="length"), None, 200),
                 ({"error": {"message": PRIVATE}}, None, 429),
                 (None, TimeoutError(PRIVATE), 200),
                 (None, TypeError(PRIVATE), 200), ({"choices": []}, None, 200)]
        for body, error, status in cases:
            with self.subTest(status=status, error=type(error).__name__):
                report, calls = self.execute(body=body, error=error, status=status)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(len(calls), 1)

    def test_preflight_and_budget_zero_posts(self):
        for options in ({"allow": False}, {"key": False}):
            report, calls = self.execute(**options)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(calls, [])
        for field in ("draft", "snippets"):
            data = fixture()
            if field == "draft":
                data[field]["summary"] = "large " * 20000
            else:
                data[field][0]["content"] = "large " * 20000
            report, calls = self.execute(data)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(calls, [])

    def test_empty_and_malformed_input_and_usage_unknown(self):
        data = fixture()
        data["snippets"] = []
        report, calls = self.execute(data)
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(calls, [])
        data = fixture()
        data["snippets"][0]["lines"] = ["1"]
        report, calls = self.execute(data)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(calls, [])
        report, _ = self.execute()
        self.assertIsNone(report["prompt_tokens"])

    def test_schema_redaction_and_redirect_fail_closed(self):
        for body, status in (({"choices": [{"message": {"content": "{}"}}]}, 200),
                             ({"choices": [{"message": {"content": PRIVATE}}]}, 200),
                             (response(), 302)):
            report, calls = self.execute(body=body, status=status)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(len(calls), 1)
        data = fixture()
        data["snippets"][0]["content"] = "GROQ_API_KEY=gsk_synthetic_secret"
        report, calls = self.execute(data)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(calls, [])

    def test_cli_static_json_and_controlled_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(fixture()))
            with patch.dict("os.environ", {"ALLOW_REMOTE_AI": "false"}, clear=True):
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(runner.main(["--input-json", str(path)]), 1)
                    report = json.loads(output.getvalue())
            self.assertEqual(report["reason"], "remote_not_authorized")
            self.assertNotIn(PRIVATE, json.dumps(report))

    def test_protected_inputs_rejected_before_open(self):
        for path in (".env", ".env.example", "credentials.json", ".ssh/key.json"):
            with patch("builtins.open", side_effect=AssertionError("must not open")):
                self.assertEqual(runner.main(["--input-json", path]), 1)
