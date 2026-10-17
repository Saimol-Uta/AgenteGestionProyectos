# Standalone CI metrics artifact validation

The T3 core downloads and validates one already-selected GitHub Actions artifact. T4 wires it into the real MultiRepo Actions/factory path, binding each document to its repository, fixed source SHA, selected run and attempt, metadata, and final workflow status. Legacy singleton and fixture collection keep their prior paths.

## Quick path

```python
from agente_gestion.adapters.sources.artifacts import CiMetricsArtifactValidator
from agente_gestion.domain.ci_metrics import (
    CiArtifactMetadata,
    CiMetricsValidationContext,
    CiWorkflowStatus,
)

context = CiMetricsValidationContext(
    repository="owner/repository",
    target_source_sha="<full lowercase source SHA>",
    workflow_run_id="123456789",
    workflow_run_attempt="1",
    workflow_status=CiWorkflowStatus.success,
)
metadata = CiArtifactMetadata.from_github_payload(github_artifact_object)
result = CiMetricsArtifactValidator().validate(context, metadata, token=github_token)

if result.certifies_metrics:
    metrics = result.metrics  # nullable values remain None; zero remains zero
```

The context is authoritative input, not something this core guesses. Run and attempt are decimal strings. `CiArtifactMetadata.from_github_payload` retains only GitHub's artifact ID/name/expiry and selected workflow identity fields; it ignores `archive_download_url` and other metadata URLs. A missing `workflow_run.head_sha` returns `source_sha_unproven`; a different head SHA, repository, artifact name, or run/attempt returns `identity_mismatch`. Neither result fetches or returns metrics.

## Download and trust boundary

- The first URL is constructed as `https://api.github.com/repos/{expected owner}/{expected repo}/actions/artifacts/{artifact ID}/zip`. Metadata cannot select a host or URL.
- Each request is one GET with redirects disabled, no retries, `trust_env=False`, raw identity-encoded reads, and a per-hop deadline. Only validated HTTPS origins are followed. Redirects are finite, loop-checked, and counted individually.
- The GitHub token is sent only to `api.github.com`. Cookies, proxy authorization, and unknown headers are discarded; cross-origin storage receives no GitHub authorization. URLs, signed queries, response bodies, and tokens are absent from result/exception representations.
- The default storage allow-list is five exact GitHub Actions Azure Blob origins (`productionresultssa0` through `productionresultssa4`). Callers that change it must supply exact vetted HTTPS origins; wildcard hosts, userinfo, non-443 ports, IP/local hosts, and other schemes are rejected.
- ZIP contents stay in memory; nothing is extracted. A bounded archive may contain root reports or a safe directory prefix such as `reports/`, but exactly one regular file must have the basename `ci-metrics.json`. Safe auxiliary reports (including `unit-summary.json` and `integration-summary.json`) are permitted and never treated as metrics. Path segments are restricted to ASCII letters, digits, dot, underscore, and hyphen; empty, dot, dot-dot, absolute, and backslash paths are rejected. Every member and directory is checked for a safe path/type, supported compression, complete actual decompressor EOF, size agreement, and CRC before the metrics document is accepted. Duplicate/ambiguous metrics documents, duplicate paths, unsafe auxiliary paths, symlinks, encryption, ZIP64, unsupported compression, corrupt headers, and over-limit archives are rejected.
- `Content-Length`, ZIP `file_size`, and compressed-size claims never establish EOF or bound accepted output. Raw network reads and zlib output are counted from bytes actually read/decoded, with a bounded positive EOF probe at exact byte limits.
- JSON is decoded as strict UTF-8 and parsed in full. Duplicate keys, non-finite/underflowing numbers, integer or decimal literals over 1,024 characters, trailing documents, and malformed JSON are rejected. Validation always uses the fixed Draft 2020-12 v1 schema shipped in `ci_metrics_schema.py`, never a payload-provided schema or reference.

## Contract and outcomes

The Python schema and `schemas/ci-metrics.v1.schema.json` source copy match the official public schema (Git blob `09033371c03dd9458c45065b9fb1930bbff230ac`). The source copy is retained for review/provenance; runtime validation uses the Python module to avoid package-data loading fragility. `jsonschema>=4.18,<5` is a direct runtime dependency.

`CiMetricsArtifactResult` keeps independent flags for `schema_valid`, `semantics_valid`, `identity_bound`, and `workflow_status_matches`. It only exposes `metrics` when the document passes all checks. `outcome` is a typed reason, including missing/expired, permission/authentication, timeout/limit, redirect, archive/JSON/schema/semantic, identity, source-SHA, and workflow-status failures. `receipt` contains only GET-hop/status, redirect, compressed-byte, expanded-byte, and archive-entry counts.

