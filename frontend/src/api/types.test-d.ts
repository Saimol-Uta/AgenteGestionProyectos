import type { EvidenceProvenance, ExecuteProposalRequest, FindingDecisionStatus } from "./types";

type Assert<T extends true> = T;
type Equal<Left, Right> = (<Value>() => Value extends Left ? 1 : 2) extends (<Value>() => Value extends Right ? 1 : 2) ? true : false;

/** Existing evidence payloads may include, omit or explicitly clear a commit SHA. */
export type EvidenceCommitRemainsOptional = Assert<{} extends Pick<EvidenceProvenance, "commit_sha"> ? true : false>;
export type EvidenceCommitAcceptsPreviousValues = Assert<string | null | undefined extends EvidenceProvenance["commit_sha"] ? true : false>;
export type UnifiedFindingDecisionStatuses = Assert<Equal<FindingDecisionStatus, "approved" | "rejected" | "changes_requested" | "pending">>;
export type ExecutionDecisionRequiresReason = Assert<Equal<{} extends Pick<ExecuteProposalRequest, "reason"> ? true : false, false>>;
