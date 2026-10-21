"""Real FastAPI + SQLite + existing pipeline and append sandbox (no provider calls)."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from agente_gestion.api.app import create_app
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.api.proposal_execution import router
from tests.comment_sandbox_fixture import seed

TOKEN = "comment-sandbox-operator-token-long-enough"


class CommentApiTests(unittest.TestCase):
    def test_full_api_pipeline_and_receipt_reload(self):
        with tempfile.TemporaryDirectory() as root, patch("requests.sessions.Session.request", side_effect=AssertionError("No external calls")):
            storage, proposal, diagnostic = seed(root)
            settings = ApiSettings(db_path=str(Path(root) / "comments.sqlite"), governance_enabled=True,
                operator_token=TOKEN, operator_actor="simulated-manager", operator_project_ids=("HORMIGESTION",),
                projects=(ProjectConfig(id="HORMIGESTION", name="HORMIGESTION sandbox", source_mode="fixtures"),),
                external_writes_enabled=False)
            app = create_app(settings); app.include_router(router)
            with TestClient(app, base_url="http://127.0.0.1") as client:
                login = client.post("/api/v1/auth/login", json={"token": TOKEN}, headers={"Origin": "http://127.0.0.1:5173"})
                self.assertEqual(login.status_code, 200, login.text)
                url = f"/api/v1/projects/HORMIGESTION/proposals/{proposal.proposal_id}"
                detail = client.get(url).json()
                self.assertEqual(detail["operation"], "append_comment")
                self.assertEqual(detail["technical_review"]["technical_status"], "ready")
                self.assertIn("human_confirmation", [e["category"] for e in detail["evidence"]])
                headers = {"Origin": "http://127.0.0.1:5173", "X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": "comment-api-one"}
                response = client.post(url + "/execute", json={"decision": "approve_and_execute", "reason": "Exact comment reviewed"}, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["execution"]["status"], "SIMULATED")
                after = client.get(url).json()
                self.assertTrue(after["latest_receipt"]["native_action_id"].startswith("sandbox-comment-"))
                self.assertEqual(after["latest_receipt"]["execution_mode"], "sandbox")
                self.assertTrue(after["decision_history"])
                replay = client.post(url + "/execute", json={"decision": "approve_and_execute", "reason": "Exact comment reviewed"}, headers=headers)
                self.assertTrue(replay.json()["replayed"])


if __name__ == "__main__":
    unittest.main()