| Rule | Result behavior |
|---|---|
| `status` | Represents the final whole-workflow outcome and must match Actions; it is not derived from test counts. Semantic relationships are checked for success, failure, and cancellation alike. |
| Suite counts | When a represented suite has all counts, `tests` must equal `failed + passed + cancelled + skipped + todo`. When both unit and integration suites are complete, any known top-level totals/failures must equal their sums. Incomplete suite values remain unknown and are not coerced to zero. |
| Successful workflow | Requires known top-level test and failure counts and no reported failure. If `test_suites` is present, both represented suites must be complete and successful. |
| Failure/cancellation | A schema- and identity-valid failure document may retain valid metrics, but `green_eligible` is false. Partial suite breakdowns are not rejected merely because unknown counts cannot be summed. |
| Coverage pair | A known `coverage_percentage`, including `0`, requires `coverage_metric: "lines"`; unknown coverage requires both fields to remain `null`. |
| Unknown metrics | `null` remains unknown; a known `0` remains zero. Coverage basis is any nonempty producer-specific description. |
| Optional fields | Frontend documents may omit `test_suites`; the validator never fabricates integration data. `e2e_status: not_implemented` is not E2E proof. |

Default limits are 8 MiB compressed, 1 MiB total expanded across all archive members (also the per-member ceiling), 1 MiB for the selected JSON document, 16 archive entries including directories and auxiliaries, three redirects, and 15 seconds overall. Supported ceilings are 64 MiB compressed/expanded, 16 MiB JSON, 128 entries, five redirects, and 30 seconds; callers may lower but not raise these ceilings. The archive-wide expansion receipt counts actual bytes from every validated member, not only `ci-metrics.json`.

## T4 MultiRepo integration

`create_analysis_composite_source` and `create_composite_source` construct `ArtifactEnabledCISource` for each real MultiRepo repository. That wrapper keeps the existing Actions run adapter, artifact validator, JSON metadata transport, and binary transport; metadata and binary GETs share the active project/repository budget ledger. The singleton factory and fixture-only paths remain unchanged.

| Decision | Contract |
|---|---|
| Workflow | A configured `quality_workflow_path` is authoritative per repository. Without one, exactly one known target workflow path must be derivable; competing or missing paths produce an explicit noncertifying `invalid_context` observation. |
| Run chronology | The Actions adapter's newest-first `(updated_at, run_id)` ordering selects the run. Attempt numbers are compared only among duplicate rows for that same run ID. Pending, unknown, or failed newest runs never fall back to an older green run. |
| Artifact list | The source requests canonical `/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts` pages with `per_page=100` and increasing page numbers. It does not follow remote-provided pagination URLs. |
| Completeness | Metadata is bounded to 10 pages/1,000 items, 2 MiB, and a five-second overall deadline, in addition to the shared HTTP, byte, and elapsed-time ledger. Counts and page lengths must prove completeness before any matching artifact is validated. |
| Ambiguity/truncation | Duplicate matches, inconsistent/incomplete pages, page/item/time limits, or shared-ledger exhaustion remain typed and noncertifying. A matching artifact on page one is not accepted while later pages could contain another match. |
| Public result | A selected `CIRunResult` may include optional typed `metrics_artifact` metadata with validation flags, outcome, artifact name/receipt, and producer metrics. `coverage_basis`, nullable values, and optional suite fields are preserved; absent metadata stays omitted for legacy serialization. The scoped result's evidence retains repository, SHA, run, and artifact provenance. |

For the producer document's optional `workflow_run_attempt`, absence remains `None`; it is not filled from the known Actions run attempt. Public validation still requires the selected run attempt to be known, binds the artifact name and other run identity to it, and checks a declared document attempt when present. Repository identity comparison is case-insensitive, matching GitHub semantics, while serialized metrics preserve the producer's original repository spelling. A different owner or repository remains invalid.

The validator still proves source SHA and full repository/run/attempt identity from artifact metadata and the validated document. It exposes metric values only when `result.certifies_metrics` is true; a green workflow without valid metrics is not complete evidence. It constructs the download endpoint from validated repository/artifact IDs and never trusts archive download URLs.

T3's backend/frontend fixtures are synthetic examples of the producer contract, not captured live artifacts. Deterministic tests use in-memory responses/ZIP bytes and fake transports, deny socket `connect`/`connect_ex` before application imports, and set `EXTERNAL_WRITES_ENABLED=false`; no live GitHub credential is needed.
