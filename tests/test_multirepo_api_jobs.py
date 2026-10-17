"""Public preparation and fingerprint contracts for MultiRepo analysis."""

import tempfile
import unittest
from pathlib import Path

from agente_gestion.api.analysis_jobs import load_analysis_plan
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.domain.repositories import RepositoryConfiguration


PROJECT_ID = "MULTI-JOBS"


class MultiRepoAnalysisPlanTests(unittest.TestCase):
    def test_sibling_repository_change_invalidates_configuration_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            config_path = root / "analysis.yaml"
            config_path.write_text(
                "project:\n"
                f"  id: {PROJECT_ID}\n"
                "  name: Synthetic MultiRepo fixture\n"
                "source_mode: fixtures\n"
                "is_fake: true\n"
                "allow_remote_ai: false\n"
                f"fixtures_dir: {fixtures}\n",
                encoding="utf-8",
            )
            backend = RepositoryConfiguration(
                id="backend", owner="example", repo="backend", branch="develop",
            )
            frontend = RepositoryConfiguration(
                id="frontend", owner="example", repo="frontend", branch="develop",
                quality_workflow_path=".github/workflows/ci.yml",
            )
            settings = ApiSettings(
                db_path=str(root / "analysis.sqlite"),
                job_ledger_path=str(root / "jobs.sqlite"),
                analysis_config_path=str(config_path),
                projects=(ProjectConfig(
                    id=PROJECT_ID, name="MultiRepo fixture", source_mode="fixtures",
                    repositories=(backend, frontend),
                ),),
                external_writes_enabled=False,
            )
            initial = load_analysis_plan(settings, PROJECT_ID)
            self.assertEqual(
                [repository.repository_id for repository in initial.preflight.repositories],
                ["backend", "frontend"],
            )
            self.assertEqual(initial.preflight.repositories[0].branch, "develop")
            self.assertEqual(initial.preflight.repositories[1].quality_workflow_path,
                             ".github/workflows/ci.yml")
            self.assertTrue(all(
                repository.commit_resolution_status == "pending"
                and repository.access_status == "unknown"
                and repository.availability_status == "unknown"
                for repository in initial.preflight.repositories
            ))
            changed_settings = settings.model_copy(update={
                "projects": (ProjectConfig(
                    id=PROJECT_ID, name="MultiRepo fixture", source_mode="fixtures",
                    repositories=(backend, frontend.model_copy(update={
                        "branch": "release",
                        "quality_workflow_path": ".github/workflows/quality.yml",
                    })),
                ),),
            })
            changed = load_analysis_plan(changed_settings, PROJECT_ID)

            self.assertNotEqual(
                initial.fingerprint, changed.fingerprint,
                "a changed sibling repository must invalidate the whole-project preparation",
            )


if __name__ == "__main__":
    unittest.main()
