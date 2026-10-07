"""Tests for Trello read-only source adapter (T2b)."""

from datetime import datetime, timezone
import traceback
import unittest
from typing import Any
from zoneinfo import ZoneInfo

from agente_gestion.adapters.sources.trello import TrelloSourceAdapter
from agente_gestion.adapters.sources.http_transport import (
    HttpGetTransportPort,
    HttpResponse,
)
from agente_gestion.domain.evidence import EvidenceItem, EvidenceSource
from agente_gestion.domain.tasks import (
    TaskCard,
    TaskMappingConfig,
    TaskState,
)
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.ports.sources import TaskSourcePort


class FakeTransport(HttpGetTransportPort):
    """Deterministic fake HTTP GET transport capturing requests and routing responses."""

    def __init__(
        self,
        responses: dict[str, HttpResponse | Exception] | None = None,
    ):
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpResponse:
        self.calls.append({
            "url": url,
            "params": params,
            "headers": headers,
            "timeout_seconds": timeout_seconds,
        })
        resp = self.responses.get(url)
        if isinstance(resp, Exception):
            raise resp
        if resp is not None:
            return resp
        return HttpResponse(status_code=200, body=[])


class TestTrelloSourceAdapter(unittest.TestCase):
    """Unit tests for TrelloSourceAdapter contract, mapping, validation, caching, and security."""

    def setUp(self):
        self.board_id = "board123"
        self.api_key = "sentinel_api_key_xyz"
        self.token = "sentinel_token_abc"
        self.lists_url = f"https://api.trello.com/1/boards/{self.board_id}/lists/all"
        self.cards_url = f"https://api.trello.com/1/boards/{self.board_id}/cards/all"
        self.tz = ZoneInfo("America/Guayaquil")

    def _sample_lists(self) -> list[dict[str, Any]]:
        return [
            {"id": "list_todo", "name": "To Do", "closed": False, "idBoard": self.board_id},
            {"id": "list_doing", "name": "Doing", "closed": False, "idBoard": self.board_id},
            {"id": "list_done", "name": "Done", "closed": False, "idBoard": self.board_id},
            {"id": "list_archived", "name": "Old Closed List", "closed": True, "idBoard": self.board_id},
        ]

    def _sample_cards(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "card_1",
                "name": "Task 1",
                "desc": "Detail for task 1",
                "idList": "list_todo",
                "idBoard": self.board_id,
                "closed": False,
                "due": "2026-10-05T12:00:00.000Z",
                "dateLastActivity": "2026-10-05T11:00:00.000Z",
                "labels": [{"id": "lbl_1", "name": "frontend"}],
                "idMembers": ["mem_1"],
            },
            {
                "id": "card_2",
                "name": "Task 2",
                "desc": "Doing work",
                "idList": "list_doing",
                "idBoard": self.board_id,
                "closed": False,
                "due": None,
                "labels": [],
                "idMembers": [],
            },
            {
                "id": "card_3",
                "name": "Task 3",
                "desc": "Finished work",
                "idList": "list_done",
                "idBoard": self.board_id,
                "closed": False,
                "due": "2026-10-01T15:30:00+00:00",
                "labels": [],
                "idMembers": ["mem_2"],
            },
        ]

    def _make_transport(
        self,
        lists: list[dict[str, Any]] | Exception | None = None,
        cards: list[dict[str, Any]] | Exception | None = None,
    ) -> FakeTransport:
        lists_val = lists if lists is not None else self._sample_lists()
        cards_val = cards if cards is not None else self._sample_cards()

        responses: dict[str, HttpResponse | Exception] = {}
        if isinstance(lists_val, Exception):
            responses[self.lists_url] = lists_val
        else:
            responses[self.lists_url] = HttpResponse(status_code=200, body=lists_val)

        if isinstance(cards_val, Exception):
            responses[self.cards_url] = cards_val
        else:
            responses[self.cards_url] = HttpResponse(status_code=200, body=cards_val)

        return FakeTransport(responses)

    def _make_adapter(
        self,
        transport: FakeTransport | None = None,
        mapping: TaskMappingConfig | None = None,
        **kwargs: Any,
    ) -> TrelloSourceAdapter:
        t = transport or self._make_transport()
        m = mapping or TaskMappingConfig(
            pending_list_ids=["list_todo"],
            in_progress_list_ids=["list_doing"],
            completed_list_ids=["list_done"],
            blocked_labels=["bloqueada", "blocked"],
        )
        return TrelloSourceAdapter(
            board_id=self.board_id,
            api_key=self.api_key,
            token=self.token,
            mapping_config=m,
            transport=t,
            **kwargs,
        )

    def test_implements_task_source_port(self):
        """Adapter satisfies TaskSourcePort abstract base class."""
        adapter = self._make_adapter()
        self.assertIsInstance(adapter, TaskSourcePort)
        self.assertTrue(callable(getattr(adapter, "get_tasks", None)))
        self.assertTrue(callable(getattr(adapter, "get_evidence", None)))
        self.assertEqual(adapter.board_id, self.board_id)

    def test_list_state_mapping(self):
        """Cards map to pending, in_progress, completed, or unknown states."""
        cards = self._sample_cards()
        cards.append({
            "id": "card_unmapped",
            "name": "Unmapped card",
            "idList": "list_archived",
            "idBoard": self.board_id,
            "closed": False,
            "due": None,
            "labels": [],
        })
        transport = self._make_transport(cards=cards)
        adapter = self._make_adapter(transport=transport)

        tasks = adapter.get_tasks("test_proj")
        task_map = {t.id: t for t in tasks}

        self.assertEqual(task_map["card_1"].state, TaskState.pending)
        self.assertEqual(task_map["card_1"].list_name, "To Do")
        self.assertEqual(task_map["card_2"].state, TaskState.in_progress)
        self.assertEqual(task_map["card_2"].list_name, "Doing")
        self.assertEqual(task_map["card_3"].state, TaskState.completed)
        self.assertEqual(task_map["card_3"].list_name, "Done")
        self.assertEqual(task_map["card_unmapped"].state, TaskState.unknown)

    def test_blocked_label_and_completed_contradiction(self):
        """Blocked label sets state to blocked for in_progress/pending, but completed list creates contradiction."""
        cards = [
            {
                "id": "c_blocked_pending",
                "name": "Blocked in todo",
                "idList": "list_todo",
                "idBoard": self.board_id,
                "closed": False,
                "due": None,
                "labels": [{"name": "blocked"}],
            },
            {
                "id": "c_blocked_doing",
                "name": "Blocked in doing",
                "idList": "list_doing",
                "idBoard": self.board_id,
                "closed": False,
                "due": None,
                "labels": [{"name": "bloqueada"}],
            },
            {
                "id": "c_contra_done",
                "name": "Completed but marked blocked",
                "idList": "list_done",
                "idBoard": self.board_id,
                "closed": False,
                "due": None,
                "labels": [{"name": "blocked"}],
            },
            {
                "id": "c_normal_done",
                "name": "Completed normal",
                "idList": "list_done",
                "idBoard": self.board_id,
                "closed": False,
                "due": None,
                "labels": [{"name": "frontend"}],
            },
        ]
        transport = self._make_transport(cards=cards)
        adapter = self._make_adapter(transport=transport)

        tasks = adapter.get_tasks("test_proj")
        t_map = {t.id: t for t in tasks}

        self.assertEqual(t_map["c_blocked_pending"].state, TaskState.blocked)
        self.assertFalse(t_map["c_blocked_pending"].has_contradiction)

        self.assertEqual(t_map["c_blocked_doing"].state, TaskState.blocked)
        self.assertFalse(t_map["c_blocked_doing"].has_contradiction)

        # Completed list + blocked label => contradiction
        self.assertEqual(t_map["c_contra_done"].state, TaskState.completed)
        self.assertTrue(t_map["c_contra_done"].has_contradiction)

        self.assertEqual(t_map["c_normal_done"].state, TaskState.completed)
        self.assertFalse(t_map["c_normal_done"].has_contradiction)

    def test_included_lists_filtering(self):
        """When included_list_ids is configured, cards outside those lists have is_included=False."""
        mapping = TaskMappingConfig(
            pending_list_ids=["list_todo"],
            in_progress_list_ids=["list_doing"],
            completed_list_ids=["list_done"],
            included_list_ids=["list_todo", "list_doing"],
        )
        adapter = self._make_adapter(mapping=mapping)
        tasks = adapter.get_tasks("test_proj")
        t_map = {t.id: t for t in tasks}

        self.assertTrue(t_map["card_1"].is_included)
        self.assertTrue(t_map["card_2"].is_included)
        self.assertFalse(t_map["card_3"].is_included)

    def test_closed_card_and_closed_list_exclusion(self):
        """Closed cards and cards in closed lists are excluded from metrics (is_included=False) but retained as evidence."""
        cards = [
            {
                "id": "c_open",
                "name": "Open card",
                "idList": "list_todo",
                "idBoard": self.board_id,
                "closed": False,
                "due": None,
                "labels": [],
            },
            {
                "id": "c_closed_card",
                "name": "Archived card",
                "idList": "list_todo",
                "idBoard": self.board_id,
                "closed": True,
                "due": None,
                "labels": [],
            },
            {
                "id": "c_in_closed_list",
                "name": "Card in archived list",
                "idList": "list_archived",
                "idBoard": self.board_id,
                "closed": False,
                "due": None,
                "labels": [],
            },
        ]
        transport = self._make_transport(cards=cards)
        adapter = self._make_adapter(transport=transport)

        tasks = adapter.get_tasks("test_proj")
        t_map = {t.id: t for t in tasks}

        self.assertTrue(t_map["c_open"].is_included)
        self.assertFalse(t_map["c_closed_card"].is_included)
        self.assertFalse(t_map["c_in_closed_list"].is_included)

        evidence = adapter.get_evidence("test_proj")
        ev_ids = [e.provenance.external_id for e in evidence]
        self.assertIn("c_closed_card", ev_ids)
        self.assertIn("c_in_closed_list", ev_ids)

    def test_timezone_aware_and_none_dates(self):
        """ISO dates with offset/Z are converted to default timezone; due=None is preserved."""
        adapter = self._make_adapter()
        tasks = adapter.get_tasks("test_proj")
        t_map = {t.id: t for t in tasks}

        # card_1 has "2026-10-05T12:00:00.000Z"
        self.assertIsNotNone(t_map["card_1"].due_date)
        self.assertEqual(t_map["card_1"].due_date.tzinfo, self.tz)
        self.assertEqual(t_map["card_1"].due_date.hour, 7)

        # card_2 has due=None
        self.assertIsNone(t_map["card_2"].due_date)

        # card_3 has "2026-10-01T15:30:00+00:00"
        self.assertIsNotNone(t_map["card_3"].due_date)
        self.assertEqual(t_map["card_3"].due_date.tzinfo, self.tz)
        self.assertEqual(t_map["card_3"].due_date.hour, 10)
        self.assertEqual(t_map["card_3"].due_date.minute, 30)

    def test_naive_or_malformed_due_date_rejected(self):
        """Naive datetime without offset/Z and malformed strings raise malformed_payload."""
        for bad_due in ["2026-10-05T12:00:00", "not-a-date", "2026-02-30T00:00:00Z", 12345]:
            cards = [{
                "id": "c_bad_due",
                "name": "Bad Due",
                "idList": "list_todo",
                "idBoard": self.board_id,
                "closed": False,
                "due": bad_due,
            }]
            transport = self._make_transport(cards=cards)
            adapter = self._make_adapter(transport=transport)

            with self.assertRaises(SourceError) as ctx:
                adapter.get_tasks("test_proj")
            self.assertEqual(ctx.exception.provider, SourceProvider.trello)
            self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_strict_bool_not_int(self):
        """Integer 1 or 0 for boolean closed flag is strictly rejected."""
        # Int in card closed
        cards = [{
            "id": "c_int_closed",
            "name": "Int closed",
            "idList": "list_todo",
            "idBoard": self.board_id,
            "closed": 1,
        }]
        transport = self._make_transport(cards=cards)
        adapter = self._make_adapter(transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_tasks("test_proj")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

        # Int in list closed
        lists = [
            {"id": "list_1", "name": "List 1", "closed": 0, "idBoard": self.board_id},
        ]
        transport = self._make_transport(lists=lists)
        adapter = self._make_adapter(transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_tasks("test_proj")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_duplicate_card_or_list_ids_rejected(self):
        """Duplicate card IDs or duplicate list IDs reject payload as malformed."""
        cards = [
            {"id": "dup_c", "name": "C1", "idList": "list_todo", "idBoard": self.board_id, "closed": False},
            {"id": "dup_c", "name": "C2", "idList": "list_todo", "idBoard": self.board_id, "closed": False},
        ]
        transport = self._make_transport(cards=cards)
        adapter = self._make_adapter(transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_tasks("test_proj")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

        lists = [
            {"id": "dup_l", "name": "L1", "closed": False, "idBoard": self.board_id},
            {"id": "dup_l", "name": "L2", "closed": False, "idBoard": self.board_id},
        ]
        transport = self._make_transport(lists=lists)
        adapter = self._make_adapter(transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_tasks("test_proj")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_board_identity_mismatch_rejected(self):
        """Mismatch between card/list idBoard and adapter configured board_id raises malformed_payload."""
        lists = [
            {"id": "l1", "name": "L1", "closed": False, "idBoard": "other_board"},
        ]
        transport = self._make_transport(lists=lists)
        adapter = self._make_adapter(transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_tasks("test_proj")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

        cards = [
            {"id": "c1", "name": "C1", "idList": "list_todo", "idBoard": "other_board", "closed": False},
        ]
        transport = self._make_transport(cards=cards)
        adapter = self._make_adapter(transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_tasks("test_proj")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_missing_referenced_list_rejected(self):
        """Card referencing an idList not present on board raises malformed_payload."""
        cards = [
            {"id": "c1", "name": "C1", "idList": "nonexistent_list", "idBoard": self.board_id, "closed": False},
        ]
        transport = self._make_transport(cards=cards)
        adapter = self._make_adapter(transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_tasks("test_proj")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_card_count_ceiling_and_bounds(self):
        """Payload with >= 1000 cards or exceeding configured max_cards raises limit_exceeded."""
        cards = self._sample_cards()
        transport = self._make_transport(cards=cards)
        adapter = self._make_adapter(transport=transport, max_cards=2)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_tasks("test_proj")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.limit_exceeded)

        many_cards = [
            {"id": f"card_{i}", "name": f"T{i}", "idList": "list_todo", "idBoard": self.board_id, "closed": False}
            for i in range(1000)
        ]
        transport = self._make_transport(cards=many_cards)
        adapter = self._make_adapter(transport=transport)
        with self.assertRaises(SourceError) as ctx:
            adapter.get_tasks("test_proj")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.limit_exceeded)

    def test_empty_board(self):
        """Empty board with 0 lists and 0 cards returns empty lists successfully."""
        transport = self._make_transport(lists=[], cards=[])
        adapter = self._make_adapter(transport=transport)

        tasks = adapter.get_tasks("test_proj")
        evidence = adapter.get_evidence("test_proj")
        self.assertEqual(tasks, [])
        self.assertEqual(evidence, [])

    def test_transport_error_propagation(self):
        """Underlying transport SourceError categories are preserved with trello provider."""
        for cat in [
            SourceErrorCategory.authentication,
            SourceErrorCategory.permission_denied,
            SourceErrorCategory.rate_limit,
            SourceErrorCategory.timeout,
            SourceErrorCategory.network,
        ]:
            err = SourceError(SourceProvider.trello, cat)
            transport = self._make_transport(lists=err)
            adapter = self._make_adapter(transport=transport)

            with self.assertRaises(SourceError) as ctx:
                adapter.get_tasks("test_proj")
            self.assertEqual(ctx.exception.provider, SourceProvider.trello)
            self.assertEqual(ctx.exception.category, cat)

    def test_credentials_never_in_url_or_repr(self):
        """API key and token are never in request URLs, params, or adapter repr."""
        transport = self._make_transport()
        adapter = self._make_adapter(transport=transport)

        adapter.get_tasks("test_proj")
        for call in transport.calls:
            self.assertNotIn(self.api_key, call["url"])
            self.assertNotIn(self.token, call["url"])
            self.assertIsNone(call["params"])
            auth = call["headers"].get("Authorization", "")
            self.assertIn(self.api_key, auth)
            self.assertIn(self.token, auth)

        rep = repr(adapter)
        self.assertNotIn(self.api_key, rep)
        self.assertNotIn(self.token, rep)

    def test_echoed_credentials_redacted_from_tasks_and_evidence(self):
        """Configured secrets echoed in remote text are sanitized from tasks and evidence."""
        cards = [{
            "id": "card_leak",
            "name": f"Title leaking {self.api_key} in name",
            "desc": f"Detail leaking token {self.token} in desc",
            "idList": "list_todo",
            "idBoard": self.board_id,
            "closed": False,
            "due": None,
            "labels": [{"name": f"lbl-{self.api_key}"}],
            "idMembers": [],
        }]
        transport = self._make_transport(cards=cards)
        adapter = self._make_adapter(transport=transport)

        tasks = adapter.get_tasks("test_proj")
        evidence = adapter.get_evidence("test_proj")

        t = tasks[0]
        self.assertNotIn(self.api_key, t.title)
        self.assertNotIn(self.token, t.detail or "")
        self.assertNotIn(self.api_key, " ".join(t.labels))

        ev = evidence[0]
        self.assertNotIn(self.api_key, ev.summary)
        self.assertNotIn(self.token, ev.summary)
        payload_str = str(ev.provenance.payload)
        self.assertNotIn(self.api_key, payload_str)
        self.assertNotIn(self.token, payload_str)

    def test_header_injection_rejection(self):
        """Control characters, CR, LF, and quotes in key, token, or board_id are rejected at init."""
        bad_creds = [
            "key\r\ninjection",
            'key"quote',
            "key\x00null",
            "key\tcontrol",
            "",
            "   ",
        ]
        for bad in bad_creds:
            with self.assertRaises(SourceError) as ctx:
                TrelloSourceAdapter(
                    board_id=self.board_id,
                    api_key=bad,
                    token=self.token,
                )
            self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

            with self.assertRaises(SourceError) as ctx:
                TrelloSourceAdapter(
                    board_id=self.board_id,
                    api_key=self.api_key,
                    token=bad,
                )
            self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        bad_board_ids = [
            "board/slash",
            "board?query=1",
            "board#frag",
            "board 123",
            "board\ninjection",
            "",
        ]
        for bad in bad_board_ids:
            with self.assertRaises(SourceError) as ctx:
                TrelloSourceAdapter(
                    board_id=bad,
                    api_key=self.api_key,
                    token=self.token,
                )
            self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

    def test_single_cached_collection_for_tasks_and_evidence(self):
        """Calling get_tasks and get_evidence triggers exactly 2 GETs and returns consistent data."""
        transport = self._make_transport()
        adapter = self._make_adapter(transport=transport)

        tasks = adapter.get_tasks("proj_1")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0]["url"], self.lists_url)
        self.assertEqual(transport.calls[1]["url"], self.cards_url)

        evidence = adapter.get_evidence("proj_1")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(tasks), len(evidence))

        tasks2 = adapter.get_tasks("proj_1")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(tasks2), len(tasks))

    def test_evidence_first_call_order(self):
        """Calling get_evidence first triggers retrieval, subsequent get_tasks uses cache."""
        transport = self._make_transport()
        adapter = self._make_adapter(transport=transport)

        evidence = adapter.get_evidence("proj_ev_first")
        self.assertEqual(len(transport.calls), 2)

        tasks = adapter.get_tasks("proj_ev_first")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(tasks), len(evidence))

    def test_evidence_provenance_and_payload_allowlisting(self):
        """Evidence items have canonical provenance, last_activity, and allowlisted payloads."""
        adapter = self._make_adapter()
        evidence = adapter.get_evidence("proj_alpha")

        self.assertGreater(len(evidence), 0)
        ev = evidence[0]
        self.assertIsInstance(ev, EvidenceItem)
        self.assertEqual(ev.category, "task")
        self.assertEqual(ev.provenance.source, EvidenceSource.trello)
        self.assertEqual(ev.provenance.project_id, "proj_alpha")
        self.assertEqual(ev.provenance.external_id, "card_1")
        self.assertIn("card_1", ev.summary)

        self.assertEqual(ev.provenance.payload.get("url"), "https://api.trello.com/1/cards/card_1")
        self.assertEqual(ev.provenance.payload.get("last_activity"), "2026-10-05T11:00:00+00:00")

        allowed_keys = {
            "card_id",
            "title",
            "state",
            "list_id",
            "list_name",
            "labels",
            "member_ids",
            "due_date",
            "last_activity",
            "is_archived",
            "is_included",
            "has_contradiction",
            "detail",
            "url",
            "due_complete",
            "start_date",
            "start_raw",
        }
        self.assertTrue(set(ev.provenance.payload.keys()).issubset(allowed_keys))

    def test_cache_mutation_isolation(self):
        """Mutations to returned tasks or evidence do not corrupt internal cache."""
        adapter = self._make_adapter()
        tasks1 = adapter.get_tasks("proj_iso")
        tasks1[0].title = "MUTATED_TITLE"

        tasks2 = adapter.get_tasks("proj_iso")
        self.assertNotEqual(tasks2[0].title, "MUTATED_TITLE")
        self.assertEqual(tasks2[0].title, "Task 1")

        evidence1 = adapter.get_evidence("proj_iso")
        evidence1[0].summary = "MUTATED_SUMMARY"

        evidence2 = adapter.get_evidence("proj_iso")
        self.assertNotEqual(evidence2[0].summary, "MUTATED_SUMMARY")

    def test_cache_invalidation_and_refresh(self):
        """Invalidating cache forces refetch on next call."""
        transport = self._make_transport()
        adapter = self._make_adapter(transport=transport)

        adapter.get_tasks("proj_inv")
        self.assertEqual(len(transport.calls), 2)

        adapter.invalidate_cache("proj_inv")
        adapter.get_tasks("proj_inv")
        self.assertEqual(len(transport.calls), 4)

        refreshed = adapter.refresh("proj_inv")
        self.assertEqual(len(transport.calls), 6)
        self.assertEqual(len(refreshed), 3)

    def test_failure_retry_leaves_no_cached_partial_state(self):
        """If lists succeeds but cards fails, no partial cache is committed."""
        responses: dict[str, HttpResponse | Exception] = {
            self.lists_url: HttpResponse(status_code=200, body=self._sample_lists()),
            self.cards_url: SourceError(SourceProvider.trello, SourceErrorCategory.network),
        }
        transport = FakeTransport(responses)
        adapter = self._make_adapter(transport=transport)

        with self.assertRaises(SourceError) as ctx:
            adapter.get_tasks("proj_fail")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.network)
        self.assertEqual(len(transport.calls), 2)

        transport.responses[self.cards_url] = HttpResponse(status_code=200, body=self._sample_cards())
        tasks = adapter.get_tasks("proj_fail")
        self.assertEqual(len(tasks), 3)
        self.assertEqual(len(transport.calls), 4)

    def test_invalid_project_id_or_timezone_rejected(self):
        """Empty project_id or invalid timezone name raises configuration error."""
        adapter = self._make_adapter()
        for bad_proj in ["", "   ", None]:
            with self.assertRaises(SourceError) as ctx:
                adapter.get_tasks(bad_proj)  # type: ignore
            self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        with self.assertRaises(SourceError) as ctx:
            TrelloSourceAdapter(
                board_id=self.board_id,
                api_key=self.api_key,
                token=self.token,
                timezone_name="Invalid/Unknown_Zone",
            )
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

    def _assert_safe_rejection(self, adapter: TrelloSourceAdapter, exc: SourceError, expected_cat: SourceErrorCategory) -> None:
        self.assertEqual(exc.provider, SourceProvider.trello)
        self.assertEqual(exc.category, expected_cat)
        self.assertNotIn(self.api_key, str(exc))
        self.assertNotIn(self.token, str(exc))
        self.assertNotIn(self.api_key, repr(exc))
        self.assertNotIn(self.token, repr(exc))
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.assertNotIn(self.api_key, tb)
        self.assertNotIn(self.token, tb)
        self.assertEqual(len(adapter._cache), 0)

    def test_credential_echo_in_list_id_rejected(self):
        """Incoming list ID echoing configured key/token (exact or substring) raises malformed_payload without secret leakage."""
        for secret_ident in [self.api_key, self.token, f"pre_{self.api_key}_post", f"pre_{self.token}_post"]:
            lists = [
                {"id": secret_ident, "name": "Secret List", "closed": False, "idBoard": self.board_id},
            ]
            cards = [
                {"id": "card_ok", "name": "Card", "idList": secret_ident, "idBoard": self.board_id, "closed": False},
            ]
            transport = self._make_transport(lists=lists, cards=cards)
            adapter = self._make_adapter(transport=transport)

            with self.assertRaises(SourceError) as ctx:
                adapter.get_tasks("test_proj")
            self._assert_safe_rejection(adapter, ctx.exception, SourceErrorCategory.malformed_payload)

    def test_credential_echo_in_list_id_board_rejected(self):
        """Incoming list idBoard echoing configured key/token (exact or substring) raises malformed_payload."""
        for secret_ident in [self.api_key, self.token, f"b_{self.api_key}"]:
            lists = [
                {"id": "list_ok", "name": "List", "closed": False, "idBoard": secret_ident},
            ]
            transport = self._make_transport(lists=lists)
            adapter = self._make_adapter(transport=transport)

            with self.assertRaises(SourceError) as ctx:
                adapter.get_tasks("test_proj")
            self._assert_safe_rejection(adapter, ctx.exception, SourceErrorCategory.malformed_payload)

    def test_credential_echo_in_card_id_rejected(self):
        """Incoming card ID echoing configured key/token (exact or substring) raises malformed_payload."""
        for secret_ident in [self.api_key, self.token, f"c_{self.api_key}_end"]:
            cards = [{
                "id": secret_ident,
                "name": "Secret Card",
                "idList": "list_todo",
                "idBoard": self.board_id,
                "closed": False,
            }]
            transport = self._make_transport(cards=cards)
            adapter = self._make_adapter(transport=transport)

            with self.assertRaises(SourceError) as ctx:
                adapter.get_tasks("test_proj")
            self._assert_safe_rejection(adapter, ctx.exception, SourceErrorCategory.malformed_payload)

    def test_credential_echo_in_card_id_list_rejected(self):
        """Incoming card idList echoing configured key/token (exact or substring) raises malformed_payload."""
        for secret_ident in [self.api_key, self.token, f"l_{self.token}"]:
            cards = [{
                "id": "card_ok",
                "name": "Card",
                "idList": secret_ident,
                "idBoard": self.board_id,
                "closed": False,
            }]
            transport = self._make_transport(cards=cards)
            adapter = self._make_adapter(transport=transport)

            with self.assertRaises(SourceError) as ctx:
                adapter.get_tasks("test_proj")
            self._assert_safe_rejection(adapter, ctx.exception, SourceErrorCategory.malformed_payload)

    def test_credential_echo_in_card_id_board_rejected(self):
        """Incoming card idBoard echoing configured key/token (exact or substring) raises malformed_payload."""
        for secret_ident in [self.api_key, self.token, f"b_{self.token}"]:
            cards = [{
                "id": "card_ok",
                "name": "Card",
                "idList": "list_todo",
                "idBoard": secret_ident,
                "closed": False,
            }]
            transport = self._make_transport(cards=cards)
            adapter = self._make_adapter(transport=transport)

            with self.assertRaises(SourceError) as ctx:
                adapter.get_tasks("test_proj")
            self._assert_safe_rejection(adapter, ctx.exception, SourceErrorCategory.malformed_payload)

    def test_credential_echo_in_card_member_id_rejected(self):
        """Incoming card idMembers item echoing configured key/token (exact or substring) raises malformed_payload."""
        for secret_ident in [self.api_key, self.token, f"mem_{self.api_key}"]:
            cards = [{
                "id": "card_ok",
                "name": "Card",
                "idList": "list_todo",
                "idBoard": self.board_id,
                "closed": False,
                "idMembers": ["normal_mem", secret_ident],
            }]
            transport = self._make_transport(cards=cards)
            adapter = self._make_adapter(transport=transport)

            with self.assertRaises(SourceError) as ctx:
                adapter.get_tasks("test_proj")
            self._assert_safe_rejection(adapter, ctx.exception, SourceErrorCategory.malformed_payload)

    def test_credential_echo_in_configured_board_id_rejected(self):
        """Configured board_id echoing key/token raises configuration error at init with 0 requests made."""
        transport = FakeTransport()
        for secret_board in [self.api_key, self.token, f"b_{self.api_key}"]:
            with self.assertRaises(SourceError) as ctx:
                TrelloSourceAdapter(
                    board_id=secret_board,
                    api_key=self.api_key,
                    token=self.token,
                    transport=transport,
                )
            self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)
            self.assertEqual(len(transport.calls), 0)

    def test_credential_echo_in_project_id_rejected(self):
        """project_id echoing key/token raises configuration error before any HTTP request or cache use."""
        transport = FakeTransport()
        adapter = self._make_adapter(transport=transport)

        for secret_proj in [self.api_key, self.token, f"proj_{self.token}_x"]:
            with self.assertRaises(SourceError) as ctx:
                adapter.get_tasks(secret_proj)
            self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)
            self.assertEqual(len(transport.calls), 0)
            self.assertEqual(len(adapter._cache), 0)

            with self.assertRaises(SourceError) as ctx:
                adapter.get_evidence(secret_proj)
            self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)
            self.assertEqual(len(transport.calls), 0)
            self.assertEqual(len(adapter._cache), 0)

    def test_state_strategy_init_validation(self):
        """Accepted strategies are list_mapping and date_fields; others raise configuration error."""
        transport = FakeTransport()
        adapter = self._make_adapter(state_strategy="list_mapping", transport=transport)
        self.assertEqual(adapter.state_strategy, "list_mapping")
        adapter2 = self._make_adapter(state_strategy="date_fields", transport=transport)
        self.assertEqual(adapter2.state_strategy, "date_fields")
        for bad_strategy in ["invalid", "", "other", None, 123]:
            with self.assertRaises(SourceError) as ctx:
                self._make_adapter(state_strategy=bad_strategy, transport=transport)
            self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

    def test_strategy_a_list_mapping_preserves_existing_behavior_with_dates(self):
        """A: list_mapping preserves existing list-based status regardless of dueComplete or start dates."""
        cards = [
            {"id": "c1", "name": "C1", "idList": "list_todo", "idBoard": self.board_id, "closed": False,
             "dueComplete": True, "start": "2026-10-01T00:00:00Z"},
            {"id": "c2", "name": "C2", "idList": "list_done", "idBoard": self.board_id, "closed": False,
             "dueComplete": False, "start": "2026-10-10T00:00:00Z"},
            {"id": "c3", "name": "C3", "idList": "list_doing", "idBoard": self.board_id, "closed": False,
             "dueComplete": False, "labels": [{"name": "blocked"}], "start": "2026-10-01T00:00:00Z"},
        ]
        adapter = self._make_adapter(transport=self._make_transport(cards=cards), state_strategy="list_mapping")
        t_map = {t.id: t for t in adapter.get_tasks("test_proj")}
        self.assertEqual(t_map["c1"].state, TaskState.pending)
        self.assertFalse(t_map["c1"].has_contradiction)
        self.assertEqual(t_map["c2"].state, TaskState.completed)
        self.assertFalse(t_map["c2"].has_contradiction)
        self.assertEqual(t_map["c3"].state, TaskState.blocked)
        self.assertFalse(t_map["c3"].has_contradiction)

    def test_strategy_b_date_fields_phase_and_true_completed(self):
        """B: date_fields with dueComplete=True maps to completed regardless of list phase."""
        cards = [
            {"id": "c_comp", "name": "C Comp", "idList": "list_todo", "idBoard": self.board_id, "closed": False,
             "dueComplete": True, "start": "2026-10-01T00:00:00Z"},
        ]
        adapter = self._make_adapter(transport=self._make_transport(cards=cards), state_strategy="date_fields")
        t = adapter.get_tasks("test_proj")[0]
        self.assertEqual(t.state, TaskState.completed)
        self.assertFalse(t.has_contradiction)

    def test_strategy_c_date_fields_false_future_pending(self):
        """C: date_fields with dueComplete=False and aware start > collection_time maps to pending."""
        frozen_now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
        cards = [
            {"id": "c_pend", "name": "C Pend", "idList": "list_doing", "idBoard": self.board_id, "closed": False,
             "dueComplete": False, "start": "2026-10-07T13:00:00Z"},
        ]
        adapter = self._make_adapter(
            transport=self._make_transport(cards=cards),
            state_strategy="date_fields",
            clock=lambda: frozen_now,
        )
        t = adapter.get_tasks("test_proj")[0]
        self.assertEqual(t.state, TaskState.pending)
        self.assertFalse(t.has_contradiction)

    def test_strategy_d_date_fields_false_start_past_equality_offset_in_progress(self):
        """D: date_fields with dueComplete=False and start <= collection_time maps to in_progress."""
        frozen_now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
        test_cases = [
            ("past", "2026-10-07T11:00:00Z"),
            ("equal", "2026-10-07T12:00:00Z"),
            ("offset_equal", "2026-10-07T07:00:00-05:00"),
        ]
        for name, start_val in test_cases:
            with self.subTest(case=name):
                cards = [
                    {"id": f"c_{name}", "name": "Card", "idList": "list_todo", "idBoard": self.board_id,
                     "closed": False, "dueComplete": False, "start": start_val},
                ]
                adapter = self._make_adapter(
                    transport=self._make_transport(cards=cards),
                    state_strategy="date_fields",
                    clock=lambda: frozen_now,
                )
                t = adapter.get_tasks("test_proj")[0]
                self.assertEqual(t.state, TaskState.in_progress)
                self.assertFalse(t.has_contradiction)

    def test_strategy_e_date_fields_false_blocked_is_blocked(self):
        """E: date_fields with dueComplete=False and configured blocked label maps to blocked."""
        frozen_now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
        for start_val in ["2026-10-07T10:00:00Z", "2026-10-07T14:00:00Z", None]:
            with self.subTest(start=start_val):
                card = {
                    "id": "c_blk", "name": "Card", "idList": "list_todo", "idBoard": self.board_id,
                    "closed": False, "dueComplete": False, "labels": [{"name": "bloqueada"}],
                }
                if start_val is not None:
                    card["start"] = start_val
                adapter = self._make_adapter(
                    transport=self._make_transport(cards=[card]),
                    state_strategy="date_fields",
                    clock=lambda: frozen_now,
                )
                t = adapter.get_tasks("test_proj")[0]
                self.assertEqual(t.state, TaskState.blocked)
                self.assertFalse(t.has_contradiction)

    def test_strategy_f_date_fields_true_blocked_contradiction(self):
        """F: date_fields with dueComplete=True and blocked label stays completed with contradiction."""
        cards = [
            {"id": "c_f", "name": "Card", "idList": "list_todo", "idBoard": self.board_id, "closed": False,
             "dueComplete": True, "labels": [{"name": "blocked"}]},
        ]
        adapter = self._make_adapter(transport=self._make_transport(cards=cards), state_strategy="date_fields")
        t = adapter.get_tasks("test_proj")[0]
        self.assertEqual(t.state, TaskState.completed)
        self.assertTrue(t.has_contradiction)

    def test_strategy_g_date_fields_true_future_start_contradiction(self):
        """G: date_fields with dueComplete=True and future start stays completed with contradiction."""
        frozen_now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
        cards = [
            {"id": "c_g", "name": "Card", "idList": "list_todo", "idBoard": self.board_id, "closed": False,
             "dueComplete": True, "start": "2026-10-07T15:00:00Z"},
        ]
        adapter = self._make_adapter(
            transport=self._make_transport(cards=cards),
            state_strategy="date_fields",
            clock=lambda: frozen_now,
        )
        t = adapter.get_tasks("test_proj")[0]
        self.assertEqual(t.state, TaskState.completed)
        self.assertTrue(t.has_contradiction)

    def test_strategy_h_date_fields_no_three_groups_and_phase_filtering(self):
        """H: date_fields does not require 3 status groups and applies included_list_ids filter."""
        mapping = TaskMappingConfig(
            pending_list_ids=[],
            in_progress_list_ids=[],
            completed_list_ids=[],
            included_list_ids=["list_todo"],
        )
        cards = [
            {"id": "c_inc", "name": "Included", "idList": "list_todo", "idBoard": self.board_id, "closed": False,
             "dueComplete": True},
            {"id": "c_exc", "name": "Excluded", "idList": "list_doing", "idBoard": self.board_id, "closed": False,
             "dueComplete": True},
        ]
        adapter = self._make_adapter(
            transport=self._make_transport(cards=cards),
            mapping=mapping,
            state_strategy="date_fields",
        )
        t_map = {t.id: t for t in adapter.get_tasks("test_proj")}
        self.assertEqual(t_map["c_inc"].state, TaskState.completed)
        self.assertTrue(t_map["c_inc"].is_included)
        self.assertEqual(t_map["c_exc"].state, TaskState.completed)
        self.assertFalse(t_map["c_exc"].is_included)

    def test_strategy_i_evidence_fields_and_provenance_clock(self):
        """I: EvidenceItem preserves due_complete, start_date, and provenance timestamp matches collection."""
        frozen_now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
        cards = [
            {"id": "c_ev", "name": "Ev Card", "idList": "list_todo", "idBoard": self.board_id, "closed": False,
             "dueComplete": True, "start": "2026-10-07T10:00:00Z"},
        ]
        adapter = self._make_adapter(
            transport=self._make_transport(cards=cards),
            state_strategy="date_fields",
            clock=lambda: frozen_now,
        )
        evidence = adapter.get_evidence("test_proj")
        self.assertEqual(len(evidence), 1)
        ev = evidence[0]
        self.assertEqual(ev.provenance.timestamp, frozen_now)
        self.assertIs(ev.provenance.payload.get("due_complete"), True)
        self.assertIsNotNone(ev.provenance.payload.get("start_date"))
        self.assertEqual(ev.provenance.payload.get("start_raw"), "2026-10-07T10:00:00Z")

    def test_strategy_j_strict_payload_validation_and_atomicity(self):
        """J: Invalid start or non-boolean dueComplete fails as malformed_payload in both modes without cache."""
        bad_cards = [
            ("dueComplete_null", {"id": "c_bad", "name": "C", "idList": "list_todo", "idBoard": self.board_id,
                                  "closed": False, "dueComplete": None}),
            ("dueComplete_0", {"id": "c_bad", "name": "C", "idList": "list_todo", "idBoard": self.board_id,
                               "closed": False, "dueComplete": 0}),
            ("dueComplete_1", {"id": "c_bad", "name": "C", "idList": "list_todo", "idBoard": self.board_id,
                               "closed": False, "dueComplete": 1}),
            ("dueComplete_str", {"id": "c_bad", "name": "C", "idList": "list_todo", "idBoard": self.board_id,
                                 "closed": False, "dueComplete": "true"}),
            ("dueComplete_list", {"id": "c_bad", "name": "C", "idList": "list_todo", "idBoard": self.board_id,
                                  "closed": False, "dueComplete": []}),
            ("start_empty", {"id": "c_bad", "name": "C", "idList": "list_todo", "idBoard": self.board_id,
                             "closed": False, "start": ""}),
            ("start_whitespace", {"id": "c_bad", "name": "C", "idList": "list_todo", "idBoard": self.board_id,
                                  "closed": False, "start": "   "}),
            ("start_malformed", {"id": "c_bad", "name": "C", "idList": "list_todo", "idBoard": self.board_id,
                                 "closed": False, "start": "not-a-date"}),
            ("start_naive", {"id": "c_bad", "name": "C", "idList": "list_todo", "idBoard": self.board_id,
                             "closed": False, "start": "2026-10-07T12:00:00"}),
            ("start_int", {"id": "c_bad", "name": "C", "idList": "list_todo", "idBoard": self.board_id,
                           "closed": False, "start": 12345}),
        ]
        for strat in ["list_mapping", "date_fields"]:
            for label, card in bad_cards:
                with self.subTest(strategy=strat, label=label):
                    adapter = self._make_adapter(
                        transport=self._make_transport(cards=[card]),
                        state_strategy=strat,
                    )
                    with self.assertRaises(SourceError) as ctx:
                        adapter.get_tasks("test_proj")
                    self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)
                    self.assertEqual(len(adapter._cache), 0)

    def test_strategy_unknown_signals_never_invent_states(self):
        """date_fields returns unknown on missing dueComplete or insufficient signals, never inventing states."""
        frozen_now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
        cases = [
            ("false_missing_start", {"id": "c_u1", "name": "U1", "idList": "list_todo", "idBoard": self.board_id,
                                     "closed": False, "dueComplete": False}),
            ("false_null_start", {"id": "c_u2", "name": "U2", "idList": "list_todo", "idBoard": self.board_id,
                                  "closed": False, "dueComplete": False, "start": None}),
            ("missing_dueComplete_start_past", {"id": "c_u3", "name": "U3", "idList": "list_todo", "idBoard": self.board_id,
                                                "closed": False, "start": "2026-10-07T10:00:00Z"}),
            ("missing_dueComplete_blocked_label", {"id": "c_u4", "name": "U4", "idList": "list_todo", "idBoard": self.board_id,
                                                   "closed": False, "labels": [{"name": "blocked"}]}),
        ]
        for name, card in cases:
            with self.subTest(case=name):
                adapter = self._make_adapter(
                    transport=self._make_transport(cards=[card]),
                    state_strategy="date_fields",
                    clock=lambda: frozen_now,
                )
                t = adapter.get_tasks("test_proj")[0]
                self.assertEqual(t.state, TaskState.unknown)
                self.assertFalse(t.has_contradiction)


if __name__ == "__main__":
    unittest.main()
