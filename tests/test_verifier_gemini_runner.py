"""Offline integration tests: real Verifier agent, fake Gemini HTTP boundary."""
import importlib.util
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "verifier_runner", Path(__file__).parents[1] / "scripts/check_verifier_gemini.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

SHA = "a" * 40
PRIVATE = "private-marker-not-for-output"


def candidate(finding_id="CQ-001", rule_id="SEC", line=1):
    return {
        "id": finding_id,
        "rule_id": rule_id,
        "claim": "SQL injection in query construction",
        "severity": "high",
        "evidence_ids": [SHA],
        "commit_sha": SHA,
        "file_path": "src/example.py",
        "line": line,
        "confidence": 0.85,
        "requires_verification": True,
        "claim_basis": "visible_code",
        "evidence": "query = f'SELECT * FROM users WHERE u = {user}'",
        "suggested_fix": "Use parameterized queries",
    }


def fixture(finding=None):
    return {
        "project_id": "synthetic-project",
        "findings": [finding or candidate()],
        "snippets": [{
            "repository": "synthetic/repo",
            "commit_sha": SHA,
            "file_path": "src/example.py",
            "lines": [1],
            "content": "query = f'SELECT * FROM users WHERE u = {user}'",
            "evidence_scope": "diff",
            "is_complete": False,
        }],
        "rules": [{
            "id": "SEC",
            "description": "Review SQL construction",
            "metric_key": "open_critical_findings",
            "source": "review",
            "mandatory": True,
        }],
        "evidence_ids": [SHA],
    }


def default_verification(verdict="confirmed", finding_id="CQ-001", e_ids=None):
    return {
        "finding_id": finding_id,
        "verdict": verdict,
        "reason": f"Independent inspection resulted in {verdict}",
        "evidence_ids": e_ids if e_ids is not None else [SHA],
    }


def gemini_response(verifications=None, usage=None):
    body = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({"verifications": verifications or [default_verification()]})
                }]
            },
            "finishReason": "STOP",
        }]
    }
    if usage is not None:
        body["usageMetadata"] = usage
    return body


class VerifierRunnerTests(unittest.TestCase):
    def execute(self, data=None, body=None, error=None, status=200, allow=True, key=True, model="gemini-3.8-flash"):
        calls = []

        def post(url, **kwargs):
            calls.append((url, kwargs))
            if error:
                raise error
            result = unittest.mock.Mock()
            result.status_code = status
            result.json.return_value = body if body is not None else gemini_response()
            result.text = json.dumps(result.json.return_value)
            return result

        env = {"ALLOW_REMOTE_AI": "true" if allow else "false"}
        if key:
            env["GEMINI_API_KEY"] = "synthetic-key"
        with patch.dict("os.environ", env, clear=True), patch.object(runner.requests, "post", post):
            report = runner.run(data or fixture(), allow_real=allow, model=model)
        text = json.dumps(report)
        for private in ("synthetic-key", SHA, "src/example.py", "SQL injection"):
            self.assertNotIn(private, text)
        return report, calls

    def test_valid_one_post_confirmed(self):
        usage = {"promptTokenCount": 500, "candidatesTokenCount": 80}
        report, calls = self.execute(body=gemini_response(usage=usage))
        self.assertEqual(report["status"], "validated")
        self.assertEqual(report["verification_count"], 1)
        self.assertEqual(report["verdicts"], {"confirmed": 1})
        self.assertEqual(report["prompt_tokens"], 500)
        self.assertEqual(report["completion_tokens"], 80)
        self.assertEqual(len(calls), 1)
        url, kwargs = calls[0]
        self.assertIn("gemini-3.8-flash", url)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertFalse(kwargs["allow_redirects"])
        payload = kwargs["json"]
        self.assertIn("contents", payload)
        self.assertIn("generationConfig", payload)
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 1024)

    def test_valid_rejected_verdict(self):
        verif = default_verification(verdict="rejected")
        report, calls = self.execute(body=gemini_response([verif]))
        self.assertEqual(report["status"], "validated")
        self.assertEqual(report["verdicts"], {"rejected": 1})
        self.assertEqual(len(calls), 1)

    def test_valid_insufficient_verdict(self):
        verif = default_verification(verdict="insufficient")
        report, calls = self.execute(body=gemini_response([verif]))
        self.assertEqual(report["status"], "validated")
        self.assertEqual(report["verdicts"], {"insufficient": 1})
        self.assertEqual(len(calls), 1)

    def test_empty_findings_skips_zero_post(self):
        empty_data = fixture()
        empty_data["findings"] = []
        report, calls = self.execute(data=empty_data)
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(report["reason"], "no_findings")
        self.assertEqual(len(calls), 0)

    def test_remote_disabled_fails_closed(self):
        report, calls = self.execute(allow=False)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "remote_not_authorized")
        self.assertEqual(len(calls), 0)

    def test_missing_key_fails_closed(self):
        report, calls = self.execute(key=False)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "missing_key")
        self.assertEqual(len(calls), 0)

    def test_invalid_finding_id_stops(self):
        verif = default_verification(finding_id="INVENTED_ID")
        report, calls = self.execute(body=gemini_response([verif]))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "generation_rejected")
        self.assertEqual(len(calls), 1)

    def test_evidence_id_not_subset_stops(self):
        verif = default_verification(e_ids=["invented_sha"])
        report, calls = self.execute(body=gemini_response([verif]))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "generation_rejected")
        self.assertEqual(len(calls), 1)

    def test_cli_execution_with_tempfile(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(fixture(), f)
            temp_path = f.name
        try:
            with patch("sys.stdout", new=io.StringIO()) as out:
                code = runner.main(["--input-json", temp_path])
            self.assertEqual(code, 1)
            output = json.loads(out.getvalue())
            self.assertEqual(output["status"], "failed")
            self.assertEqual(output["reason"], "remote_not_authorized")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_cli_protected_path_rejected(self):
        with patch("sys.stdout", new=io.StringIO()) as out:
            code = runner.main(["--input-json", "/tmp/.env.json"])
        self.assertEqual(code, 1)
        output = json.loads(out.getvalue())
        self.assertEqual(output["status"], "failed")
        self.assertEqual(output["reason"], "input_rejected")


if __name__ == "__main__":
    unittest.main()

