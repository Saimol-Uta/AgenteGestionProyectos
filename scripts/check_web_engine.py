#!/usr/bin/env python3
"""Verify the default web executor against the real engine using isolated fake ports."""

import argparse
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=(
        "Verifica API → executor predeterminado → motor real con fuentes/LLM simulados, "
        "base temporal y transporte HTTP bloqueado. No usa datos auténticos ni modelos reales."
    ))
    parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient
    from agente_gestion.api.app import create_app
    from scripts.serve_web_demo import FIXTURE_OPERATOR_TOKEN, demo_settings

    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("http_transport")
        raise AssertionError("Network transport forbidden in offline engine verification")

    with demo_settings("fixture") as settings:
        app = create_app(settings)  # Deliberately no injected executor or plan loader.
        with (patch("requests.sessions.Session.request", side_effect=forbidden),
              patch("httpx.HTTPTransport.handle_request", side_effect=forbidden),
              patch("httpx.AsyncHTTPTransport.handle_async_request", side_effect=forbidden),
              TestClient(app, base_url="http://localhost:8000") as client):
            auth = client.post("/api/v1/auth/login", headers={"Origin": "http://localhost:8000"},
                               json={"token": FIXTURE_OPERATOR_TOKEN})
            assert auth.status_code == 200, auth.text
            headers = {"Origin": "http://localhost:8000", "X-CSRF-Token": auth.json()["csrf_token"]}
            prepared = client.post("/api/v1/projects/H5-TEST/analysis/prepare", headers=headers,
                                   json={"request_analysis": True})
            assert prepared.status_code == 200, prepared.text
            plan = prepared.json()
            assert plan["eligible"] and not plan["remote"]["required"], plan
            # server_allowed is vacuously true when no remote AI is required.
            assert plan["preflight"]["source_mode"] == "fixtures", plan
            assert plan["remote"]["providers"] and all(
                provider["provider"] == "fake_simulated" for provider in plan["remote"]["providers"]
            ), plan
            accepted = client.post("/api/v1/projects/H5-TEST/analysis/confirm", headers={
                **headers, "Idempotency-Key": "offline-default-executor-001",
            }, json={"preparation_id": plan["preparation_id"],
                     "configuration_fingerprint": plan["configuration_fingerprint"],
                     "confirm_analysis": True, "remote_consent": False})
            assert accepted.status_code == 202, accepted.text
            deadline = time.monotonic() + 20
            while True:
                response = client.get(accepted.json()["poll_url"])
                assert response.status_code == 200, response.text
                job = response.json()
                if job["status"] in {"completed", "failed", "interrupted"}:
                    break
                assert time.monotonic() < deadline, "Offline engine job timed out"
                time.sleep(0.05)
            assert job["status"] == "completed", job
            run_id = job["run_id"]
            assert run_id and run_id != "run-current", job
            stages = client.get(f"/api/v1/runs/{run_id}/stages")
            assert stages.status_code == 200, stages.text
            assert any(stage["recorded"] for stage in stages.json()["items"]), stages.text
            reports = client.get("/api/v1/projects/H5-TEST/reports")
            assert reports.status_code == 200, reports.text
            report = next((row for row in reports.json()["items"] if row["run_id"] == run_id), None)
            assert report is not None, reports.text
            assert not calls, calls
            proof = {"mode": "actual_default_executor_actual_engine_fake_ports",
                     "job": job, "stages": stages.json(), "report_id": report["report_id"],
                     "remote_calls": 0, "authentic_data_used": False}
            target = ROOT / ".runtime" / "h5-offline-engine.json"
            target.write_text(json.dumps(proof, indent=2), encoding="utf-8")
            print(f"OFFLINE_REAL_ENGINE_OK: {run_id}, {report['report_id']}; remote_calls=0")
            print("Motor real, puertos simulados, base/artefactos temporales; sin executor inyectado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
