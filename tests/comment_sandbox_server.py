"""Local-only real API sandbox; never loads dotenv, live adapters or remote models."""
import os
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["EXTERNAL_WRITES_ENABLED"] = "false"
os.environ["ALLOW_REMOTE_AI"] = "false"
from agente_gestion.api.app import create_app
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.api.proposal_execution import router
from tests.comment_sandbox_fixture import seed
from tests.test_comment_api import TOKEN

ROOT = Path(os.environ["COMMENT_SANDBOX_ROOT"])
assert ROOT.is_absolute() and ROOT.is_dir()
network_guard = patch("requests.sessions.Session.request", side_effect=AssertionError("External network forbidden in sandbox"))
network_guard.start()
storage, proposal, diagnostic = seed(ROOT)
settings = ApiSettings(db_path=str(ROOT / "comments.sqlite"), governance_enabled=True,
    allowed_origins=("http://127.0.0.1:4173",),
    operator_token=TOKEN, operator_actor="browser-simulated-manager", operator_project_ids=("HORMIGESTION",),
    projects=(ProjectConfig(id="HORMIGESTION", name="HORMIGESTION sandbox", source_mode="fixtures"),),
    external_writes_enabled=False)
app = create_app(settings)
app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=4178)
