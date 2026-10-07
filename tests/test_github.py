"""Tests for GitHub read-only commit source adapter (T3)."""

from datetime import datetime, timezone
import unittest
from typing import Any

from agente_gestion.adapters.sources.http_transport import (
    HttpGetTransportPort,
    HttpResponse,
)
from agente_gestion.application.validation import SemanticValidator
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.evidence import EvidenceItem, EvidenceSource
from agente_gestion.domain.findings import CandidateFinding, FindingSeverity
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.ports.sources import CodeSourcePort

# Import under test - will cause initial RED before implementation exists
from agente_gestion.adapters.sources.github import (
    GitHubCodeSourceAdapter,
    DEFAULT_MAX_FILES,
    MAX_GITHUB_FILES_CEILING,
)


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
        call_record = {
            "url": url,
            "params": dict(params) if params else None,
            "headers": dict(headers) if headers else None,
            "timeout_seconds": timeout_seconds,
        }
        self.calls.append(call_record)

        # Route matching: exact url with params or base url
        # First check full matching or exact url
        resp = self.responses.get(url)
        if isinstance(resp, Exception):
            raise resp
        if resp is not None:
            return resp

        # Check by base URL without query
        base_url = url.split("?")[0]
        resp = self.responses.get(base_url)
        if isinstance(resp, Exception):
            raise resp
        if resp is not None:
            return resp

        return HttpResponse(status_code=200, body={})


