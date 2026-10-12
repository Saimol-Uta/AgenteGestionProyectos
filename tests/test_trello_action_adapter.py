"""Unit tests for live TrelloActionAdapter using mock transport."""

from datetime import datetime
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from agente_gestion.adapters.sources.trello_action_adapter import TrelloActionAdapter
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory

DEFAULT_TIMEZONE = ZoneInfo("America/Guayaquil")


class TestTrelloActionAdapter(unittest.TestCase):
    def test_trello_action_adapter_validation(self):
        with self.assertRaises(SourceError):
            TrelloActionAdapter(board_id="invalid board id!", api_key="key", token="token")
        with self.assertRaises(SourceError):
            TrelloActionAdapter(board_id="valid_board", api_key="", token="token")
        with self.assertRaises(SourceError):
            TrelloActionAdapter(board_id="valid_board", api_key="key", token="")

    def test_trello_action_adapter_read_due_date(self):
        adapter = TrelloActionAdapter(board_id="board123", api_key="dummy_key", token="dummy_token")

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "id": "card123",
                "idBoard": "board123",
                "due": "2026-10-13T22:00:00.000Z",
                "start": None,
                "idMembers": ["mem1", "mem2"],
            }
            mock_get.return_value = mock_resp

            val = adapter.read_card_field("card123", "due_date")
            self.assertIsInstance(val, datetime)
            self.assertEqual(val.isoformat(), "2026-10-13T17:00:00-05:00")

            call_headers = mock_get.call_args[1]["headers"]
            self.assertIn('OAuth oauth_consumer_key="dummy_key"', call_headers["Authorization"])

    def test_trello_action_adapter_read_mismatched_board(self):
        adapter = TrelloActionAdapter(board_id="board123", api_key="dummy_key", token="dummy_token")

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "id": "card123",
                "idBoard": "another_board",
                "due": "2026-10-13T22:00:00.000Z",
            }
            mock_get.return_value = mock_resp

            with self.assertRaises(SourceError) as exc_info:
                adapter.read_card_field("card123", "due_date")
            self.assertEqual(exc_info.exception.category, SourceErrorCategory.malformed_payload)

    def test_trello_action_adapter_apply_update(self):
        adapter = TrelloActionAdapter(board_id="board123", api_key="dummy_key", token="dummy_token")

        with patch("requests.put") as mock_put:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_put.return_value = mock_resp

            new_dt = datetime(2026, 10, 14, 17, 0, 0, tzinfo=DEFAULT_TIMEZONE)
            ok = adapter.apply_card_field_update("card123", "due_date", new_dt)
            self.assertTrue(ok)

            payload = mock_put.call_args[1]["json"]
            self.assertEqual(payload["due"], "2026-10-14T22:00:00.000Z")


if __name__ == "__main__":
    unittest.main()

