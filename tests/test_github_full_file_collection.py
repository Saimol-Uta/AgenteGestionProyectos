"""G1: la recopilacion de codigo debe obtener integro el archivo que contiene el defecto.

La regresion comprobada sobre HORMIGESTION: el backend cambio 14 archivos de
codigo mas 2 fuentes binarias. El tope de archivos completos por repositorio
era 8 y el orden es alfabetico, de modo que el archivo con el defecto
(`src/infrastructure/database/despacho-store.ts`, el decimo) nunca se capturaba
y la ruta de accion correctiva era inalcanzable con `finding_source_unavailable`.
Ademas, el binario sin parche se contaba como fuente incompleta y el parche y
el archivo completo compartian un unico contador de bytes.
"""

import base64
import hashlib
import unittest
from typing import Any

from agente_gestion.adapters.sources.github import (
    GitHubCodeSourceAdapter,
)
from agente_gestion.adapters.sources.http_transport import (
    HttpGetTransportPort,
    HttpResponse,
)
from agente_gestion.adapters.sources.multirepo import DEFAULT_MULTIREPO_MAX_FULL_FILES
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)


class FakeTransport(HttpGetTransportPort):
    """Deterministic fake GET transport that records every request."""

    def __init__(self, responses: dict[str, HttpResponse | Exception] | None = None):
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpResponse:
        self.calls.append({"url": url, "params": dict(params) if params else None})
        response = self.responses.get(url)
        if isinstance(response, Exception):
            raise response
        if response is not None:
            return response
        return HttpResponse(status_code=200, body={})


def _contents_url(owner: str, repo: str, path: str, sha: str) -> str:
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={sha}"


def _contents_response(content: str, blob_sha: str) -> HttpResponse:
    return HttpResponse(
        status_code=200,
        body={
            "encoding": "base64",
            "content": base64.b64encode(content.encode()).decode(),
            "sha": blob_sha,
            "size": len(content.encode()),
        },
    )


