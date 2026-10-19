import { useProposals, useQuality } from "../../api/queries";

export function useDecisionSummary(projectId: string | null) {
  const proposals = useProposals(projectId, 1, 1);
  const quality = useQuality(projectId);
  return {
    proposals: proposals.data?.availability === "available" ? proposals.data.pending_actionable_count ?? null : null,
    findings: quality.data?.availability === "available" && quality.data.finding_counts
      ? Math.max(0, quality.data.finding_counts.candidate - quality.data.finding_counts.rejected)
      : null,
    confirmedFindings: quality.data?.availability === "available" ? quality.data.finding_counts?.confirmed ?? null : null,
  };
}
