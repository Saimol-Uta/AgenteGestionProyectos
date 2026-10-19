import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AuditEventDetail,
  DecideCodeFindingRequest,
  ExecuteProposalRequest,
  OutboxDetail,
  ProjectConfiguration,
  ProposalDetail,
  SendManagerEmailRequest,
  ReportDetail,
  ReportHumanDecision,
  RepositoryHistoryFilters,
  RunFindingFilters,
  TaskQuery,
  WeeklyCycleJob,
} from "./types";

export const queryKeys = {
  health: ["system", "health"] as const,
  session: ["auth", "session"] as const,
  projects: (page: number, pageSize: number) => ["projects", page, pageSize] as const,
  project: (projectId: string) => ["project", projectId] as const,
  summary: (projectId: string, captureId?: string) => ["summary", projectId, captureId ?? null] as const,
  tasks: (projectId: string, filters: TaskQuery) => ["tasks", projectId, filters] as const,
  task: (projectId: string, taskId: string, captureId?: string) => ["task", projectId, taskId, captureId ?? null] as const,
  history: (projectId: string) => ["history", projectId] as const,
  comparison: (projectId: string, before: string, after: string) => ["comparison", projectId, before, after] as const,
  executive: (projectId: string) => ["executive-summary", projectId] as const,
  quality: (projectId: string) => ["quality", projectId] as const,
  policies: (projectId: string) => ["policies", projectId] as const,
  repositoryResults: (
    projectId: string,
    filters: RepositoryHistoryFilters,
    page: number,
    pageSize: number,
    scopeRunId?: string | null,
  ) => ["repository-results", projectId, page, pageSize, filters, scopeRunId === undefined
    ? { scope: "project" } : { scope: "run", runId: scopeRunId }] as const,
  runs: (projectId: string, page: number, pageSize: number) => ["runs", projectId, page, pageSize] as const,
  run: (runId: string, projectId?: string | null) => projectId
    ? ["run", projectId, runId] as const : ["run", runId] as const,
  runStages: (runId: string, projectId?: string | null) => projectId
    ? ["run-stages", projectId, runId] as const : ["run-stages", runId] as const,
  findings: (
    runId: string,
    page: number,
    pageSize: number,
    projectId?: string | null,
    filters: RunFindingFilters = {},
  ) => projectId !== undefined || Object.keys(filters).length > 0
    ? ["findings", projectId ?? null, runId, page, pageSize, filters] as const
    : ["findings", runId, page, pageSize] as const,
  finding: (runId: string, findingId: string, projectId?: string | null, repositoryId?: string | null) =>
    projectId !== undefined || repositoryId !== undefined
      ? ["finding", projectId ?? null, runId, repositoryId ?? null, findingId] as const
      : ["finding", runId, findingId] as const,
  evidence: (runId: string, page: number, pageSize: number, projectId?: string | null) => projectId
    ? ["run-evidence", projectId, runId, page, pageSize] as const : ["run-evidence", runId, page, pageSize] as const,
  analysisJobs: (projectId: string, page: number, pageSize: number) => ["analysis-jobs", projectId, page, pageSize] as const,
  analysisJob: (jobId: string) => ["analysis-job", jobId] as const,
  proposals: (projectId: string, page: number, pageSize: number) => ["proposals", projectId, page, pageSize] as const,
  proposal: (projectId: string, proposalId: string) => ["proposal", projectId, proposalId] as const,
  reports: (projectId: string, page: number, pageSize: number, decision: string) => ["reports", projectId, page, pageSize, decision] as const,
  report: (projectId: string, reportId: string) => ["report", projectId, reportId] as const,
  audit: (projectId: string, page: number, pageSize: number, eventType: string, since: string, until: string) => ["audit", projectId, page, pageSize, eventType, since, until] as const,
  auditEvent: (projectId: string, eventId: string) => ["audit-event", projectId, eventId] as const,
  schedule: (projectId: string) => ["schedule", projectId] as const,
  outbox: (projectId: string, page: number, pageSize: number) => ["schedule-outbox", projectId, page, pageSize] as const,
  outboxDetail: (projectId: string, deliveryId: string) => ["outbox-detail", projectId, deliveryId] as const,
  weeklyCycleJobs: (projectId: string, page: number, pageSize: number) => ["weekly-cycle-jobs", projectId, page, pageSize] as const,
  weeklyCycleJob: (projectId: string, jobId: string) => ["weekly-cycle-job", projectId, jobId] as const,
  projectConfiguration: (projectId: string) => ["project-configuration", projectId] as const,
};

export function useProjects(page = 1, pageSize = 100) {
  return useQuery({
    queryKey: queryKeys.projects(page, pageSize),
    queryFn: () => api.getProjects(page, pageSize),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useSystemHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: api.getHealth,
    staleTime: 30_000,
    retry: 1,
    refetchInterval: 60_000,
  });
}

export function useProject(projectId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.project(projectId ?? ""),
    queryFn: () => api.getProject(projectId!),
    enabled: Boolean(projectId) && enabled,
    staleTime: 60_000,
  });
}

