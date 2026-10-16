# Standalone CI metrics artifact validation

The T3 core downloads and validates one already-selected GitHub Actions artifact. It binds the document to caller-supplied repository, source SHA, run, attempt, metadata, and final workflow status. It is not wired into Actions run selection or MultiRepo collection; that integration remains T4.

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

## T4 integration hook (pending)

T4 owns deterministic run/attempt selection and `GitHubActionsSourceAdapter`/factory integration. It should:

1. Select one final Actions run and attempt without falling back to an older successful attempt.
2. Prove the selected run's source SHA against the configured target. If the selected run exposes only a synthetic execution SHA or lacks a source binding, do not repin; report insufficient evidence.
3. Select the artifact metadata whose name exactly matches `ci-quality-<SHA>-<run_id>-<attempt>` and pass the GitHub artifact object to `CiArtifactMetadata.from_github_payload`.
4. Construct `BoundedBinaryGetTransport` using the artifact limits and wrap it in the existing `BudgetedHttpGetTransport` with the already-active `CollectionBudgetLedger`, `github_actions` provider, and the selected `RepositoryIdentity`. Do not call `begin_repository` again. `BudgetedHttpGetTransport` passes `params=None`; the binary port accepts that compatibility argument and rejects nonempty params. Each redirect invokes the wrapper again, charging one call per GET/hop.
5. Pass `CiMetricsValidationContext` with explicit repository/source SHA/run/attempt/final conclusion. Use `result.certifies_metrics` before exposing values; a green workflow without a document is not complete evidence.

T3 does not modify `actions.py`, factories, orchestration, APIs, storage, UI, producers, or workflows. The backend/frontend fixture files are synthetic examples of their respective contract shapes, not live artifacts. Deterministic tests use in-memory responses/ZIP bytes and fake transports, deny socket `connect`/`connect_ex` before application imports, and set `EXTERNAL_WRITES_ENABLED=false`; no live GitHub credential is needed.