class TestGitHubCodeSourceAdapter(unittest.TestCase):
    """Comprehensive test suite for GitHubCodeSourceAdapter."""

    def setUp(self):
        self.owner = "test-org"
        self.repo = "test-repo"
        self.token = "ghp_secret_token_value_12345"
        self.valid_sha_1 = "a" * 40
        self.valid_sha_2 = "b" * 40
        self.commit_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{self.valid_sha_1}"

    # 1. Port contract and representation
    def test_implements_code_source_port(self):
        transport = FakeTransport()
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        self.assertIsInstance(adapter, CodeSourcePort)
        self.assertIsNone(adapter.last_resolved_commit_sha)

    def test_safe_repr_hides_token(self):
        transport = FakeTransport()
        adapter = GitHubCodeSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            token=self.token,
            branch="main",
            transport=transport,
        )
        rep = repr(adapter)
        self.assertNotIn(self.token, rep)
        self.assertIn(self.owner, rep)
        self.assertIn(self.repo, rep)

    # 2. Configuration validation
    def test_configuration_validation_errors(self):
        transport = FakeTransport()

        # Invalid owner/repo
        with self.assertRaises(SourceError) as cm:
            GitHubCodeSourceAdapter(owner="", repo=self.repo, transport=transport)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        with self.assertRaises(SourceError) as cm:
            GitHubCodeSourceAdapter(owner=self.owner, repo="repo/with/slash", transport=transport)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        with self.assertRaises(SourceError) as cm:
            GitHubCodeSourceAdapter(owner="owner\ninjection", repo=self.repo, transport=transport)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        # Invalid branch
        with self.assertRaises(SourceError) as cm:
            GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, branch="../traversal", transport=transport)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        # Token with control characters or newlines
        with self.assertRaises(SourceError) as cm:
            GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, token="tok\r\nen", transport=transport)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        # Credential substring in owner, repo, or branch
        with self.assertRaises(SourceError) as cm:
            GitHubCodeSourceAdapter(owner=self.token, repo=self.repo, token=self.token, transport=transport)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        # Invalid caps
        with self.assertRaises(SourceError) as cm:
            GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, max_files=0, transport=transport)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        with self.assertRaises(SourceError) as cm:
            GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, max_files=3001, transport=transport)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        with self.assertRaises(SourceError) as cm:
            GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, max_files=True, transport=transport)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

    def test_credential_in_project_id_rejected(self):
        transport = FakeTransport()
        adapter = GitHubCodeSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            token=self.token,
            transport=transport,
        )
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs(f"project_{self.token}_bad")
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

    # 3. Explicit commit SHA retrieval and verification
    def test_explicit_sha_retrieval_success(self):
        patch = "@@ -1,2 +1,3 @@\n def hello():\n-    pass\n+    x = 1\n+    return x\n"
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {
                            "filename": "src/hello.py",
                            "status": "modified",
                            "patch": patch,
                        }
                    ],
                },
            )
        })

        adapter = GitHubCodeSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            token=self.token,
            transport=transport,
        )

        snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(snippets), 1)
        snip = snippets[0]
        self.assertEqual(snip.repository, f"{self.owner}/{self.repo}")
        self.assertEqual(snip.commit_sha, self.valid_sha_1)
        self.assertEqual(snip.file_path, "src/hello.py")
        self.assertEqual(snip.diff_side, "NEW")
        # Line mapping: line 1 is context ('def hello():'), lines 2, 3 are added
        self.assertEqual(snip.lines, [1, 2, 3])
        self.assertEqual(snip.content, "def hello():\n    x = 1\n    return x\n")
        self.assertEqual(adapter.last_resolved_commit_sha, self.valid_sha_1)
        self.assertTrue(adapter.last_collection_complete)
        self.assertEqual(adapter.unavailable_file_paths, [])

        # Check call headers
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {self.token}")
        self.assertEqual(call["headers"]["Accept"], "application/vnd.github+json")
        self.assertEqual(call["headers"]["X-GitHub-Api-Version"], "2022-11-28")
        self.assertEqual(call["params"], {"per_page": 100, "page": 1})

    def test_explicit_sha_abbreviated_or_non_hex_rejected(self):
        transport = FakeTransport()
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)

        # 7-char short SHA
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha="a" * 7)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        # Branch name passed as commit_sha
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha="feature-branch")
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        # Non-hex characters
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha="g" * 40)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

    def test_explicit_sha_mismatch_with_response_rejected(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_2,  # Differs from requested valid_sha_1
                    "files": [],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)
        self.assertIsNone(adapter.last_resolved_commit_sha)

    # 4. SHA omitted: configured branch vs default_branch resolution
    def test_sha_omitted_resolves_configured_branch_tip_once(self):
        branch_name = "release-1.0"
        branch_commit_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{branch_name}"
        transport = FakeTransport({
            branch_commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {
                            "filename": "README.md",
                            "status": "modified",
                            "patch": "@@ -1,1 +1,1 @@\n-# v0.9\n+# v1.0\n",
                        }
                    ],
                },
            )
        })

        adapter = GitHubCodeSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            branch=branch_name,
            transport=transport,
        )

        snippets1 = adapter.get_diffs("proj1")
        self.assertEqual(len(snippets1), 1)
        self.assertEqual(adapter.last_resolved_commit_sha, self.valid_sha_1)
        self.assertEqual(len(transport.calls), 1)

        # Repeated call reuses cached resolution and does not make another HTTP request
        snippets2 = adapter.get_diffs("proj1")
        self.assertEqual(len(snippets2), 1)
        self.assertEqual(len(transport.calls), 1)

    def test_sha_omitted_resolves_default_branch_when_branch_omitted(self):
        repo_url = f"https://api.github.com/repos/{self.owner}/{self.repo}"
        default_branch_commit_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/main"

        transport = FakeTransport({
            repo_url: HttpResponse(
                status_code=200,
                body={"default_branch": "main"},
            ),
            default_branch_commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_2,
                    "files": [],
                },
            ),
        })

        adapter = GitHubCodeSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            branch=None,
            transport=transport,
        )

        snippets = adapter.get_diffs("proj1")
        self.assertEqual(snippets, [])
        self.assertEqual(adapter.last_resolved_commit_sha, self.valid_sha_2)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0]["url"], repo_url)
        self.assertEqual(transport.calls[1]["url"], default_branch_commit_url)

    # 5. Deterministic pagination with pinned SHA
    def test_pagination_pinned_sha_and_deterministic_pages(self):
        branch = "main"
        branch_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{branch}"
        page2_pinned_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{self.valid_sha_1}"

        page1_files = [
            {"filename": f"file_{i}.txt", "status": "added", "patch": f"@@ -0,0 +1,1 @@\n+line {i}\n"}
            for i in range(100)
        ]
        page2_files = [
            {"filename": f"file_{i}.txt", "status": "added", "patch": f"@@ -0,0 +1,1 @@\n+line {i}\n"}
            for i in range(100, 125)
        ]

        transport = FakeTransport()

        def dynamic_get(url, params=None, headers=None, timeout_seconds=None):
            call_record = {"url": url, "params": params, "headers": headers}
            transport.calls.append(call_record)
            page = (params or {}).get("page", 1)
            if page == 1:
                return HttpResponse(
                    status_code=200,
                    body={"sha": self.valid_sha_1, "files": page1_files},
                    headers={"link": f'<{page2_pinned_url}?page=2&per_page=100>; rel="next"'},
                )
            elif page == 2:
                return HttpResponse(
                    status_code=200,
                    body={"sha": self.valid_sha_1, "files": page2_files},
                    headers={},
                )
            return HttpResponse(status_code=404, body={})

        transport.get = dynamic_get

        adapter = GitHubCodeSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            branch=branch,
            transport=transport,
        )

        snippets = adapter.get_diffs("proj1")
        self.assertEqual(len(snippets), 125)
        self.assertEqual(adapter.last_resolved_commit_sha, self.valid_sha_1)

        # Page 1 was requested on branch, page 2 was requested strictly with pinned SHA
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0]["url"], branch_url)
        self.assertEqual(transport.calls[0]["params"], {"per_page": 100, "page": 1})
        self.assertEqual(transport.calls[1]["url"], page2_pinned_url)
        self.assertEqual(transport.calls[1]["params"], {"per_page": 100, "page": 2})

    def test_pagination_page2_sha_mismatch_rejected(self):
        transport = FakeTransport()

        def dynamic_get(url, params=None, headers=None, timeout_seconds=None):
            page = (params or {}).get("page", 1)
            if page == 1:
                return HttpResponse(
                    status_code=200,
                    body={
                        "sha": self.valid_sha_1,
                        "files": [{"filename": "f1.txt", "status": "added", "patch": "@@ -0,0 +1,1 @@\n+hi\n"}] * 100,
                    },
                    headers={"link": f'<{self.commit_url}?page=2&per_page=100>; rel="next"'},
                )
            return HttpResponse(
                status_code=200,
                body={"sha": self.valid_sha_2, "files": []},  # SHA mismatch on page 2!
            )

        transport.get = dynamic_get
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)

        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)
        self.assertIsNone(adapter.last_resolved_commit_sha)

    def test_duplicate_file_across_pages_rejected(self):
        transport = FakeTransport()

        def dynamic_get(url, params=None, headers=None, timeout_seconds=None):
            page = (params or {}).get("page", 1)
            if page == 1:
                return HttpResponse(
                    status_code=200,
                    body={
                        "sha": self.valid_sha_1,
                        "files": [
                            {"filename": "duplicate.txt", "status": "added", "patch": "@@ -0,0 +1,1 @@\n+line\n"}
                        ] + [
                            {"filename": f"f_{i}.txt", "status": "added", "patch": "@@ -0,0 +1,1 @@\n+line\n"}
                            for i in range(99)
                        ],
                    },
                    headers={"link": f'<{self.commit_url}?page=2&per_page=100>; rel="next"'},
                )
            return HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {"filename": "duplicate.txt", "status": "modified", "patch": "@@ -1,1 +1,2 @@\n line\n+new\n"}
                    ],
                },
            )

        transport.get = dynamic_get
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)

    def test_empty_intermediate_page_rejected(self):
        transport = FakeTransport()

        def dynamic_get(url, params=None, headers=None, timeout_seconds=None):
            page = (params or {}).get("page", 1)
            if page == 1:
                return HttpResponse(
                    status_code=200,
                    body={
                        "sha": self.valid_sha_1,
                        "files": [
                            {"filename": f"f_{i}.txt", "status": "added", "patch": "@@ -0,0 +1,1 @@\n+line\n"}
                            for i in range(100)
                        ],
                    },
                    headers={"link": f'<{self.commit_url}?page=2&per_page=100>; rel="next"'},
                )
            return HttpResponse(status_code=200, body={"sha": self.valid_sha_1, "files": []})

        transport.get = dynamic_get
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)

    # 6. Strict patch parser and line anchoring
    def test_patch_parsing_multihunk_and_gaps(self):
        patch = (
            "@@ -10,2 +10,3 @@\n"
            " context 10\n"
            "+added 11\n"
            " context 12\n"
            "@@ -50,2 +51,2 @@\n"
            "-old 51\n"
            "+new 51\n"
            " context 52\n"
        )
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": "pkg/mod.py", "status": "modified", "patch": patch}],
                },
            )
        })

        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(snippets), 1)
        snip = snippets[0]

        # Lines must be exact: [10, 11, 12, 51, 52] - no fake numbers across the gap!
        self.assertEqual(snip.lines, [10, 11, 12, 51, 52])
        self.assertEqual(
            snip.content,
            "context 10\nadded 11\ncontext 12\nnew 51\ncontext 52\n",
        )

        # Confirm exact 1-to-1 match between splitlines and lines
        content_lines = snip.content.splitlines()
        self.assertEqual(len(content_lines), len(snip.lines))

    def test_patch_parsing_zero_counts_and_no_newline_marker(self):
        patch = (
            "@@ -1,2 +0,0 @@\n"
            "-only old 1\n"
            "-only old 2\n"
            "\\ No newline at end of file\n"
        )
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": "deleted_code.py", "status": "modified", "patch": patch}],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        # All lines deleted: no NEW lines, so no NEW snippet emitted
        self.assertEqual(snippets, [])

    def test_patch_hunk_counter_mismatch_rejected(self):
        patch = (
            "@@ -1,2 +1,3 @@\n"
            " context\n"
            "+added 1\n"
            # Claims new_count=3, but only 2 lines provided!
        )
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": "bad.py", "status": "modified", "patch": patch}],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)

    def test_patch_combined_diff_rejected(self):
        patch = "@@@ -1,2 -1,2 +1,2 @@@\n+combined\n"
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": "merge.py", "status": "modified", "patch": patch}],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)

    # 7. Deleted, renamed, and missing-patch files
    def test_deleted_file_semantics(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {
                            "filename": "old_file.py",
                            "status": "removed",
                            "patch": "@@ -1,2 +0,0 @@\n-deleted line 1\n-deleted line 2\n",
                        }
                    ],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(snippets, [])

        evidence = adapter.get_evidence("proj1", commit_sha=self.valid_sha_1)
        # Must have commit evidence and file diff evidence
        file_ev = [e for e in evidence if e.provenance.file_path == "old_file.py"]
        self.assertEqual(len(file_ev), 1)
        self.assertEqual(file_ev[0].provenance.payload["status"], "removed")
        self.assertTrue(file_ev[0].provenance.payload["patch_available"])
        self.assertEqual(file_ev[0].provenance.payload["reason"], "file_removed")
        self.assertTrue(adapter.last_collection_complete)
        self.assertEqual(adapter.unavailable_file_paths, [])

    def test_missing_patch_binary_or_large_evidence(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {
                            "filename": "images/logo.png",
                            "status": "added",
                            "patch": None,  # Binary file / large diff
                        },
                        {
                            "filename": "docs/readme.txt",
                            "status": "modified",
                            "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                        },
                    ],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        # Only the file with available patch produces a snippet
        self.assertEqual(len(snippets), 1)
        self.assertEqual(snippets[0].file_path, "docs/readme.txt")

        # Adapter tracks incomplete collection due to unavailable patch
        self.assertFalse(adapter.last_collection_complete)
        self.assertEqual(adapter.unavailable_file_paths, ["images/logo.png"])

        evidence = adapter.get_evidence("proj1", commit_sha=self.valid_sha_1)
        img_ev = [e for e in evidence if e.provenance.file_path == "images/logo.png"]
        self.assertEqual(len(img_ev), 1)
        self.assertFalse(img_ev[0].provenance.payload["patch_available"])
        self.assertEqual(img_ev[0].provenance.payload["reason"], "patch_unavailable")

    # 8. Finding validation integration
    def test_semantic_validator_anchors_with_adapter_snippets(self):
        patch = "@@ -20,2 +20,3 @@\n context\n+vulnerable_query()\n context2\n"
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": "src/api.py", "status": "modified", "patch": patch}],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        evidence = adapter.get_evidence("proj1", commit_sha=self.valid_sha_1)
        ev_ids = {e.provenance.id for e in evidence}

        # Create finding anchored at line 21 (vulnerable_query)
        file_ev_id = [e.provenance.id for e in evidence if e.provenance.file_path == "src/api.py"][0]
        finding = CandidateFinding(
            id="FINDING-01",
            rule_id="SEC-01",
            claim="SQL injection in query",
            severity=FindingSeverity.high,
            evidence_ids=[file_ev_id],
            commit_sha=self.valid_sha_1,
            file_path="src/api.py",
            line=21,
            confidence=0.9,
            requires_verification=True,
        )

        errors = SemanticValidator.validate_candidate_findings(
            findings=[finding],
            available_snippets=snippets,
            valid_rule_ids={"SEC-01"},
            available_evidence_ids=ev_ids,
        )
        self.assertEqual(errors, [])

    # 9. Path validation and security
    def test_unsafe_paths_rejected(self):
        for bad_path in ["/etc/passwd", "../escape.py", "dir/../../escape.py", "C:\\windows\\win.ini"]:
            transport = FakeTransport({
                self.commit_url: HttpResponse(
                    status_code=200,
                    body={
                        "sha": self.valid_sha_1,
                        "files": [{"filename": bad_path, "status": "added", "patch": "@@ -0,0 +1,1 @@\n+x\n"}],
                    },
                )
            })
            adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
            with self.assertRaises(SourceError) as cm:
                adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
            self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)

    # 10. Bounds and budgets
    def test_max_files_ceiling_exceeded_raises_limit_exceeded(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    # 3000 files reaches the ceiling!
                    "files": [
                        {"filename": f"f_{i}.txt", "status": "added", "patch": "@@ -0,0 +1,1 @@\n+x\n"}
                        for i in range(MAX_GITHUB_FILES_CEILING)
                    ],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.limit_exceeded)

    def test_configured_max_files_cap_raises_limit_exceeded(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {"filename": f"f_{i}.txt", "status": "added", "patch": "@@ -0,0 +1,1 @@\n+x\n"}
                        for i in range(15)
                    ],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, max_files=10, transport=transport)
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.limit_exceeded)

    def test_max_text_length_and_lines_cap(self):
        long_patch = "@@ -0,0 +1,1 @@\n+" + ("x" * 200) + "\n"
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": "long.txt", "status": "added", "patch": long_patch}],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            max_text_length=100,  # 200 exceeds 100!
            transport=transport,
        )
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.limit_exceeded)

    # 11. Malicious / invalid Link header
    def test_malicious_foreign_link_header_rejected(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {"filename": f"f_{i}.txt", "status": "added", "patch": "@@ -0,0 +1,1 @@\n+x\n"}
                        for i in range(100)
                    ],
                },
                # Foreign malicious host in Link next!
                headers={"link": '<https://evil.com/commits?page=2>; rel="next"'},
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)

    # 12. Credential echo protection (source code rejected safely, NOT redacted into fake code)
    def test_credential_echo_in_source_code_rejected_safely(self):
        patch_with_secret = f"@@ -0,0 +1,1 @@\n+API_KEY = \"{self.token}\"\n"
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": "secret.py", "status": "added", "patch": patch_with_secret}],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            token=self.token,
            transport=transport,
        )
        # Source code with secret must be REJECTED, NOT redacted!
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)

    def test_credential_echo_in_filename_rejected(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": f"src/{self.token}.py", "status": "added", "patch": "@@ -0,0 +1,1 @@\n+x\n"}],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            token=self.token,
            transport=transport,
        )
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)

    # 13. Transport error mapping
    def test_transport_error_categories(self):
        for cat in [
            SourceErrorCategory.authentication,
            SourceErrorCategory.permission_denied,
            SourceErrorCategory.rate_limit,
            SourceErrorCategory.not_found,
            SourceErrorCategory.timeout,
            SourceErrorCategory.network,
        ]:
            transport = FakeTransport({
                self.commit_url: SourceError(SourceProvider.github, cat),
            })
            adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
            with self.assertRaises(SourceError) as cm:
                adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
            self.assertEqual(cm.exception.category, cat)

    # 14. Cache immutability, lifecycle, and no partial publication
    def test_cache_immutability_and_deep_copy(self):
        patch = "@@ -0,0 +1,1 @@\n+val\n"
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": "f.py", "status": "added", "patch": patch}],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets1 = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        # Mutate returned list and object
        snippets1[0].lines.append(999)

        snippets2 = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(snippets2[0].lines, [1])

    def test_cache_invalidation_and_refresh(self):
        patch = "@@ -0,0 +1,1 @@\n+val\n"
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": "f.py", "status": "added", "patch": patch}],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(transport.calls), 1)

        adapter.refresh("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(transport.calls), 2)

    def test_no_partial_publication_on_failure(self):
        transport = FakeTransport({
            self.commit_url: SourceError(SourceProvider.github, SourceErrorCategory.network),
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError):
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)

        self.assertIsNone(adapter.last_resolved_commit_sha)
        self.assertFalse(adapter.last_collection_complete)
        # Attempting get_evidence without prior successful collection raises not_found
        with self.assertRaises(SourceError) as cm:
            adapter.get_evidence("proj1")
        self.assertEqual(cm.exception.category, SourceErrorCategory.not_found)

    # 15. Additional edge cases and boundaries
    def test_empty_commit_returns_empty_snippets_and_commit_evidence(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(snippets, [])
        self.assertEqual(adapter.last_resolved_commit_sha, self.valid_sha_1)
        self.assertTrue(adapter.last_collection_complete)

        evidence = adapter.get_evidence("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].category, "commit")
        self.assertEqual(evidence[0].provenance.payload["file_count"], 0)
        self.assertEqual(evidence[0].provenance.payload["commit_sha"], self.valid_sha_1)

    def test_renamed_file_with_previous_filename(self):
        patch = "@@ -1,1 +1,1 @@\n-old\n+new\n"
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {
                            "filename": "new_name.py",
                            "previous_filename": "old_name.py",
                            "status": "renamed",
                            "patch": patch,
                        }
                    ],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(snippets), 1)
        self.assertEqual(snippets[0].file_path, "new_name.py")

    def test_sha_64_hex_supported(self):
        sha64 = "c" * 64
        sha64_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{sha64}"
        transport = FakeTransport({
            sha64_url: HttpResponse(
                status_code=200,
                body={
                    "sha": sha64,
                    "files": [],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets = adapter.get_diffs("proj1", commit_sha=sha64)
        self.assertEqual(snippets, [])
        self.assertEqual(adapter.last_resolved_commit_sha, sha64)

    def test_public_repo_omitted_token_no_auth_header(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={"sha": self.valid_sha_1, "files": []},
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, token=None, transport=transport)
        adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        call = transport.calls[0]
        self.assertNotIn("Authorization", call["headers"])
        self.assertEqual(call["headers"]["Accept"], "application/vnd.github+json")

    def test_hunk_non_increasing_line_numbers_rejected(self):
        # Hunk 2 starts at line 5, but hunk 1 already advanced past line 10
        overlapping_patch = (
            "@@ -10,1 +10,1 @@\n"
            "+first\n"
            "@@ -5,1 +5,1 @@\n"
            "+backwards\n"
        )
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [{"filename": "overlap.py", "status": "modified", "patch": overlapping_patch}],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)

    def test_link_header_cyclic_or_invalid_page_rejected(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {"filename": f"f_{i}.txt", "status": "added", "patch": "@@ -0,0 +1,1 @@\n+x\n"}
                        for i in range(100)
                    ],
                },
                # Points backward to page 1 instead of page 2!
                headers={"link": f'<{self.commit_url}?page=1&per_page=100>; rel="next"'},
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)

    def test_aggregate_total_bytes_budget_exceeded(self):
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={"sha": self.valid_sha_1, "files": []},
                bytes_received=5000,
            )
        })
        adapter = GitHubCodeSourceAdapter(
            owner=self.owner,
            repo=self.repo,
            max_total_bytes=1000,  # 5000 exceeds 1000!
            transport=transport,
        )
        with self.assertRaises(SourceError) as cm:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm.exception.category, SourceErrorCategory.limit_exceeded)

    # 16. Confirmed defect regression tests (Finding 1, 2, 3, 4)
    def test_cache_hit_synchronizes_public_metadata_aba(self):
        sha_a = "1" * 40
        sha_b = "2" * 40
        url_a = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{sha_a}"
        url_b = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{sha_b}"

        transport = FakeTransport({
            url_a: HttpResponse(
                status_code=200,
                body={
                    "sha": sha_a,
                    "files": [
                        {
                            "filename": "file_a.py",
                            "status": "modified",
                            "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                        }
                    ],
                },
            ),
            url_b: HttpResponse(
                status_code=200,
                body={
                    "sha": sha_b,
                    "files": [
                        {
                            "filename": "bin_b.bin",
                            "status": "added",
                            "patch": None,  # unavailable patch!
                        }
                    ],
                },
            ),
        })

        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)

        # 1. First query: Project A -> sha_a, complete, no unavailable files
        diffs_a1 = adapter.get_diffs("projA", commit_sha=sha_a)
        self.assertEqual(len(diffs_a1), 1)
        self.assertEqual(adapter.last_resolved_commit_sha, sha_a)
        self.assertTrue(adapter.last_collection_complete)
        self.assertEqual(adapter.unavailable_file_paths, [])

        # 2. Second query: Project B -> sha_b, incomplete, bin_b.bin unavailable
        diffs_b = adapter.get_diffs("projB", commit_sha=sha_b)
        self.assertEqual(len(diffs_b), 0)
        self.assertEqual(adapter.last_resolved_commit_sha, sha_b)
        self.assertFalse(adapter.last_collection_complete)
        self.assertEqual(adapter.unavailable_file_paths, ["bin_b.bin"])

        # 3. Third query: Project A (CACHE HIT via get_diffs)
        # Must synchronize public metadata back to Project A state!
        diffs_a2 = adapter.get_diffs("projA", commit_sha=sha_a)
        self.assertEqual(len(diffs_a2), 1)
        self.assertEqual(adapter.last_resolved_commit_sha, sha_a)
        self.assertTrue(adapter.last_collection_complete)
        self.assertEqual(adapter.unavailable_file_paths, [])

        # 4. Fourth query: Project B (CACHE HIT via get_evidence)
        # Must synchronize public metadata back to Project B state!
        ev_b = adapter.get_evidence("projB", commit_sha=sha_b)
        self.assertTrue(len(ev_b) > 0)
        self.assertEqual(adapter.last_resolved_commit_sha, sha_b)
        self.assertFalse(adapter.last_collection_complete)
        self.assertEqual(adapter.unavailable_file_paths, ["bin_b.bin"])

        # Verify only 2 HTTP requests were made across all queries
        self.assertEqual(len(transport.calls), 2)

        # Verify unavailable_file_paths list mutation does not alias internal cache
        unavail = adapter.unavailable_file_paths
        unavail.append("tampered.txt")
        self.assertEqual(adapter.unavailable_file_paths, ["bin_b.bin"])

    def test_hunk_header_huge_digits_raises_sanitized_source_error(self):
        huge_start = "9" * 4500
        huge_count = "8" * 4500
        patch_huge_start = f"@@ -{huge_start},1 +1,1 @@\n context\n"
        patch_huge_count = f"@@ -1,1 +1,{huge_count} @@\n context\n"

        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {"filename": "huge_start.py", "status": "modified", "patch": patch_huge_start}
                    ],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)

        # Huge start group should raise SourceError(github, malformed_payload), not ValueError
        with self.assertRaises(SourceError) as cm1:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm1.exception.category, SourceErrorCategory.malformed_payload)
        self.assertEqual(cm1.exception.provider, SourceProvider.github)
        self.assertNotIn(huge_start, str(cm1.exception))
        self.assertNotIn(huge_start, repr(cm1.exception))

        # Huge count group should also raise SourceError(github, malformed_payload)
        transport.responses[self.commit_url] = HttpResponse(
            status_code=200,
            body={
                "sha": self.valid_sha_1,
                "files": [
                    {"filename": "huge_count.py", "status": "modified", "patch": patch_huge_count}
                ],
            },
        )
        adapter.invalidate_cache()
        with self.assertRaises(SourceError) as cm2:
            adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(cm2.exception.category, SourceErrorCategory.malformed_payload)
        self.assertEqual(cm2.exception.provider, SourceProvider.github)
        self.assertNotIn(huge_count, str(cm2.exception))
        self.assertNotIn(huge_count, repr(cm2.exception))

    def test_patch_split_preserves_unicode_separators_and_crlf(self):
        # Source line contains vertical tab, form feed, next line, and line separator
        special_line = "value = 'vtab:\x0b formfeed:\x0c nextline:\x85 linesep:\u2028'"
        patch_with_unicode = (
            "@@ -10,0 +10,1 @@\n"
            f"+{special_line}\n"
        )
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {
                            "filename": "unicode_code.py",
                            "status": "modified",
                            "patch": patch_with_unicode,
                        }
                    ],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(snippets), 1)
        self.assertEqual(snippets[0].lines, [10])
        self.assertEqual(snippets[0].content, f"{special_line}\n")

        # CRLF source lines should preserve \r as part of line content across multiple hunks
        crlf_patch = (
            "@@ -1,1 +1,2 @@\n"
            " context line 1\r\n"
            "+added line 2\r\n"
            "@@ -20,0 +21,1 @@\n"
            "+added line 21\r\n"
        )
        transport.responses[self.commit_url] = HttpResponse(
            status_code=200,
            body={
                "sha": self.valid_sha_1,
                "files": [
                    {
                        "filename": "crlf.py",
                        "status": "modified",
                        "patch": crlf_patch,
                    }
                ],
            },
        )
        adapter.invalidate_cache()
        crlf_snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(crlf_snippets), 1)
        self.assertEqual(crlf_snippets[0].lines, [1, 2, 21])
        self.assertEqual(
            crlf_snippets[0].content,
            "context line 1\r\nadded line 2\r\nadded line 21\r\n",
        )

        # Patch without terminal newline delimiter
        patch_no_final_nl = "@@ -10,0 +10,1 @@\n" + f"+{special_line}"
        transport.responses[self.commit_url] = HttpResponse(
            status_code=200,
            body={
                "sha": self.valid_sha_1,
                "files": [
                    {
                        "filename": "no_final_nl.py",
                        "status": "modified",
                        "patch": patch_no_final_nl,
                    }
                ],
            },
        )
        adapter.invalidate_cache()
        no_nl_snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(no_nl_snippets), 1)
        self.assertEqual(no_nl_snippets[0].lines, [10])
        self.assertEqual(no_nl_snippets[0].content, f"{special_line}\n")

    def test_no_newline_marker_strict_matching(self):
        # Legitimate standard marker is accepted (LF and CRLF)
        valid_patch = (
            "@@ -0,0 +1,1 @@\n"
            "+unterminated line\n"
            "\\ No newline at end of file\n"
        )
        transport = FakeTransport({
            self.commit_url: HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {"filename": "unterminated.txt", "status": "added", "patch": valid_patch}
                    ],
                },
            )
        })
        adapter = GitHubCodeSourceAdapter(owner=self.owner, repo=self.repo, transport=transport)
        snippets = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(snippets), 1)
        self.assertEqual(snippets[0].lines, [1])

        # Legitimate standard marker with CRLF
        valid_patch_crlf = (
            "@@ -0,0 +1,1 @@\n"
            "+unterminated line\r\n"
            "\\ No newline at end of file\r\n"
        )
        transport.responses[self.commit_url] = HttpResponse(
            status_code=200,
            body={
                "sha": self.valid_sha_1,
                "files": [
                    {"filename": "unterminated_crlf.txt", "status": "added", "patch": valid_patch_crlf}
                ],
            },
        )
        adapter.invalidate_cache()
        snippets_crlf = adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
        self.assertEqual(len(snippets_crlf), 1)
        self.assertEqual(snippets_crlf[0].lines, [1])

        # Malformed backslash markers should be rejected as malformed_payload
        for bad_marker in [
            "\\ No newline at end of file with extra suffix",
            "\\ No newline at end of file.",
            "\\ unknown marker",
            "\\",
        ]:
            bad_patch = (
                "@@ -0,0 +1,1 @@\n"
                "+line\n"
                f"{bad_marker}\n"
            )
            transport.responses[self.commit_url] = HttpResponse(
                status_code=200,
                body={
                    "sha": self.valid_sha_1,
                    "files": [
                        {"filename": "bad_marker.txt", "status": "added", "patch": bad_patch}
                    ],
                },
            )
            adapter.invalidate_cache()
            with self.assertRaises(SourceError) as cm:
                adapter.get_diffs("proj1", commit_sha=self.valid_sha_1)
            self.assertEqual(cm.exception.category, SourceErrorCategory.malformed_payload)


if __name__ == "__main__":
    unittest.main()
