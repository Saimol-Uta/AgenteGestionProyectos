import type {
  EvidenceProvenance,
  ExecuteProposalRequest,
  FindingDecisionStatus,
  FindingSummary,
  RepositoryHistoryFilters,
  RepositoryMetricsView,
  RepositoryResultsCollection,
  RunFindingFilters,
} from "./types";
import type { api } from "./client";

type Assert<T extends true> = T;
type Equal<Left, Right> = (<Value>() => Value extends Left ? 1 : 2) extends (<Value>() => Value extends Right ? 1 : 2) ? true : false;

/** Existing evidence payloads may include, omit or explicitly clear a commit SHA. */
export type EvidenceCommitRemainsOptional = Assert<{} extends Pick<EvidenceProvenance, "commit_sha"> ? true : false>;
export type EvidenceCommitAcceptsPreviousValues = Assert<string | null | undefined extends EvidenceProvenance["commit_sha"] ? true : false>;
export type UnifiedFindingDecisionStatuses = Assert<Equal<FindingDecisionStatus, "approved" | "rejected" | "changes_requested" | "pending">>;
export type ExecutionDecisionRequiresReason = Assert<Equal<{} extends Pick<ExecuteProposalRequest, "reason"> ? true : false, false>>;
export type RepositoryCoveragePreservesUnknownAndZero = Assert<Equal<RepositoryMetricsView["coverage_percentage"], number | null>>;
export type RepositoryCollectionCarriesConfiguredSiblings = Assert<Equal<RepositoryResultsCollection["repositories"][number]["repository_id"], string>>;
export type FindingIdentityRemainsWhole = Assert<Equal<FindingSummary["finding_id"], string>>;
export type FindingRepositoryIdentityCanBeAbsent = Assert<Equal<{} extends Pick<FindingSummary, "repository_id"> ? true : false, true>>;
export type RepositoryFilterCarriesActualAttempt = Assert<Equal<RepositoryHistoryFilters["attempt"], number | undefined>>;
export type RunFindingFilterIsRepositoryOnly = Assert<Equal<keyof RunFindingFilters, "repository_id">>;
export type FindingEndpointAcceptsBackendFilter = Assert<Equal<Exclude<Parameters<typeof api.getRunFindings>[3], undefined>, RunFindingFilters>>;
