"""Offline integration tests: real Report agent, fake Gemini HTTP boundary."""
import importlib.util
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "report_runner", Path(__file__).parents[1] / "scripts/check_report_gemini.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

SHA = "a" * 40
PRIVATE = "private-marker-not-for-output"


def sample_metrics():
    return {
        "total_cards": 5,
        "included_cards": 5,
        "completed_cards": 2,
        "progress_percentage": 40.0,
        "overdue_card_ids": [],
        "blocked_card_ids": [],
        "unknown_card_ids": [],
        "contradictory_card_ids": [],
    }


def sample_draft():
    return {
        "summary": "Avance del 40.0% con 2 de 5 tareas completadas.",
        "risks": ["Riesgo de prueba"],
        "suggested_actions": ["Continuar monitoreo"],
        "referenced_task_ids": [],
        "evidence_ids": [SHA],
        "ci_status_note": None,
        "claimed_progress_percentage": 40.0,
        "claimed_completed_cards": 2,
        "claimed_total_cards": 5,
    }


def sample_policy_result(action="recomendar_aprobacion", failed_ids=None, unknown_ids=None):
    f_ids = failed_ids or []
    u_ids = unknown_ids or []
    evals = []
    if not f_ids and not u_ids:
        evals.append({
            "rule_id": "Q1",
            "description": "Regla base",
            "status": "pass",
            "observed_value": 0,
            "mandatory": True,
            "reason": None,
        })
    for fid in f_ids:
        evals.append({
            "rule_id": fid,
            "description": f"Regla fallida {fid}",
            "status": "fail",
            "observed_value": 1,
            "mandatory": True,
            "reason": "Test failure",
        })
    for uid in u_ids:
        evals.append({
            "rule_id": uid,
            "description": f"Regla desconocida {uid}",
            "status": "unknown",
            "observed_value": None,
            "mandatory": True,
            "reason": "Missing fact",
        })
    return {
        "evaluations": evals,
        "passed_rule_ids": ["Q1"] if not f_ids and not u_ids else [],
        "failed_rule_ids": f_ids,
        "unknown_rule_ids": u_ids,
        "mandatory_failed_ids": f_ids,
        "mandatory_unknown_ids": u_ids,
        "recommended_action": action,
    }


def fixture(policy_result=None):
    return {
        "project_id": "synthetic-project",
        "cutoff_datetime": "2026-10-12T12:00:00-05:00",
        "metrics": sample_metrics(),
        "draft": sample_draft(),
        "confirmed_findings": [],
        "rejected_findings": [],
        "insufficient_findings": [],
        "pending_findings": [],
        "policy_evaluations": policy_result or sample_policy_result(),
        "evidence_ids": [SHA],
    }


def gemini_response(summary="Resumen ejecutivo objetivo.", recommendation="recomendar_aprobacion",
                    recommendations=None, usage=None):
    data = {
        "summary": summary,
        "recommendation": recommendation,
        "recommendations": recommendations or ["Avanzar con el despliegue"],
    }
    body = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps(data)
                }]
            },
            "finishReason": "STOP",
        }]
    }
    if usage is not None:
        body["usageMetadata"] = usage
    return body


class ReportRunnerTests(unittest.TestCase):
    def execute(self, data=None, body=None, error=None, status=200, allow=True, key=True, model="gemini-3.5-flash-lite"):
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
        for private in ("synthetic-key", SHA, "private-marker"):
            self.assertNotIn(private, text)
        return report, calls

    def test_valid_one_post_favorable(self):
        usage = {"promptTokenCount": 600, "candidatesTokenCount": 120}
        report, calls = self.execute(body=gemini_response(usage=usage))
        self.assertEqual(report["status"], "validated")
        self.assertEqual(report["reason"], "report_generated")
        self.assertEqual(report["recommendation"], "recomendar_aprobacion")
        self.assertEqual(report["human_decision"], "pending")
        self.assertEqual(report["prompt_tokens"], 600)
        self.assertEqual(report["completion_tokens"], 120)
        self.assertEqual(len(calls), 1)
        url, kwargs = calls[0]
        self.assertIn("gemini-3.5-flash-lite", url)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertFalse(kwargs["allow_redirects"])
        payload = kwargs["json"]
        self.assertIn("contents", payload)
        self.assertIn("generationConfig", payload)
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 768)

    def test_valid_changes_requested_recommendation(self):
        pol = sample_policy_result(action="solicitar_cambios", failed_ids=["Q1"])
        data = fixture(policy_result=pol)
        body = gemini_response(recommendation="solicitar_cambios",
                               recommendations=["Corregir fallos en pruebas"])
        report, calls = self.execute(data=data, body=body)
        self.assertEqual(report["status"], "validated")
        self.assertEqual(report["recommendation"], "solicitar_cambios")
        self.assertEqual(len(calls), 1)

    def test_valid_insufficient_evidence_recommendation(self):
        pol = sample_policy_result(action="evidencia_insuficiente", unknown_ids=["Q2"])
        data = fixture(policy_result=pol)
        body = gemini_response(recommendation="evidencia_insuficiente",
                               recommendations=["Subir reporte de cobertura"])
        report, calls = self.execute(data=data, body=body)
        self.assertEqual(report["status"], "validated")
        self.assertEqual(report["recommendation"], "evidencia_insuficiente")
        self.assertEqual(len(calls), 1)

    def test_policy_violation_rejected_favorable_when_mandatory_fails(self):
        pol = sample_policy_result(action="solicitar_cambios", failed_ids=["Q1"])
        data = fixture(policy_result=pol)
        # Model unlawfully attempts favorable approval
        body = gemini_response(recommendation="recomendar_aprobacion")
        report, calls = self.execute(data=data, body=body)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "generation_rejected")
        self.assertEqual(len(calls), 1)

    def test_policy_violation_rejected_favorable_when_mandatory_unknown(self):
        pol = sample_policy_result(action="evidencia_insuficiente", unknown_ids=["Q2"])
        data = fixture(policy_result=pol)
        # Model unlawfully attempts favorable approval
        body = gemini_response(recommendation="recomendar_aprobacion")
        report, calls = self.execute(data=data, body=body)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "generation_rejected")
        self.assertEqual(len(calls), 1)

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

    def test_invalid_input_structure_rejected(self):
        invalid_data = fixture()
        del invalid_data["cutoff_datetime"]
        report, calls = self.execute(data=invalid_data)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "input_rejected")
        self.assertEqual(len(calls), 0)


if __name__ == "__main__":
    unittest.main()

