import { useEffect, useMemo } from "react";
import { ArrowLeft, Clock3, FileSearch, FileText, GitCommitHorizontal } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  attentionKindLabel,
  attentionReasonLabel,
  formatDate,
  formatDurationSeconds,
  formatPercentage,
  providerAttemptLabel,
  runStatusLabel,
  stageNameLabel,
  stageStatusLabel,
} from "../../api/formatters";
import {
  useProject,
  useProjectSummary,
  useProjects,
  useProposals,
  useRepositoryResults,
  useReport,
  useRun,
  useRunEvidence,
  useRunFindings,
  useRunStages,
} from "../../api/queries";
import { useWorkspace } from "../../app/WorkspaceContext";
import { useSession } from "../auth/SessionProvider";
import { RepositoryResultsPanel } from "../../components/RepositoryResultsPanel";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import { ProposalStageDiagnosticCard } from "./ProposalStageDiagnosticCard";
import type { AttentionNotice, RunStatus, StageView } from "../../api/types";
import "./run-detail.css";

const PIPELINE_STAGE_ORDER = [
  "source_collection",
  "monitoring",
  "proposals",
  "quality",
  "verifier",
  "report",
] as const;

function statusTone(status: RunStatus): "neutral" | "positive" | "warning" | "danger" | "unknown" | "accent" {
  if (status === "completed") return "positive";
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "needs_attention" || status === "awaiting_human") return "warning";
  if (status === "unknown") return "unknown";
  if (status === "queued") return "neutral";
  return "accent";
}

function stageTone(stage: StageView | undefined): "neutral" | "positive" | "warning" | "danger" | "unknown" | "accent" {
  if (!stage || !stage.recorded || stage.status === "unknown") return "unknown";
  if (stage.status === "completed") return "positive";
  if (stage.status === "failed") return "danger";
  if (stage.status === "needs_attention" || stage.status === "running") return "warning";
  if (stage.status === "skipped") return "neutral";
  return "accent";
}