class TestCompleteFileCollection(unittest.TestCase):
    """The complete-file reads must cover every code file of a realistic commit."""

    def setUp(self) -> None:
        self.owner = "test-org"
        self.repo = "test-repo"
        self.sha = "a" * 40
        self.commit_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{self.sha}"

    def _commit_with_files(self, entries: list[dict[str, Any]], bytes_received: int = 0) -> HttpResponse:
        return HttpResponse(
            status_code=200,
            body={"sha": self.sha, "files": entries},
            bytes_received=bytes_received,
        )

    def test_code_file_beyond_the_old_cap_is_captured_whole(self):
        """Fourteen code files and one binary: every code file is captured whole."""
        code_paths = [
            "scripts/seed-real-history.ts",
            "scripts/setup-production.ts",
            "scripts/update-catalog.ts",
            "src/app.ts",
            "src/application/despacho-service.ts",
            "src/application/services.ts",
            "src/domain/configuracion.ts",
            "src/domain/demo.ts",
            "src/domain/repositories.ts",
            "src/infrastructure/database/despacho-store.ts",
            "src/infrastructure/database/seed-demo.ts",
            "src/infrastructure/database/store.ts",
            "src/infrastructure/pdf.ts",
            "src/server.ts",
        ]
        binary_path = "src/infrastructure/assets/fonts/Consolas-Bold.ttf"
        entries = [
            {
                "filename": path, "status": "modified",
                "patch": "@@ -1 +1 @@\n-old\n+new\n", "sha": f"{index:040d}",
            }
            for index, path in enumerate(code_paths)
        ]
        entries.append({"filename": binary_path, "status": "added", "patch": None, "sha": "f" * 40})
        responses: dict[str, HttpResponse | Exception] = {self.commit_url: self._commit_with_files(entries)}
        for index, path in enumerate(code_paths):
            responses[_contents_url(self.owner, self.repo, path, self.sha)] = _contents_response(
                f"export const value{index} = 1;\n", f"{index:040d}",
            )
        transport = FakeTransport(responses)
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)

        snippets = adapter.get_diffs("proj1", commit_sha=self.sha)
        evidence = adapter.get_evidence("proj1", commit_sha=self.sha)

        full_files = [item for item in snippets if item.evidence_scope == "full_file"]
        self.assertEqual(len(full_files), len(code_paths))
        captured = {item.file_path for item in full_files}
        self.assertIn("src/infrastructure/database/despacho-store.ts", captured)

        # A binary asset is read last, so it can never starve the code files
        # that a corrective action needs as its patch base.
        contents_calls = [
            call["url"] for call in transport.calls if "/contents/" in call["url"]
        ]
        self.assertEqual(len(contents_calls), len(code_paths) + 1)
        self.assertEqual(
            contents_calls[-1],
            _contents_url(self.owner, self.repo, binary_path, self.sha),
        )

        # A binary is never a code source, and the fact that its content could
        # not be obtained stays visible as an unavailable textual source.
        self.assertNotIn(binary_path, {item.file_path for item in snippets})
        self.assertEqual(adapter.unavailable_file_paths, [binary_path])
        self.assertFalse(adapter.last_collection_complete)
        binary_evidence = next(
            item for item in evidence if item.provenance.file_path == binary_path
        )
        self.assertFalse(binary_evidence.provenance.payload["patch_available"])
        self.assertEqual(binary_evidence.provenance.payload["reason"], "patch_unavailable")
        self.assertFalse(binary_evidence.provenance.payload["full_file_available"])

    def test_complete_file_read_does_not_consume_the_patch_budget(self):
        """The complete-file read is charged to its own budget."""
        path = "src/infrastructure/database/despacho-store.ts"
        source = "export const total = 1;\n"
        transport = FakeTransport({
            self.commit_url: self._commit_with_files(
                [{"filename": path, "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+new\n", "sha": "b" * 40}],
                bytes_received=900,
            ),
            _contents_url(self.owner, self.repo, path, self.sha): _contents_response(source, "b" * 40),
        })
        adapter = GitHubCodeSourceAdapter(
            owner=self.owner, repo=self.repo, transport=transport,
            max_total_bytes=1000, max_full_file_bytes=1000,
        )

        snippets = adapter.get_diffs("proj1", commit_sha=self.sha)

        full_files = [item for item in snippets if item.evidence_scope == "full_file"]
        self.assertEqual(len(full_files), 1)
        self.assertEqual(full_files[0].content, source)

    def test_complete_file_budget_is_enforced(self):
        """The complete-file ceiling still stops an oversized collection."""
        path = "src/app.ts"
        transport = FakeTransport({
            self.commit_url: self._commit_with_files(
                [{"filename": path, "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+new\n", "sha": "c" * 40}],
            ),
            _contents_url(self.owner, self.repo, path, self.sha): HttpResponse(
                status_code=200,
                body={
                    "encoding": "base64",
                    "content": base64.b64encode(b"x" * 400).decode(),
                    "sha": "c" * 40,
                    "size": 400,
                },
                bytes_received=400,
            ),
        })
        adapter = GitHubCodeSourceAdapter(
            owner=self.owner, repo=self.repo, transport=transport, max_full_file_bytes=100,
        )
        with self.assertRaises(SourceError) as context:
            adapter.get_diffs("proj1", commit_sha=self.sha)
        self.assertEqual(context.exception.category, SourceErrorCategory.limit_exceeded)

    def test_complete_file_preserves_commit_identity_and_content_integrity(self):
        """The captured file stays bound to the pinned commit and to its own bytes."""
        path = "src/infrastructure/database/despacho-store.ts"
        source = "line one\nline two\nline three\n"
        transport = FakeTransport({
            self.commit_url: self._commit_with_files(
                [{"filename": path, "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+new\n", "sha": "d" * 40}],
            ),
            _contents_url(self.owner, self.repo, path, self.sha): _contents_response(source, "d" * 40),
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)

        snippets = adapter.get_diffs("proj1", commit_sha=self.sha)

        full_files = [item for item in snippets if item.evidence_scope == "full_file"]
        self.assertEqual(len(full_files), 1)
        captured = full_files[0]
        self.assertEqual(captured.commit_sha, self.sha)
        self.assertEqual(captured.file_path, path)
        self.assertEqual(captured.content, source)
        self.assertTrue(captured.is_complete)
        self.assertFalse(captured.truncated)
        self.assertEqual(captured.source_sha256, hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(captured.total_lines, 3)
        self.assertEqual(adapter.last_resolved_commit_sha, self.sha)

    def test_repository_cap_covers_a_realistic_commit(self):
        """The cap must not drop the code files of a real commit again."""
        self.assertGreaterEqual(
            DEFAULT_MULTIREPO_MAX_FULL_FILES, 16,
            "A commit with fourteen code files must not lose its complete files.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
