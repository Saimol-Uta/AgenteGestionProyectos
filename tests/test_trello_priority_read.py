"""Configured label observations use the same map as corrective actions."""
import unittest
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from agente_gestion.adapters.sources.factory import (
    create_task_source, _task_source_identity, _task_source_identity_from_settings,
)
from agente_gestion.adapters.sources.http_transport import HttpResponse
from agente_gestion.adapters.sources.trello import TrelloSourceAdapter
from agente_gestion.application.config import Settings
from agente_gestion.api.analysis_jobs import load_analysis_plan
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.ports.source_errors import SourceError
from tests.test_trello import FakeTransport


LABELS = {"alta": "6a957e9173d4d13d0c5db616", "media": "6a957e9173d4d13d0c5db615",
          "baja": "6a957e9173d4d13d0c5db614"}


class PriorityReadTest(unittest.TestCase):
    def adapter(self, cards, **kwargs):
        transport = FakeTransport({
            "https://api.trello.com/1/boards/board/lists/all": HttpResponse(200, [
                {"id": "todo", "idBoard": "board", "name": "Todo", "closed": False}]),
            "https://api.trello.com/1/boards/board/cards/all": HttpResponse(200, cards),
        })
        return TrelloSourceAdapter("board", "sentinel_key", "sentinel_token", transport=transport, **kwargs)

    def card(self, **kwargs):
        return {"id": "card", "idBoard": "board", "idList": "todo", "name": "Task",
                "closed": False, **kwargs}

    def test_official_mapping_and_absent_milestone_priority(self):
        for expected, ids in [(name, [label]) for name, label in LABELS.items()] + [(None, [])]:
            with self.subTest(expected=expected):
                adapter = self.adapter([self.card(labels=[{"id": i, "name": "not a priority name"} for i in ids])],
                                       priority_labels=LABELS)
                task = adapter.get_tasks("HORMIGESTION")[0]
                payload = adapter.get_evidence("HORMIGESTION")[0].provenance.payload
                self.assertEqual(task.priority, expected)
                self.assertIn("priority", task.observed_fields)
                self.assertEqual(payload["priority"], expected)
                self.assertEqual(payload["priority_label_ids"], ids)

    def test_no_map_keeps_legacy_explicit_priority_and_no_name_inference(self):
        adapter = self.adapter([self.card(labels=[{"name": "alta"}], priority="urgent")])
        self.assertEqual(adapter.get_tasks("project")[0].priority, "urgent")
        adapter = self.adapter([self.card(labels=[{"name": "alta"}])])
        self.assertIsNone(adapter.get_tasks("project")[0].priority)
        self.assertNotIn("priority", adapter.get_tasks("project")[0].observed_fields)

    def test_missing_ids_are_unobserved_blocked_is_not_priority(self):
        for fields, observed in [({}, False), ({"labels": [{"name": "alta"}]}, False),
                                 ({"idLabels": ["6a957e9173d4d13d0c5db617"]}, True)]:
            adapter = self.adapter([self.card(**fields)], priority_labels=LABELS)
            task = adapter.get_tasks("project")[0]
            self.assertIsNone(task.priority)
            self.assertEqual("priority" in task.observed_fields, observed)

    def test_conflicting_labels_and_malformed_ids_refuse_atomic_collection(self):
        for fields in ({"idLabels": list(LABELS.values())[:2]}, {"idLabels": [1]},
                       {"idLabels": ["sentinel_key"]}, {"idLabels": None},
                       {"labels": [], "idLabels": [LABELS["alta"]]},
                       {"idLabels": [LABELS["alta"]], "priority": "baja"},
                       {"labels": [{"id": 1, "name": "alta"}]}):
            with self.subTest(fields=fields), self.assertRaises(SourceError):
                self.adapter([self.card(**fields)], priority_labels=LABELS).get_tasks("project")

    def test_factory_wiring_and_scope_fingerprint(self):
        with patch.dict("os.environ", {}, clear=True):
            base = dict(source_mode="real", project_id="HORMIGESTION", trello_board_id="board",
                        trello_api_key="sentinel_key", trello_token="sentinel_token", _env_file=None)
            plain = create_task_source(Settings(**base))
            configured = create_task_source(Settings(**base, trello_priority_labels={"HORMIGESTION": LABELS}))
            self.assertNotEqual(_task_source_identity(plain), _task_source_identity(configured))
            self.assertEqual(configured.priority_labels, LABELS)
            self.assertEqual(_task_source_identity(configured), _task_source_identity_from_settings(
                Settings(**base, trello_priority_labels={"HORMIGESTION": LABELS})))
            other = create_task_source(Settings(**base, trello_priority_labels={"OTHER": LABELS}))
            self.assertEqual(other.priority_labels, {})
            self.assertEqual(_task_source_identity(other), _task_source_identity(plain))

    def test_invalid_mapping_fails_before_fetching(self):
        for broken in ({"alta": LABELS["alta"]}, {"alta": "same", "media": "same", "baja": "low"}):
            with self.subTest(broken=broken), self.assertRaises(SourceError):
                self.adapter([], priority_labels=broken)

    def test_api_and_engine_share_environment_map_without_persisting_config(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            config = Path(root) / "config.yaml"
            config.write_text("project_id: HORMIGESTION\nsource_mode: fixtures\n")
            api = ApiSettings(analysis_config_path=str(config), analysis_env_file=None,
                              trello_priority_labels={"HORMIGESTION": LABELS},
                              projects=(ProjectConfig(id="HORMIGESTION", name="P", source_mode="fixtures"),))
            self.assertEqual(load_analysis_plan(api, "HORMIGESTION").engine_settings.trello_priority_labels,
                             {"HORMIGESTION": LABELS})
            with patch.dict(os.environ, {"TRELLO_PRIORITY_LABELS": json.dumps({"HORMIGESTION": LABELS})}):
                self.assertEqual(Settings(_env_file=None).trello_priority_labels, {"HORMIGESTION": LABELS})


if __name__ == "__main__":
    unittest.main()
