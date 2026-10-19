import { useEffect, useMemo } from "react";
import { ArrowLeft, Clock3, FileSearch, FileText, GitCommitHorizontal, Layers } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  formatDate,
  formatDurationSeconds,
  providerAttemptLabel,
  runStatusLabel,
  stageNameLabel,
  stageStatusLabel,
} from "../../api/formatters";
import {
  useProject,
  useProjects,
  useRepositoryResults,
  useRun,
  useRunEvidence,
  useRunStages,
} from "../../api/queries";
import { useWorkspace } from "../../app/WorkspaceContext";
import { useSession } from "../auth/SessionProvider";
import { RepositoryResultsPanel } from "../../components/RepositoryResultsPanel";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import type { RunStatus, StageView } from "../../api/types";
import "./run-detail.css";

const PIPELINE_STAGE_ORDER = [
  "source_collection",
  "monitoring",
  "quality",
  "verification",
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

  const kpis = useMemo(() => {
    const repos = repositoryResultsQuery.data?.repositories?.length ?? repositoryResultsQuery.data?.items?.length ?? 0;
    const sourceStage = stagesMap.get("source_collection");
    const qualityStage = stagesMap.get("quality");

    let candidateCount = 0;
    let verdictCount = 0;
    for (const repoItem of repositoryResultsQuery.data?.items ?? []) {
      candidateCount += repoItem.findings?.length ?? 0;
      for (const finding of repoItem.findings ?? []) {
        if (finding.verification_status && finding.verification_status !== "pending") {
          verdictCount += 1;
        }
      }
    }

    let completedStages = 0;
    for (const stageName of PIPELINE_STAGE_ORDER) {
      if (stagesMap.get(stageName)?.status === "completed") {
        completedStages += 1;
      }
    }

    return {
      repos,
      sourceStatus: sourceStage?.status ?? "unknown",
      qualityStatus: qualityStage?.status ?? "unknown",
      candidateCount,
      verdictCount,
      completedStages,
    };
  }, [repositoryResultsQuery.data, stagesMap]);

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

  return (
    <section className="run-detail-page content-section">
      {/* 1. Encabezado */}
      <header className="page-heading-row run-detail-header">
        <div>
          <div className="run-detail-nav">
            <button className="button button-secondary button-small button-back" type="button" onClick={() => navigate("/analysis")}>
              <ArrowLeft aria-hidden="true" size={14} /> Volver a análisis
            </button>
            <span className="caption">/ Detalle de ejecución</span>
          </div>
          <p className="eyebrow">CENTRO DE ANÁLISIS · {project?.name ?? run.project_id}</p>
          <h1>Ejecución {run.run_id}</h1>
          <div className="run-meta-row">
            <Pill tone={statusTone(run.status)}>{runStatusLabel(run.status)}</Pill>
            <Pill tone="neutral">{run.is_simulated ? "Simulación" : "Ejecución real"}</Pill>
            <Pill tone="neutral">
              <Clock3 aria-hidden="true" size={13} style={{ marginRight: 4 }} />
              {formatDurationSeconds(run.duration_seconds)}
            </Pill>
            <span>Creada: {formatDate(run.created_at, true)}</span>
            {run.report_id && (
              <Link to={`/reports?report_id=${encodeURIComponent(run.report_id)}`} className="text-link">
                <FileText aria-hidden="true" size={13} style={{ marginRight: 4 }} />
                Informe: {run.report_id}
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

      {run.error_code && (
        <div className="stage-error panel">
          <strong>Atención:</strong> La ejecución registró el código de error <code>{run.error_code}</code>. Revisa las etapas y evidencias a continuación.
        </div>
      )}

      {/* 2. Resumen ejecutivo de la ejecución (KPIs) */}
      <section className="panel run-kpis-panel" aria-labelledby="kpis-heading">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">MÉTRICAS CLAVE</p>
            <h2 id="kpis-heading">Resumen ejecutivo de la ejecución</h2>
          </div>
          <span className="caption">Datos consolidados a partir de los registros del backend</span>
        </div>
        <div className="run-kpi-grid">
          <article className="run-kpi-card">
            <span className="run-kpi-label">Repositorios</span>
            <span className="run-kpi-value"><Layers aria-hidden="true" size={16} /> {kpis.repos}</span>
          </article>
          <article className="run-kpi-card">
            <span className="run-kpi-label">Recopilación</span>
            <span className="run-kpi-value">
              <Pill tone={kpis.sourceStatus === "completed" ? "positive" : kpis.sourceStatus === "failed" ? "danger" : "warning"}>
                {stageStatusLabel(kpis.sourceStatus)}
              </Pill>
            </span>
          </article>
          <article className="run-kpi-card">
            <span className="run-kpi-label">Calidad (Quality)</span>
            <span className="run-kpi-value">
              <Pill tone={kpis.qualityStatus === "completed" ? "positive" : kpis.qualityStatus === "failed" ? "danger" : "warning"}>
                {stageStatusLabel(kpis.qualityStatus)}
              </Pill>
            </span>
          </article>
          <article className="run-kpi-card">
            <span className="run-kpi-label">Candidatos</span>
            <span className="run-kpi-value">{kpis.candidateCount}</span>
          </article>
          <article className="run-kpi-card">
            <span className="run-kpi-label">Veredictos</span>
            <span className="run-kpi-value">{kpis.verdictCount}</span>
          </article>
          <article className="run-kpi-card">
            <span className="run-kpi-label">Etapas completadas</span>
            <span className="run-kpi-value">{kpis.completedStages} / 5</span>
          </article>
        </div>
      </section>

      {/* 3. Etapas del pipeline */}
      <section className="panel run-pipeline-panel" aria-labelledby="pipeline-heading">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">SECUENCIA DEL FLUJO</p>
            <h2 id="pipeline-heading">Etapas del pipeline</h2>
          </div>
          <span className="caption">Source Collection → Monitoring → Quality → Verification → Report</span>
        </div>
        {stagesQuery.isPending && <LoadingState label="Consultando registros de etapas…" />}
        {stagesQuery.isError && <ErrorState title="No se pudieron cargar las etapas" message={stagesQuery.error.message} onRetry={() => void stagesQuery.refetch()} />}
        {stagesQuery.data && (
          <div className="run-pipeline-flow">
            {PIPELINE_STAGE_ORDER.map((stageName) => {
              const stage = stagesMap.get(stageName);
              const tone = stageTone(stage);
              const statusLabel = stage ? stageStatusLabel(stage.status) : "No ejecutada";

              return (
                <article key={stageName} className={`pipeline-stage-card stage-${stage?.status ?? "unknown"}`}>
                  <div className="stage-card-top">
                    <h3 className="stage-card-title">{stageNameLabel(stageName)}</h3>
                    <Pill tone={tone}>{statusLabel}</Pill>
                  </div>
                  <span className="stage-card-duration">
                    Duración: {formatDurationSeconds(stage?.duration_seconds ?? null)}
                  </span>
                  {stage?.result_summary && <p className="stage-card-summary">{stage.result_summary}</p>}
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

      {/* 4 y 5. Resultados por repositorio y Candidatos detectados */}
      <section className="panel run-repositories-panel" aria-labelledby="repositories-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">ANÁLISIS MULTIREPOSITORIO</p>
            <h2 id="repositories-heading">Resultados por repositorio y candidatos</h2>
            <p className="section-subtitle">
              Evidencia por repositorio, integración continua certificada y candidatos con veredicto independiente.
            </p>
          </div>
        </div>
        <RepositoryResultsPanel projectId={project?.id ?? run.project_id} runId={run.run_id} />
      </section>

      {/* 6. Evidencias vinculadas */}
      <section className="panel run-evidence-panel" aria-labelledby="evidence-heading">
        <div className="section-heading compact-heading">
          <div>
            <p className="eyebrow">TRAZABILIDAD</p>
            <h2 id="evidence-heading">Evidencias vinculadas ({evidenceQuery.data?.total ?? 0})</h2>
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
    </section>
  );
}
