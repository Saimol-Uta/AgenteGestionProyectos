"""Offline integration of the pinned, permission-gated Monitoring runner."""
import importlib.util
import io
import json
import os
from contextlib import ExitStack, redirect_stdout
from datetime import datetime as RealDatetime
from pathlib import Path
import unittest
from unittest.mock import Mock, PropertyMock, patch

from test_actions import _make_run_payload
from agente_gestion.application.validation import SemanticValidator

SPEC = importlib.util.spec_from_file_location("monitoring_runner", Path(__file__).resolve().parents[1] /
                                             "scripts/check_monitoring_gemini.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            "ALLOW_REMOTE_AI": "true", "TRELLO_API_KEY": "synthetic-key",
            "TRELLO_TOKEN": "synthetic-token", "GEMINI_API_KEY": "synthetic-gemini"}, clear=True))
        for method in ("get", "post", "put", "patch", "delete", "request"):
            self.stack.enter_context(patch("requests." + method, side_effect=AssertionError("Network blocked")))
            self.stack.enter_context(patch("requests.sessions.Session." + method,
                                          side_effect=AssertionError("Network blocked")))
        self.get = self.stack.enter_context(patch("requests.sessions.Session.get", side_effect=self.source))
        self.post = self.stack.enter_context(patch("requests.sessions.Session.post", side_effect=self.generation))
        self.clock = self.stack.enter_context(patch.object(runner, "datetime"))
        self.cutoff = RealDatetime.fromisoformat("2026-10-01T12:00:00-05:00")
        self.clock.now.return_value = self.cutoff
        self.commit = {"sha": runner.SHA}
        self.commit_status = 200
        self.total = 1
        self.finish = "STOP"
        self.data = {"summary": "No execution state asserted.", "referenced_task_ids": ["card1"],
                     "evidence_ids": ["card1", "github_actions_1001"]}
        self.context = None
        self.coordinators = []
        coordinator_type = runner.StageInvocationCoordinator
        def make_coordinator(**kwargs):
            coordinator = coordinator_type(**kwargs)
            self.coordinators.append(coordinator)
            return coordinator
        self.stack.enter_context(patch.object(runner, "StageInvocationCoordinator", side_effect=make_coordinator))
        self.card_fields = {}
        self.extra_cards = []
        self.metrics = self.stack.enter_context(patch.object(runner, "calculate_task_metrics",
                                                             wraps=runner.calculate_task_metrics))

    def response(self, body, status=200):
        response = Mock(status_code=status, headers={})
        response.iter_content.return_value = [json.dumps(body).encode()]
        response.json.return_value = body
        return response

    def source(self, url, **kwargs):
        self.assertEqual(kwargs["timeout"], 15)
        self.assertFalse(kwargs["allow_redirects"])
        if url == runner.COMMIT_URL:
            return self.response(self.commit, self.commit_status)
        if url == runner.ACTIONS_URL:
            self.assertEqual(kwargs["params"], {"head_sha": runner.SHA, "per_page": 100, "page": 1})
            return self.response({"total_count": self.total, "workflow_runs": [
                _make_run_payload(head_sha=runner.SHA)]})
        self.assertTrue(url.startswith(runner.BOARD_URL))
        if url.endswith("/lists/all"):
            return self.response([{"id": "phase", "name": "Completed Phase", "closed": False,
                                   "idBoard": runner.BOARD}])
        if url.endswith("/members"):
            return self.response([])
        self.assertEqual(url, runner.BOARD_URL + "/cards/all")
        return self.response([{"id": "card1", "name": "PRIVATE FIXTURE TITLE", "idList": "phase",
                               "idBoard": runner.BOARD, "closed": False, "labels": ["blocked"],
                               **self.card_fields}, *self.extra_cards])

    def generation(self, url, **kwargs):
        self.assertEqual(url, runner.GEMINI_URL)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "synthetic-gemini")
        body = kwargs["json"]
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 2048)
        schema = body["generationConfig"]["responseFormat"]["text"]["schema"]
        self.assertIn("TaskStateClaim", schema["$defs"])
        self.assertIn("evidence_ids", schema["required"])
        prompt = body["contents"][0]["parts"][0]["text"]
        self.assertNotIn("synthetic-", prompt)
        self.context = json.loads(prompt.split("Evidence Context:\n", 1)[1])
        return self.response({"candidates": [{"finishReason": self.finish, "content": {"parts": [
            {"text": json.dumps(self.data)}]}}], "usageMetadata": {"promptTokenCount": 321,
            "candidatesTokenCount": 41, "thoughtsTokenCount": 12, "totalTokenCount": 374}})

    def run_cli(self, flags=None):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = runner.main(flags if flags is not None else ["--due-soon-days", "7", "--allow-real"])
        output = stdout.getvalue()
        for private in ("synthetic-", "PRIVATE FIXTURE TITLE", "No execution state asserted."):
            self.assertNotIn(private, output)
        return code, json.loads(output)

    def test_permission_gates_do_not_read_credentials_or_construct_transports(self):
        for flags, remote in ((["--due-soon-days", "7"], "true"),
                              (["--due-soon-days", "7", "--allow-real"], "false")):
            with patch.dict(os.environ, {"ALLOW_REMOTE_AI": remote}), patch.object(
                    runner, "BoundedGetTransport") as constructor, patch.object(
                    runner, "OneShotGemini") as generation_constructor, patch.object(
                    os.environ, "get", wraps=os.environ.get) as lookup:
                code, metadata = self.run_cli(flags)
                self.assertNotEqual(code, 0)
                self.assertEqual(metadata["validation_errors"], [])
                self.assertTrue(all(c.args[0] == "ALLOW_REMOTE_AI" for c in lookup.call_args_list))
                constructor.assert_not_called()
                generation_constructor.assert_not_called()
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_required_nonnegative_owner_window_and_help_have_no_environment_reads(self):
        for flags in ([], ["--due-soon-days", "-1"], ["--help"]):
            with patch.object(os.environ, "get", side_effect=AssertionError("Environment read")), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(flags), 0 if flags == ["--help"] else 2)
        self.get.assert_not_called()

    def test_real_composition_same_cutoff_unknown_state_and_metadata_only(self):
        code, metadata = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(metadata["generation_status"], "validated")
        self.assertEqual(metadata["provider_status"], "complete")
        self.assertIs(metadata["validation_passed"], True)
        self.assertEqual(metadata["validation_errors"], [])
        self.clock.now.assert_called_once()
        self.assertEqual(metadata["cutoff"], self.cutoff.isoformat())
        self.assertIsNone(metadata["source_is_complete"])
        self.assertEqual(metadata["request_counts"], {"trello": 3, "github": 2, "gemini": 1})
        self.assertEqual(metadata["usage"]["thoughtsTokenCount"], 12)
        self.assertEqual(self.context["source_context"]["commit_sha"], runner.SHA)
        self.assertIsNone(self.context["source_context"]["is_complete"])
        self.assertEqual(self.context["tasks"][0]["state"], "desconocido")
        self.assertIs(self.metrics.call_args.args[1], self.cutoff)
        self.assertEqual(self.metrics.call_args.kwargs["due_soon_days"], 7)
        self.assertIn("github_actions_1001", self.context["available_evidence_ids"])
        budget = self.context["context_metadata"]
        self.assertLessEqual(budget["budget_tokens"], 4096)
        self.assertLessEqual(budget["estimated_prompt_tokens"] + 2048 + budget["safety_margin_tokens"], 8192)

    def test_commit_invalid_or_sources_partial_stop_before_generation(self):
        for status, commit, total in ((404, self.commit, 1), (200, {"sha": "a" * 40}, 1),
                                      (200, [], 1), (200, self.commit, 201), (200, self.commit, 2)):
            self.commit_status, self.commit, self.total = status, commit, total
            self.assertNotEqual(self.run_cli()[0], 0)
            self.post.assert_not_called()

    def test_incomplete_refusal_invalid_refs_never_repair(self):
        for finish, data in (("MAX_TOKENS", self.data), ("SAFETY", self.data),
                             ("STOP", {"summary": "Missing references"}),
                             ("STOP", {"summary": "Unknown", "referenced_task_ids": ["unknown"],
                                       "evidence_ids": ["unknown"]})):
            self.finish, self.data = finish, data
            self.post.reset_mock()
            code, metadata = self.run_cli()
            self.assertNotEqual(code, 0)
            self.assertEqual(self.post.call_count, 1)
            self.assertEqual(metadata["request_counts"]["gemini"], 1)

    def test_validation_diagnostics_are_specific_and_private_values_never_escape(self):
        private = "synthetic-gemini PRIVATE FIXTURE TITLE https://private.invalid/secret"
        cases = [
            ({"summary": private}, [("missing_required_fields", ["referenced_task_ids", "evidence_ids"])]),
            ({"summary": private, "referenced_task_ids": private, "evidence_ids": []},
             [("invalid_schema", ["referenced_task_ids"])]),
            ({"summary": private, "referenced_task_ids": [private], "evidence_ids": [private]},
             [("missing_task_grounding", ["referenced_task_ids", "evidence_ids"]),
              ("unknown_task_reference", ["referenced_task_ids"]),
              ("unknown_evidence_reference", ["evidence_ids"])]),
            ({"summary": private, "referenced_task_ids": [], "evidence_ids": [],
              "task_state_claims": [{"task_id": "card1", "state": "completada"}]},
             [("task_state_mismatch", ["task_state_claims"]),
              ("missing_task_grounding", ["referenced_task_ids", "evidence_ids"])]),
        ]
        for data, expected in cases:
            with self.subTest(expected=expected):
                self.data = data
                self.post.reset_mock()
                code, metadata = self.run_cli()
                self.assertNotEqual(code, 0)
                self.assertEqual(metadata["provider_status"], "complete")
                self.assertEqual(metadata["generation_status"], "validation_failed")
                self.assertIs(metadata["validation_passed"], False)
                self.assertEqual([(d["code"], d["fields"]) for d in metadata["validation_errors"]], expected)
                attempt = self.coordinators[-1].recorded_attempts[-1]
                self.assertEqual(len(metadata["validation_errors"]), len(attempt.validation_errors))
                self.assertEqual(self.post.call_count, 1)
                self.assertEqual(metadata["retries"], 0)
                self.assertEqual(metadata["repairs"], 0)
                for value in (private, "https://private.invalid", "card1", "input_value", "Evidence Context:"):
                    self.assertNotIn(value, json.dumps(metadata))
        with patch.object(SemanticValidator, "validate_monitoring_draft", return_value=[private, private]):
            self.data = {"summary": private, "referenced_task_ids": [], "evidence_ids": []}
            self.post.reset_mock()
            _, metadata = self.run_cli()
            self.assertEqual([d["code"] for d in metadata["validation_errors"]],
                             ["unclassified_validation_error"] * 2)
            self.assertTrue(all(d["fields"] == [] for d in metadata["validation_errors"]))
            self.assertNotIn("https://private.invalid", json.dumps(metadata))
            self.assertEqual(self.post.call_count, 1)

    def test_reachable_semantic_templates_are_static_and_full_match_only(self):
        narrative_fields = ["summary", "risks", "suggested_actions", "ci_status_note"]
        for private in ("T-938a", "quote'id", "line\nbreak", "https://private.invalid/task?token=synthetic-key"):
            templates = [
                (f"Duplicate referenced task ID in draft: '{private}'.", "duplicate_task_reference",
                 ["referenced_task_ids"], "Task reference occurs more than once."),
                (f"Duplicate referenced evidence ID in draft: '{private}'.", "duplicate_evidence_reference",
                 ["evidence_ids"], "Evidence reference occurs more than once."),
                ("Insufficient task-status attribution after multiple task mentions; cite the exact task ID or request additional context.",
                 "ambiguous_task_status_attribution", narrative_fields, "Narrative task status requires explicit attribution."),
                (f"Narrative task '{private}' state 'completed' contradicts deterministic 'desconocido'.",
                 "narrative_task_state_mismatch", narrative_fields, "Narrative task state contradicts deterministic state."),
                ("Narrative references an omitted task outside selected evidence; request additional context.",
                 "omitted_task_reference", narrative_fields, "Narrative refers to a task outside selected evidence."),
            ]
            for text, code, fields, message in templates:
                with self.subTest(code=code, private=private):
                    self.assertEqual(runner.validation_diagnostics([text]),
                                     [{"code": code, "fields": fields, "message": message}])
                    unknown = ["prefix " + text, text + " suffix", text[:-1], text.lower()]
                    self.assertEqual(runner.validation_diagnostics(unknown), [{
                        "code": "unclassified_validation_error", "fields": [],
                        "message": "Unclassified validator rejection."}] * len(unknown))

    def test_mixed_diagnostics_keep_order_duplicates_and_private_fallback(self):
        known = "Duplicate referenced task ID in draft: 'https://private.invalid/token'."
        errors = [known, None, known, 42, {"private": "synthetic-key"},
                  "Narrative task 'private' state 'forged' contradicts deterministic 'forged'.",
                  "Draft claimed 99 completed cards does not match actual metric 1.",
                  "Referenced task ID 'external-938a' does not exist in available task context.",
                  "Referenced evidence ID 'external-938a' does not exist in supplied context.",
                  "Task state claim 'external-938a' contradicts deterministic TaskCard.state.",
                  "Mentioned task 'external-938a' must occur in referenced_task_ids and evidence_ids."]
        diagnostics = runner.validation_diagnostics(errors)
        self.assertEqual([d["code"] for d in diagnostics], [
            "duplicate_task_reference", "unclassified_validation_error", "duplicate_task_reference",
            *(["unclassified_validation_error"] * 4), "unknown_task_reference",
            "unknown_evidence_reference", "task_state_mismatch", "missing_task_grounding"])
        self.assertEqual(len(diagnostics), len(errors))
        self.assertEqual(diagnostics[0], diagnostics[2])
        for value in ("private", "synthetic-key", "external-938a", "99", "forged"):
            self.assertNotIn(value, json.dumps(diagnostics))

    def test_new_mappings_from_actual_validator_and_last_attempt(self):
        self.extra_cards = [{"id": "card2", "name": "SECOND PRIVATE TITLE", "idList": "phase",
                             "idBoard": runner.BOARD, "closed": False, "labels": []}]
        narrative_fields = ["summary", "risks", "suggested_actions", "ci_status_note"]
        base = {"summary": "No execution state asserted.", "referenced_task_ids": ["card1", "card2"],
                "evidence_ids": ["card1", "card2"]}
        cases = [
            ({**base, "referenced_task_ids": ["card1", "card1", "card2"]},
             "duplicate_task_reference", ["referenced_task_ids"]),
            ({**base, "evidence_ids": ["card1", "card2", "card2"]},
             "duplicate_evidence_reference", ["evidence_ids"]),
        ]
        for field in narrative_fields:
            for narrative, code in (("card1 and card2. Completed.", "ambiguous_task_status_attribution"),
                                    ("card1 completed", "narrative_task_state_mismatch")):
                value = [narrative] if field in ("risks", "suggested_actions") else narrative
                cases.append(({**base, field: value}, code, narrative_fields))
        for data, expected_code, fields in cases:
            with self.subTest(code=expected_code, fields=list(data)):
                self.data = data
                self.post.reset_mock()
                code, metadata = self.run_cli()
                self.assertEqual(code, 1)
                attempt = self.coordinators[-1].recorded_attempts[-1]
                self.assertEqual(len(attempt.validation_errors), 1)
                self.assertEqual(metadata["validation_errors"], runner.validation_diagnostics(attempt.validation_errors))
                self.assertEqual(metadata["validation_errors"][0]["code"], expected_code)
                self.assertEqual(metadata["validation_errors"][0]["fields"], fields)
                self.assertEqual(metadata["generation_status"], "validation_failed")
                self.assertIs(metadata["validation_passed"], False)
                self.assertEqual(metadata["provider_status"], "complete")
                self.assertEqual(metadata["request_counts"]["gemini"], 1)
                self.assertEqual(self.post.call_count, 1)
                self.assertEqual((metadata["retries"], metadata["repairs"]), (0, 0))
                for value in ("card1", "card2", "SECOND PRIVATE TITLE", "completed"):
                    self.assertNotIn(value, json.dumps(metadata))

    def test_omitted_task_mapping_from_actual_agent_validator(self):
        runtime_payload = runner.ContextBuilder.runtime_payload
        def omit(builder, *args, **kwargs):
            payload = runtime_payload(builder, *args, **kwargs)
            payload["tasks"] = []
            return payload
        for field in ("summary", "risks", "suggested_actions", "ci_status_note"):
            self.data = {"summary": "No execution state asserted.", "referenced_task_ids": [], "evidence_ids": [],
                         field: ["card1"] if field in ("risks", "suggested_actions") else "card1"}
            self.post.reset_mock()
            with patch.object(runner.ContextBuilder, "runtime_payload", omit):
                code, metadata = self.run_cli()
            attempt = self.coordinators[-1].recorded_attempts[-1]
            self.assertEqual(code, 1)
            self.assertEqual(len(attempt.validation_errors), 1)
            self.assertEqual(metadata["validation_errors"], runner.validation_diagnostics(attempt.validation_errors))
            self.assertEqual(metadata["validation_errors"][0]["code"], "omitted_task_reference")
            self.assertEqual(metadata["validation_errors"][0]["fields"],
                             ["summary", "risks", "suggested_actions", "ci_status_note"])
            self.assertEqual(metadata["generation_status"], "validation_failed")
            self.assertIs(metadata["validation_passed"], False)
            self.assertEqual(self.post.call_count, 1)
            self.assertEqual((metadata["retries"], metadata["repairs"]), (0, 0))
            self.assertNotIn("card1", json.dumps(metadata))

    def test_nonvalidation_failures_have_no_semantic_diagnostics(self):
        for finish, status in (("MAX_TOKENS", "incomplete"), ("SAFETY", "refused")):
            self.finish = finish
            self.post.reset_mock()
            _, metadata = self.run_cli()
            self.assertEqual(metadata["provider_status"], status)
            self.assertEqual(metadata["generation_status"], status)
            self.assertEqual(metadata["validation_errors"], [])
            self.assertIs(metadata["validation_passed"], False)
            self.assertEqual(self.post.call_count, 1)
        for failure in (RuntimeError("synthetic-gemini"), self.response({"error": {
                "message": "synthetic-gemini", "status": "RESOURCE_EXHAUSTED"}}, 429)):
            self.post.reset_mock()
            self.post.side_effect = failure if isinstance(failure, Exception) else None
            self.post.return_value = failure
            _, metadata = self.run_cli()
            self.assertEqual(metadata["validation_errors"], [])
            self.assertEqual(metadata["generation_status"], metadata["provider_status"])
            self.assertEqual(self.post.call_count, 1)

    def test_date_fields_use_cutoff_not_phase_names_or_blocked_labels(self):
        for start, state in (("2026-10-01T16:59:00+00:00", "en_curso"),
                             ("2026-10-01T17:01:00+00:00", "pending")):
            self.card_fields = {"start": start, "dueComplete": False,
                                "due": "2026-10-03T12:00:00-05:00"}
            self.assertEqual(self.run_cli()[0], 0)
            self.assertEqual(self.context["tasks"][0]["state"], state)
            self.assertEqual(self.context["metrics"]["due_soon_card_ids"], ["card1"])

    def test_collection_flag_fails_closed_and_endpoint_budgets(self):
        with patch.object(runner.GitHubActionsSourceAdapter, "last_collection_complete",
                          new_callable=PropertyMock, return_value=False):
            self.assertNotEqual(self.run_cli()[0], 0)
            self.post.assert_not_called()
        transport = runner.ScopedGet(runner.SourceProvider.trello,
                                    {runner.BOARD_URL + "/cards/all"}, 10)
        before = self.get.call_count
        with self.assertRaises(ValueError):
            transport.get("https://api.trello.com/1/boards/other/cards/all")
        self.assertEqual(self.get.call_count, before)
        for _ in range(10):
            transport.get(runner.BOARD_URL + "/cards/all")
        with self.assertRaises(ValueError):
            transport.get(runner.BOARD_URL + "/cards/all")
        self.assertEqual(self.get.call_count, before + 10)
        callback = runner.OneShotGemini({})
        with self.assertRaises(RuntimeError):
            callback("https://example.com", {})
        self.assertEqual(callback.count, 0)
        github = runner.ScopedGet(runner.SourceProvider.github_actions, {runner.COMMIT_URL}, 3)
        for _ in range(3):
            github.get(runner.COMMIT_URL)
        with self.assertRaises(ValueError):
            github.get(runner.COMMIT_URL)
        self.assertEqual(github.count, 3)

    def test_typeerror_after_post_does_not_repeat_http(self):
        self.post.side_effect = TypeError("PRIVATE FIXTURE TITLE")
        code, metadata = self.run_cli()
        self.assertNotEqual(code, 0)
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(metadata["request_counts"]["gemini"], 1)