export function useProjectSummary(projectId: string | null) {
  return useQuery({
    queryKey: queryKeys.summary(projectId ?? ""),
    queryFn: () => api.getSummary(projectId!),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });
}

export function useCaptureHistory(projectId: string | null) {
  return useQuery({
    queryKey: queryKeys.history(projectId ?? ""),
    queryFn: () => api.getHistory(projectId!, 1, 100),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });
}

export function useExecutiveSummary(projectId: string | null) {
  return useQuery({
    queryKey: queryKeys.executive(projectId ?? ""),
    queryFn: () => api.getExecutiveSummary(projectId!),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });
}

export function useQuality(projectId: string | null) {
  return useQuery({
    queryKey: queryKeys.quality(projectId ?? ""),
    queryFn: () => api.getQuality(projectId!),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });
}

export function usePolicies(projectId: string | null, runId?: string | null) {
  return useQuery({
    queryKey: runId ? [...queryKeys.policies(projectId ?? ""), runId] : queryKeys.policies(projectId ?? ""),
    queryFn: () => api.getPolicies(projectId!, runId),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });
}

export function useRuns(projectId: string | null, page = 1, pageSize = 25) {
  return useQuery({
    queryKey: queryKeys.runs(projectId ?? "", page, pageSize),
    queryFn: () => api.getRuns(projectId!, page, pageSize),
    enabled: Boolean(projectId),
    staleTime: 15_000,
  });
}

export function useRepositoryResults(
  projectId: string | null,
  filters: RepositoryHistoryFilters = {},
  page = 1,
  pageSize = 100,
  scopeRunId?: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.repositoryResults(projectId ?? "", filters, page, pageSize, scopeRunId),
    queryFn: () => api.getRepositoryResults(projectId!, filters, page, pageSize),
    enabled: Boolean(projectId) && enabled,
    staleTime: 15_000,
  });
}

export function useRun(runId: string | null, projectId?: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.run(runId ?? "", projectId),
    queryFn: () => api.getRun(runId!),
    enabled: Boolean(runId) && enabled,
    staleTime: 10_000,
  });
}

export function useRunStages(runId: string | null, projectId?: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.runStages(runId ?? "", projectId),
    queryFn: () => api.getRunStages(runId!),
    enabled: Boolean(runId) && enabled,
    staleTime: 10_000,
  });
}

export function useRunFindings(
  runId: string | null,
  page = 1,
  pageSize = 25,
  projectId?: string | null,
  filters: RunFindingFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.findings(runId ?? "", page, pageSize, projectId, filters),
    queryFn: () => api.getRunFindings(runId!, page, pageSize, filters),
    enabled: Boolean(runId) && enabled,
    staleTime: 15_000,
  });
}

export function useFinding(
  runId: string | null,
  findingId: string | null,
  projectId?: string | null,
  repositoryId?: string | null,
) {
  return useQuery({
    queryKey: queryKeys.finding(runId ?? "", findingId ?? "", projectId, repositoryId),
    queryFn: () => api.getFinding(runId!, findingId!),
    enabled: Boolean(runId && findingId),
    staleTime: 15_000,
  });
}

export function useRunEvidence(runId: string | null, page = 1, pageSize = 25, projectId?: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.evidence(runId ?? "", page, pageSize, projectId),
    queryFn: () => api.getRunEvidence(runId!, page, pageSize),
    enabled: Boolean(runId) && enabled,
    staleTime: 15_000,
  });
}

export function useAnalysisJobs(projectId: string | null, page = 1, pageSize = 25) {
  return useQuery({
    queryKey: queryKeys.analysisJobs(projectId ?? "", page, pageSize),
    queryFn: () => api.getAnalysisJobs(projectId!, page, pageSize),
    enabled: Boolean(projectId),
    staleTime: 3_000,
  });
}

export function useProposals(projectId: string | null, page = 1, pageSize = 25) {
  return useQuery({
    queryKey: queryKeys.proposals(projectId ?? "", page, pageSize),
    queryFn: () => api.getProposals(projectId!, page, pageSize),
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
}

export function useProposal(projectId: string | null, proposalId: string | null) {
  return useQuery<ProposalDetail>({
    queryKey: queryKeys.proposal(projectId ?? "", proposalId ?? ""),
    queryFn: () => api.getProposal(projectId!, proposalId!),
    enabled: Boolean(projectId && proposalId),
    staleTime: 5_000,
  });
}

export function useReports(projectId: string | null, page = 1, pageSize = 25, decision: ReportHumanDecision | "" = "") {
  return useQuery({
    queryKey: queryKeys.reports(projectId ?? "", page, pageSize, decision),
    queryFn: () => api.getReports(projectId!, page, pageSize, decision || undefined),
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
}

export function useReport(projectId: string | null, reportId: string | null) {
  return useQuery<ReportDetail>({
    queryKey: queryKeys.report(projectId ?? "", reportId ?? ""),
    queryFn: () => api.getReport(projectId!, reportId!),
    enabled: Boolean(projectId && reportId),
    staleTime: 5_000,
  });
}

export function useAudit(projectId: string | null, page: number, pageSize: number, filters: { event_type?: string; since?: string; until?: string }, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.audit(projectId ?? "", page, pageSize, filters.event_type ?? "", filters.since ?? "", filters.until ?? ""),
    queryFn: () => api.getAudit(projectId!, page, pageSize, filters),
    enabled: Boolean(projectId && enabled),
    staleTime: 5_000,
  });
}

export function useAuditEvent(projectId: string | null, eventId: string | null, enabled: boolean) {
  return useQuery<AuditEventDetail>({
    queryKey: queryKeys.auditEvent(projectId ?? "", eventId ?? ""),
    queryFn: () => api.getAuditEvent(projectId!, eventId!),
    enabled: Boolean(projectId && eventId && enabled),
    staleTime: 5_000,
  });
}

export function useSchedule(projectId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.schedule(projectId ?? ""),
    queryFn: () => api.getSchedule(projectId!),
    enabled: Boolean(projectId && enabled),
    staleTime: 10_000,
  });
}

