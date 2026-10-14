#!/usr/bin/env python3
"""Verify authentic historical API consultation without remote calls or historical writes."""

import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def historical_fingerprints() -> dict[str, str]:
    """Fingerprint only known in-project historical artifacts; never follow outside symlinks."""
    files = [ROOT / ".runtime" / "agente.db"]
    for name in ("agente.db-wal", "agente.db-journal"):
        candidate = ROOT / ".runtime" / name
        if candidate.exists():
            files.append(candidate)
    artifact_names = (
        "snapshot.json", "metrics.json", "monitoring.json", "code_findings.json",
        "verification.json", "report.json", "report.md", "execution_metrics.json",
    )
    run_root = ROOT / "runs"
    if run_root.is_dir():
        for run in run_root.iterdir():
            if not run.is_dir() or not run.name.startswith("run-"):
                continue
            for name in artifact_names:
                candidate = run / name
                if candidate.is_file():
                    files.append(candidate)
    result = {}
    for path in files:
        resolved = path.resolve()
        if not resolved.is_relative_to(ROOT):
            raise RuntimeError("Historical artifact points outside the project.")
        result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main() -> int:
    from fastapi.testclient import TestClient
    from agente_gestion.api.app import create_app
    from agente_gestion.api.settings import ApiSettings

    database = ROOT / ".runtime" / "agente.db"
    if not database.is_file():
        raise RuntimeError("Authentic historical database is absent; no fixture is substituted.")
    before = historical_fingerprints()
    # Discover stored projects only; never synthesize a configured project for the proof.
    app = create_app(ApiSettings(db_path=str(database), runs_dir=str(ROOT / "runs")))
    proof = {"dataset": "authentic historical HORMIGESTION", "checks": [], "limitations": []}
    with patch("requests.sessions.Session.request", side_effect=AssertionError("Remote HTTP forbidden")) as remote:
        with TestClient(app, base_url="http://localhost") as client:
            captures = []
            paths = (
                "/system/health", "/projects", "/projects/HORMIGESTION/summary",
                "/projects/HORMIGESTION/tasks?critical_only=true&page_size=3",
                "/projects/HORMIGESTION/history",
            )
            for route in paths:
                response = client.get("/api/v1" + route)
                if response.status_code != 200:
                    raise AssertionError(f"Historical query failed: {route}: {response.status_code}")
                body = response.json()
                row = {"route": route, "status": response.status_code}
                if route.endswith("/summary"):
                    row["metrics"] = {key: body[key] for key in (
                        "total_tasks", "included_tasks", "completed_tasks", "progress_percentage",
                        "state_counts", "overdue_count", "due_soon_count", "unknown_count",
                    )}
                elif "items" in body:
                    row["total"] = body["total"]
                if route.endswith("/history"):
                    captures = body["items"]
                proof["checks"].append(row)
            if len(captures) >= 2:
                response = client.get("/api/v1/projects/HORMIGESTION/compare", params={
                    "before": captures[-1]["capture_id"], "after": captures[0]["capture_id"],
                })
                body = response.json()
                if response.status_code == 200:
                    changes = body.get("changes", [])
                    assert all(change.get("actor") is None for change in changes)
                    proof["checks"].append({"route": "historical comparison", "status": 200, "changes": len(changes)})
                elif (response.status_code == 409 and
                      body.get("error", {}).get("code") == "comparacion_no_disponible"):
                    proof["limitations"].append({
                        "route": "oldest-to-newest historical comparison", "status": 409,
                        "reason": "Domain refused comparison with unavailable/incompatible evidence; no diff synthesized.",
                    })
                else:
                    raise AssertionError(f"Unexpected historical comparison response: {response.status_code}")
        proof["remote_http_calls"] = remote.call_count
        assert remote.call_count == 0
    assert historical_fingerprints() == before, "Historical database/artifacts changed"
    proof["historical_files_unchanged"] = len(before)
    output = ROOT / ".runtime" / "h5-authentic-readonly.json"
    output.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(proof, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