function attentionTone(notice: AttentionNotice): "neutral" | "positive" | "warning" | "danger" | "unknown" | "accent" {
  if (notice.kind === "failure") return "danger";
  if (notice.kind === "attention") return "warning";
  if (notice.kind === "limitation") return "neutral";
  return "unknown";
}

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const { selectedProjectId } = useWorkspace();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const selectedProjectQuery = useProject(selectedProjectId, Boolean(selectedProjectId && !listedProject));
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const { session } = useSession();
  const canReadAudit = Boolean(session?.authenticated && session.capabilities.includes("audit:read"));

  const effectiveRunId = runId ?? "";
  const runQuery = useRun(effectiveRunId, project?.id ?? null);
  const run = runQuery.data;
  const runMatchesProject = Boolean(run && project && run.project_id === project.id);

  const stagesQuery = useRunStages(effectiveRunId, project?.id ?? null, runMatchesProject);
  const evidenceQuery = useRunEvidence(effectiveRunId, 1, 100, project?.id ?? null, runMatchesProject);
  const repositoryResultsQuery = useRepositoryResults(
    project?.id ?? null,
    {},
    1,
    100,
    effectiveRunId || undefined,
    canReadAudit,
  );
  const findingsQuery = useRunFindings(effectiveRunId, 1, 100, project?.id ?? null, {}, runMatchesProject);
  const reportQuery = useReport(project?.id ?? null, run?.report_id ?? null);
  const proposalsQuery = useProposals(project?.id ?? null, 1, 1);
  const summaryQuery = useProjectSummary(project?.id ?? null);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [effectiveRunId]);

  const stagesMap = useMemo(() => {
    const map = new Map<string, StageView>();
    for (const stage of stagesQuery.data?.items ?? []) {
      map.set(stage.stage_name, stage);
    }
    return map;
  }, [stagesQuery.data?.items]);

  const findings = useMemo(() => findingsQuery.data?.items ?? [], [findingsQuery.data?.items]);

  const findingVerdicts = useMemo(() => {
    const counts = { confirmed: 0, rejected: 0, insufficient: 0, pending: 0, unknown: 0 };
    for (const finding of findings) {
      if (finding.verification_status in counts) {
        counts[finding.verification_status as keyof typeof counts] += 1;
      }
    }
    return counts;
  }, [findings]);

  /** Observations of this run, with the repository identity the backend reported. */
  const runObservations = useMemo(() => {
    const items = repositoryResultsQuery.data?.items ?? [];
    return items.filter((item) => item.analysis_run_id === run?.run_id);
  }, [repositoryResultsQuery.data, run?.run_id]);

  const incompleteRepositories = useMemo(
    () => runObservations.filter((item) => item.source_complete === false),
    [runObservations],
  );

  const attention = run?.attention ?? [];
  const attentionNotices = attention;

  /** Risks the report pointed at, with the readable titles of the capture. */
  const reportRisks = useMemo(() => {
    const risks = summaryQuery.data?.risks ?? [];
    const pointed = reportQuery.data?.risk_task_ids ?? [];
    if (!pointed.length) return [];
    return risks.filter((risk) => risk.task_ids.some((taskId) => pointed.includes(taskId)));
  }, [reportQuery.data?.risk_task_ids, summaryQuery.data?.risks]);

  const report = reportQuery.data ?? null;
  const recommendations = report?.recommendations ?? [];
  const proposals = proposalsQuery.data ?? null;
  const pendingProposals = proposals?.availability === "available"
    ? proposals.pending_actionable_count ?? 0
    : null;

  if (!effectiveRunId) {
    return <ErrorState title="Ejecución no especificada" message="Se requiere un identificador de ejecución para ver sus detalles." onRetry={() => navigate("/analysis")} />;
  }

  if (runQuery.isPending) {
    return <LoadingState label="Cargando detalle de ejecución…" />;
  }

  if (runQuery.isError || !run) {
    return (
      <section className="run-detail-page content-section">
        <div className="run-detail-nav">
          <button className="button button-secondary button-small button-back" type="button" onClick={() => navigate("/analysis")}>
            <ArrowLeft aria-hidden="true" size={14} /> Volver a análisis
          </button>
        </div>
        <ErrorState
          title="No se pudo cargar la ejecución"
          message={runQuery.error?.message ?? "La ejecución solicitada no existe o no se pudo cargar."}
          onRetry={() => void runQuery.refetch()}
        />
      </section>
    );
  }

  if (project && run.project_id !== project.id) {
    return (
      <section className="run-detail-page content-section">
        <div className="run-detail-nav">
          <button className="button button-secondary button-small button-back" type="button" onClick={() => navigate("/analysis")}>
            <ArrowLeft aria-hidden="true" size={14} /> Volver a análisis
          </button>
        </div>
        <ErrorState
          title="La ejecución no pertenece al proyecto activo"
          message={`La ejecución ${run.run_id} pertenece al proyecto ${run.project_id}, mientras que el proyecto seleccionado es ${project.id}.`}
          onRetry={() => navigate("/analysis")}
        />
      </section>
    );
  }

  const completedStages = PIPELINE_STAGE_ORDER.filter(
    (stageName) => stagesMap.get(stageName)?.status === "completed",
  ).length;

  return (
    <section className="run-detail-page content-section">
      {/* Encabezado */}
      <header className="page-heading-row run-detail-header">
        <div>
          <div className="run-detail-nav">
            <button className="button button-secondary button-small button-back" type="button" onClick={() => navigate("/analysis")}>
              <ArrowLeft aria-hidden="true" size={14} /> Volver a análisis
            </button>
            <span className="caption">/ Detalle de ejecución</span>
          </div>
          <p className="eyebrow">CENTRO DE ANÁLISIS · {project?.name ?? run.project_id}</p>
          <h1>Análisis del {formatDate(run.created_at, true)}</h1>
          <div className="run-meta-row">
            <Pill tone={statusTone(run.status)}>{runStatusLabel(run.status)}</Pill>
            <Pill tone="neutral">{run.is_simulated ? "Simulación" : "Ejecución real"}</Pill>
            <Pill tone="neutral">
              <Clock3 aria-hidden="true" size={13} style={{ marginRight: 4 }} />
              {formatDurationSeconds(run.duration_seconds)}
            </Pill>
            <span>Proyecto: {run.project_id}</span>
            <code className="run-technical-id">{run.run_id}</code>
            {run.report_id && (
              <Link to={`/reports?report_id=${encodeURIComponent(run.report_id)}`} className="text-link">
                <FileText aria-hidden="true" size={13} style={{ marginRight: 4 }} />
                Informe del análisis
              </Link>
            )}
          </div>
        </div>
        <div className="heading-actions">
          <Link to={`/quality?run_id=${encodeURIComponent(run.run_id)}`} className="button button-secondary">
            Ver calidad de código
          </Link>
        </div>
      </header>

      {/* 1. Resultado general */}
      <section className="panel run-outcome-panel" aria-labelledby="outcome-heading">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">RESULTADO GENERAL</p>
            <h2 id="outcome-heading">Qué quedó de este análisis</h2>
          </div>
          <span className="caption">Los motivos los informa el backend a partir de los registros persistidos.</span>
        </div>
        <dl className="run-facts">
          <div><dt>Proyecto</dt><dd>{run.project_id}</dd></div>
          <div><dt>Fecha y hora</dt><dd>{formatDate(run.created_at, true)}</dd></div>
          <div><dt>Estado final</dt><dd>{runStatusLabel(run.status)}</dd></div>
          <div><dt>Duración</dt><dd>{formatDurationSeconds(run.duration_seconds)}</dd></div>
        </dl>
        {attentionNotices.length ? (
          <ul className="run-attention-list" aria-label="Motivos registrados">
            {attentionNotices.map((notice, index) => (
              <li key={`${notice.reason_code}-${index}`}>
                <div className="run-attention-heading">
                  <Pill tone={attentionTone(notice)}>{attentionKindLabel(notice.kind)}</Pill>
                  <strong>{attentionReasonLabel(notice)}</strong>
                </div>
                <p className="run-attention-message">{notice.message}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="run-outcome-clean" role="status">
            {run.status === "completed"
              ? "El análisis se completó y no dejó motivos registrados."
              : "No hay motivos registrados para este análisis."}
          </p>
        )}
        {report && (
          <p className="run-outcome-decision" role="status">
            Decisión del informe: <strong>{report.human_decision === "pending" ? "pendiente de tu revisión" : report.human_decision}</strong>
            {report.decision_reason ? ` — ${report.decision_reason}` : ""}
          </p>
        )}
      </section>

      {/* 2. Qué encontró el análisis */}
      <section className="panel run-found-panel" aria-labelledby="found-heading">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">QUÉ ENCONTRÓ EL ANÁLISIS</p>
            <h2 id="found-heading">Riesgos, hallazgos y limitaciones</h2>
          </div>
        </div>

        <div className="run-found-grid">
          <article className="run-found-card">
            <h3>Riesgos relevantes</h3>
            {reportRisks.length ? (
              <ul className="run-found-list">
                {reportRisks.map((risk) => (
                  <li key={risk.risk_id}>
                    <strong>{risk.summary}</strong>
                    <span>
                      {risk.task_ids.map((taskId) => risk.task_titles?.[taskId] ?? taskId).join(" · ")}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="run-found-empty">El informe no señaló riesgos pendientes.</p>
            )}
          </article>

          <article className="run-found-card">
            <h3>Posibles defectos y veredictos</h3>
            {findings.length ? (
              <>
                <p className="run-found-note">Los hallazgos son <strong>candidatos</strong>: hasta que un verificador los confirma no son defectos probados.</p>
                <ul className="run-found-list">
                  <li><span>Candidatos registrados</span><strong>{findings.length}</strong></li>
                  <li><span>Confirmados por el verificador</span><strong>{findingVerdicts.confirmed}</strong></li>
                  <li><span>Rechazados por el verificador</span><strong>{findingVerdicts.rejected}</strong></li>
                  <li><span>Con evidencia insuficiente</span><strong>{findingVerdicts.insufficient}</strong></li>
                  <li><span>Sin veredicto</span><strong>{findingVerdicts.pending + findingVerdicts.unknown}</strong></li>
                </ul>
              </>
            ) : (
              <p className="run-found-empty">
                {findingsQuery.data
                  ? "No se registraron hallazgos candidatos en este análisis."
                  : "No se pudo consultar el registro de hallazgos."}
              </p>
            )}
          </article>

          <article className="run-found-card">
            <h3>Limitaciones de la recopilación</h3>
            {attentionNotices.filter((notice) => notice.kind === "limitation").length || incompleteRepositories.length ? (
              <ul className="run-found-list">
                {attentionNotices.filter((notice) => notice.kind === "limitation").map((notice, index) => (
                  <li key={`limitation-${index}`}>
                    <strong>{attentionReasonLabel(notice)}</strong>
                    <span>{notice.message}</span>
                  </li>
                ))}
                {incompleteRepositories.map((observation) => (
                  <li key={observation.capture_id}>
                    <strong>
                      {observation.repository ? `${observation.repository.owner}/${observation.repository.repo}` : "Repositorio sin identidad recuperable"}
                    </strong>
                    <span>{observation.incomplete_reason ?? "La fuente no quedó completa."}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="run-found-empty">No se registraron limitaciones en la recopilación.</p>
            )}
          </article>

          <article className="run-found-card">
            <h3>Repositorios afectados</h3>
            {runObservations.length ? (
              <ul className="run-found-list">
                {runObservations.map((observation) => (
                  <li key={observation.capture_id}>
                    <strong>
                      {observation.repository ? `${observation.repository.owner}/${observation.repository.repo}` : "Identidad de repositorio desconocida"}
                    </strong>
                    <span>
                      {observation.source_complete === null
                        ? "Completitud de la fuente desconocida."
                        : observation.source_complete
                          ? "Fuentes completas."
                          : "Fuentes incompletas."}
                      {observation.coverage_percentage !== null
                        ? ` · Cobertura ${formatPercentage(observation.coverage_percentage)}`
                        : " · Cobertura sin dato."}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="run-found-empty">
                {canReadAudit
                  ? "No se asociaron observaciones de repositorio a esta ejecución."
                  : "El historial de repositorios requiere permiso de auditoría."}
              </p>
            )}
          </article>
        </div>
      </section>

      {/* 3. Qué recomienda el sistema */}
      <section className="panel run-recommendation-panel" aria-labelledby="recommendation-heading">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">QUÉ RECOMIENDA EL SISTEMA</p>
            <h2 id="recommendation-heading">Recomendaciones y acciones pendientes</h2>
          </div>
          <span className="caption">Las recomendaciones son informativas; las propuestas requieren tu autorización.</span>
        </div>
        {reportQuery.isPending && <LoadingState label="Consultando el informe del análisis…" />}
        {reportQuery.isError && <ErrorState title="No se pudo consultar el informe" message={reportQuery.error.message} onRetry={() => void reportQuery.refetch()} />}
        {report && (
          <>
            <div className="run-recommendation-recommendation">
              <span className="run-recommendation-label">Recomendación del informe</span>
              <strong>{report.recommendation}</strong>
              <p>{report.summary}</p>
            </div>
            <h3>Recomendaciones registradas</h3>
            {recommendations.length ? (
              <ul className="run-found-list">
                {recommendations.map((recommendation, index) => (
                  <li key={`${recommendation}-${index}`}><span>{recommendation}</span></li>
                ))}
              </ul>
            ) : (
              <p className="run-found-empty">El informe no dejó recomendaciones registradas.</p>
            )}
          </>
        )}
        <div className="run-recommendation-actions">
          <h3>Acciones que requieren tu autorización</h3>
          {proposalsQuery.isPending && <LoadingState label="Consultando las propuestas del proyecto…" />}
          {proposalsQuery.isError && <ErrorState title="No se pudieron consultar las propuestas" message={proposalsQuery.error.message} onRetry={() => void proposalsQuery.refetch()} />}
          {pendingProposals === null && !proposalsQuery.isPending && !proposalsQuery.isError && (
            <p className="run-found-empty">No se pudo confirmar la disponibilidad de propuestas.</p>
          )}
          {pendingProposals === 0 && (
            <p className="run-found-empty">No hay propuestas pendientes de autorización en este proyecto.</p>
          )}
          {pendingProposals !== null && pendingProposals > 0 && (
            <p className="run-found-warning">
              Hay <strong>{pendingProposals}</strong> propuesta{pendingProposals === 1 ? "" : "s"} pendiente{pendingProposals === 1 ? "" : "s"} de autorización.
            </p>
          )}
        </div>
      </section>

      {/* 4. Qué debe hacer el gestor */}
      <section className="panel run-next-steps-panel" aria-labelledby="next-steps-heading">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">QUÉ DEBE HACER EL GESTOR</p>
            <h2 id="next-steps-heading">Siguientes pasos disponibles</h2>
          </div>
          <span className="caption">Solo se ofrecen accesos a información que existe para este análisis.</span>
        </div>
        <ul className="run-next-steps-list">
          {report && (
            <li>
              <Link className="button button-secondary button-small" to={`/reports?report_id=${encodeURIComponent(report.report_id)}`}>
                <FileText aria-hidden="true" size={14} /> Leer el informe completo
              </Link>
              <span>Contiene la recomendación y el detalle de las reglas evaluadas.</span>
            </li>
          )}
          {findings.length > 0 && (
            <li>
              <Link className="button button-secondary button-small" to={`/quality?run_id=${encodeURIComponent(run.run_id)}`}>
                Revisar los hallazgos candidatos
              </Link>
              <span>{findings.length} hallazgo{findings.length === 1 ? "" : "s"} con veredicto de verificación.</span>
            </li>
          )}
          {pendingProposals !== null && pendingProposals > 0 && (
            <li>
              <Link className="button button-secondary button-small" to="/proposals">
                Autorizar las propuestas pendientes
              </Link>
              <span>{pendingProposals} propuesta{pendingProposals === 1 ? "" : "s"} requiere{pendingProposals === 1 ? "" : "n"} tu autorización.</span>
            </li>
          )}
          {(evidenceQuery.data?.total ?? 0) > 0 && (
            <li>
              <a className="button button-secondary button-small" href="#run-evidence-panel">
                <FileSearch aria-hidden="true" size={14} /> Consultar la evidencia original
              </a>
              <span>{evidenceQuery.data?.total} evidencia{evidenceQuery.data?.total === 1 ? "" : "s"} registrada{evidenceQuery.data?.total === 1 ? "" : "s"}.</span>
            </li>
          )}
          {runObservations.some((observation) => observation.code_diff_count > 0) && (
            <li>
              <a className="button button-secondary button-small" href="#run-technical-panel">
                <GitCommitHorizontal aria-hidden="true" size={14} /> Revisar los cambios analizados
              </a>
              <span>Cambios de código recopilados por repositorio.</span>
            </li>
          )}
        </ul>
        {!report && findings.length === 0 && pendingProposals === 0 && (evidenceQuery.data?.total ?? 0) === 0 && (
          <p className="run-found-empty">Este análisis no dejó acciones pendientes que requieran tu intervención.</p>
        )}
      </section>

      {/* 5. Información técnica */}
      <details className="panel run-technical-panel" id="run-technical-panel">
        <summary>Información técnica del análisis</summary>
        <p className="caption">
          Etapas del pipeline, proveedores, resultados por repositorio y evidencias originales.
          Las seis etapas son: Source Collection → Monitoring → Proposals → Quality → Verification → Report.
        </p>

        <section className="run-technical-section" aria-labelledby="pipeline-heading">
          <div className="section-heading compact-heading">
            <div>
              <p className="eyebrow">SECUENCIA DEL FLUJO</p>
              <h3 id="pipeline-heading">Etapas del pipeline</h3>
            </div>
            <span className="caption">{completedStages} de {PIPELINE_STAGE_ORDER.length} etapas completadas</span>
          </div>
          {stagesQuery.isPending && <LoadingState label="Consultando registros de etapas…" />}
          {stagesQuery.isError && <ErrorState title="No se pudieron cargar las etapas" message={stagesQuery.error.message} onRetry={() => void stagesQuery.refetch()} />}
          {stagesQuery.data && (
            <div className="run-pipeline-flow">
              {PIPELINE_STAGE_ORDER.map((stageName) => {
                const stage = stagesMap.get(stageName);
                const tone = stageTone(stage);
                const statusLabel = !stage?.recorded
                  ? "No registrado; estado desconocido"
                  : stageStatusLabel(stage.status);

                return (
                  <article key={stageName} className={`pipeline-stage-card stage-${stage?.recorded ? stage.status : "unknown"}`}>
                    <div className="stage-card-top">
                      <h4 className="stage-card-title">{stageNameLabel(stageName)}</h4>
                      <Pill tone={tone}>{statusLabel}</Pill>
                    </div>
                    <span className="stage-card-duration">
                      Duración: {formatDurationSeconds(stage?.duration_seconds ?? null)}
                    </span>
                    {stage?.result_summary && <p className="stage-card-summary">{stage.result_summary}</p>}
                    {stageName === "proposals" && stage?.proposals && (
                      <ProposalStageDiagnosticCard diagnostic={stage.proposals} />
                    )}
                    {stage?.error_code && (
                      <p className="stage-error" style={{ margin: "4px 0 0", fontSize: "10px" }}>
                        Error reportado: <code>{stage.error_code}</code>
                      </p>
                    )}
                    {stage?.providers && stage.providers.length > 0 && (
                      <ul className="stage-providers-list">
                        {stage.providers.map((attempt, idx) => (
                          <li key={`${attempt.provider}-${attempt.model}-${idx}`}>
                            <span>{providerAttemptLabel(attempt.provider, attempt.model)}</span>
                            <span>{attempt.attempt_count} int. · {formatDurationSeconds(attempt.duration_seconds)}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </section>

        <section className="run-technical-section" aria-labelledby="repositories-heading">
          <div className="section-heading">
            <div>
              <p className="eyebrow">ANÁLISIS MULTIREPOSITORIO</p>
              <h3 id="repositories-heading">Resultados por repositorio y candidatos</h3>
              <p className="section-subtitle">
                Evidencia por repositorio, integración continua certificada y candidatos con veredicto independiente.
              </p>
            </div>
          </div>
          <RepositoryResultsPanel projectId={project?.id ?? run.project_id} runId={run.run_id} />
        </section>

        <section className="run-technical-section" id="run-evidence-panel" aria-labelledby="evidence-heading">
          <div className="section-heading compact-heading">
            <div>
              <p className="eyebrow">TRAZABILIDAD</p>
              <h3 id="evidence-heading">Evidencias vinculadas ({evidenceQuery.data?.total ?? 0})</h3>
            </div>
            <span className="caption">Resúmenes acotados y seguros; el código sensible permanece protegido</span>
          </div>
          {evidenceQuery.isPending && <LoadingState label="Consultando evidencias…" />}
          {evidenceQuery.isError && <ErrorState title="No se pudieron cargar las evidencias" message={evidenceQuery.error.message} onRetry={() => void evidenceQuery.refetch()} />}
          {evidenceQuery.data && (evidenceQuery.data.items.length === 0 ? (
            <EmptyState title="Sin evidencias registradas" message="Esta ejecución no reportó snippets ni artefactos de evidencia persistidos." />
          ) : (
            <div className="evidence-accordion-list">
              {evidenceQuery.data.items.map((evidence) => (
                <details key={evidence.id} className="evidence-accordion-item">
                  <summary>
                    <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <FileSearch aria-hidden="true" size={14} />
                      <strong>{evidence.category ?? "Evidencia"}</strong>
                      <code>{evidence.id}</code>
                    </span>
                    <Pill tone="neutral">{evidence.source}</Pill>
                  </summary>
                  <div className="evidence-accordion-body">
                    <p style={{ margin: "0 0 8px" }}>{evidence.summary}</p>
                    <div className="evidence-meta-grid">
                      {evidence.repository && <span><strong>Repositorio:</strong> {evidence.repository}</span>}
                      {evidence.commit_sha && (
                        <span>
                          <GitCommitHorizontal aria-hidden="true" size={12} style={{ marginRight: 4 }} />
                          <strong>Commit:</strong> {evidence.commit_sha}
                        </span>
                      )}
                      {evidence.file_path && <span><strong>Archivo:</strong> {evidence.file_path}{evidence.lines?.length ? `:${evidence.lines.join(", ")}` : ""}</span>}
                      <span><strong>Fecha:</strong> {formatDate(evidence.timestamp, true)}</span>
                    </div>
                  </div>
                </details>
              ))}
            </div>
          ))}
        </section>
      </details>
    </section>
  );
}