export function useOutbox(projectId: string | null, page = 1, pageSize = 25, enabled = true) {
  return useQuery({
    queryKey: queryKeys.outbox(projectId ?? "", page, pageSize),
    queryFn: () => api.getOutbox(projectId!, page, pageSize),
    enabled: Boolean(projectId && enabled),
    staleTime: 5_000,
  });
}

export function useOutboxDetail(projectId: string | null, deliveryId: string | null, enabled = true) {
  return useQuery<OutboxDetail>({
    queryKey: queryKeys.outboxDetail(projectId ?? "", deliveryId ?? ""),
    queryFn: () => api.getOutboxDetail(projectId!, deliveryId!),
    enabled: Boolean(projectId && deliveryId && enabled),
    staleTime: 5_000,
  });
}

export function useWeeklyCycleJobs(projectId: string | null, page = 1, pageSize = 25, enabled = false) {
  return useQuery({
    queryKey: queryKeys.weeklyCycleJobs(projectId ?? "", page, pageSize),
    queryFn: () => api.getWeeklyCycleJobs(projectId!, page, pageSize),
    enabled: Boolean(projectId && enabled),
    staleTime: 2_000,
  });
}

export function useWeeklyCycleJob(projectId: string | null, jobId: string | null, enabled: boolean) {
  return useQuery<WeeklyCycleJob>({
    queryKey: queryKeys.weeklyCycleJob(projectId ?? "", jobId ?? ""),
    queryFn: () => api.getWeeklyCycleJob(projectId!, jobId!),
    enabled: Boolean(projectId && jobId && enabled),
    staleTime: 0,
    refetchInterval: (query) => query.state.data && ["queued", "running"].includes(query.state.data.status) ? 750 : false,
  });
}

export function useProjectConfiguration(projectId: string | null, enabled: boolean) {
  return useQuery<ProjectConfiguration>({
    queryKey: queryKeys.projectConfiguration(projectId ?? ""),
    queryFn: () => api.getProjectConfiguration(projectId!),
    enabled: Boolean(projectId && enabled),
    staleTime: 30_000,
  });
}

export function useDecideCodeFindingMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: {
      runId: string;
      findingId: string;
      payload: DecideCodeFindingRequest;
      csrfToken: string;
      idempotencyKey: string;
      projectId?: string | null;
      repositoryId?: string | null;
    }) => api.decideCodeFinding(variables.runId, variables.findingId, variables.payload, variables.csrfToken, variables.idempotencyKey),
    onSuccess: async (_result, variables) => {
      const findingsPrefix = variables.projectId !== undefined
        ? ["findings", variables.projectId ?? null, variables.runId]
        : ["findings", variables.runId];
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.finding(variables.runId, variables.findingId, variables.projectId, variables.repositoryId), refetchType: "none" }),
        queryClient.invalidateQueries({ queryKey: findingsPrefix }),
      ]);
    },
  });
}

export function useExecuteProposalMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: {
      projectId: string;
      proposalId: string;
      payload: ExecuteProposalRequest;
      csrfToken: string;
      idempotencyKey: string;
    }) => api.executeProposal(variables.projectId, variables.proposalId, variables.payload, variables.csrfToken, variables.idempotencyKey),
    onSuccess: async (_result, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["proposals", variables.projectId] }),
        queryClient.invalidateQueries({ queryKey: queryKeys.proposal(variables.projectId, variables.proposalId) }),
        queryClient.invalidateQueries({ queryKey: ["tasks", variables.projectId] }),
        queryClient.invalidateQueries({ queryKey: ["summary", variables.projectId] }),
        queryClient.invalidateQueries({ queryKey: ["audit", variables.projectId] }),
      ]);
    },
  });
}

export function useSendManagerEmailMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: {
      projectId: string;
      payload: SendManagerEmailRequest;
      csrfToken: string;
      idempotencyKey: string;
    }) => api.sendManagerEmail(variables.projectId, variables.payload, variables.csrfToken, variables.idempotencyKey),
    onSuccess: async (_result, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["schedule-outbox", variables.projectId] }),
        queryClient.invalidateQueries({ queryKey: queryKeys.schedule(variables.projectId) }),
        queryClient.invalidateQueries({ queryKey: ["audit", variables.projectId] }),
      ]);
    },
  });
}
