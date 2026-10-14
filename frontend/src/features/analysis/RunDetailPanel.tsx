import { Link } from "react-router-dom";
import { ArrowRight, Clock3, FileSearch, GitCommitHorizontal } from "lucide-react";
import { formatDate, formatDurationSeconds, runStatusLabel, stageNameLabel, stageStatusLabel } from "../../api/formatters";
import { useRun, useRunEvidence, useRunStages } from "../../api/queries";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import type { StageView } from "../../api/types";

function stageTone(stage: StageView): "neutral" | "positive" | "warning" | "danger" | "unknown" | "accent" {
  if (!stage.recorded || stage.status === "unknown") return "unknown";
  if (stage.status === "completed") return "positive";
  if (stage.status === "failed") return "danger";
  if (stage.status === "needs_attention" || stage.status === "running") return "warning";
  if (stage.status === "skipped") return "neutral";
  return "accent";
}

function StageCard({ stage }: { stage: StageView }) {
  const status = !stage.recorded
    ? "No registrado; estado desconocido"
    : stageStatusLabel(stage.status);
  const tone = stageTone(stage);

  return (
    <article className="stage-card">
      <div className="stage-card-heading">
        <div>
          <p className="eyebrow">ETAPA PERSISTIDA</p>
          <h3>{stageNameLabel(stage.stage_name)}</h3>
          <code>{stage.stage_name}</code>
        </div>
        <div className="stage-status-stack"><Pill tone={tone}>{status}</Pill><code>{stage.status}</code></div>
      </div>
      <dl className="stage-facts">
        <div><dt>Duración</dt><dd>{formatDurationSeconds(stage.duration_seconds)}</dd></div>
        <div><dt>Finalizada</dt><dd>{stage.completed_at ? formatDate(stage.completed_at, true) : "Sin fecha registrada"}</dd></div>
      </dl>
      {stage.result_summary && <p className="stage-summary">{stage.result_summary}</p>}
      {stage.error_code && <p className="stage-error">Código seguro del backend: <code>{stage.error_code}</code></p>}
      <div className="provider-attempts">
        <h4>Proveedor y modelo registrados</h4>
        {stage.providers === null ? <p>Sin intentos de proveedor registrados.</p> : stage.providers.length === 0 ? <p>La etapa no reporta intentos de proveedor.</p> : (
          <ul>
            {stage.providers.map((attempt, index) => (
              <li key={`${attempt.provider}-${attempt.model}-${index}`}>
                <span>{attempt.provider} · {attempt.model}</span>
                <span>Estado: {attempt.status}</span>
                <span>Intentos: {attempt.attempt_count}</span>
                <span>Duración: {formatDurationSeconds(attempt.duration_seconds)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </article>
  );
}

export function RunDetailPanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const runQuery = useRun(runId);
  const stagesQuery = useRunStages(runId);
  const evidenceQuery = useRunEvidence(runId);

  if (runQuery.isPending) return <LoadingState label="Consultando la ejecución persistida…" />;
  if (runQuery.isError) {
    return <ErrorState title="No se pudo cargar la ejecución" message={runQuery.error.message} onRetry={() => void runQuery.refetch()} />;
  }

  const run = runQuery.data;
  return (
    <section className="panel run-detail-panel" aria-labelledby="run-detail-heading">
      <div className="section-heading run-detail-heading">
        <div>
          <p className="eyebrow">DETALLE PERSISTIDO</p>
          <h2 id="run-detail-heading">Ejecución {run.run_id}</h2>
          <p className="section-subtitle">Estado: {runStatusLabel(run.status)} · identificador de dominio: <code>{run.status}</code></p>
        </div>
        <button className="button button-secondary" type="button" onClick={onClose}>Cerrar detalle</button>
      </div>

      <dl className="run-facts">
        <div><dt>Proyecto</dt><dd>{run.project_id}</dd></div>
        <div><dt>Creada</dt><dd>{formatDate(run.created_at, true)}</dd></div>
        <div><dt>Actualizada</dt><dd>{formatDate(run.updated_at, true)}</dd></div>
        <div><dt>Captura asociada</dt><dd>{run.snapshot_id || "Sin dato"}</dd></div>
        <div><dt>Duración</dt><dd>{formatDurationSeconds(run.duration_seconds)}</dd></div>
        <div><dt>Simulación</dt><dd>{run.is_simulated === null ? "Sin dato" : run.is_simulated ? "Sí" : "No"}</dd></div>
        <div><dt>Informe asociado</dt><dd>{run.report_id ?? "Sin informe informado"}</dd></div>
      </dl>
      {run.error_code && <p className="stage-error">Código seguro del backend: <code>{run.error_code}</code></p>}

      <section className="run-detail-section" aria-labelledby="stages-heading">
        <div className="section-heading compact-heading">
          <div><p className="eyebrow">PIPELINE</p><h3 id="stages-heading">Etapas registradas</h3></div>
          <span className="caption">Las ausencias no se interpretan como omisiones.</span>
        </div>
        {stagesQuery.isPending && <LoadingState label="Consultando registros de etapa…" />}
        {stagesQuery.isError && <ErrorState title="No se pudieron cargar las etapas" message={stagesQuery.error.message} onRetry={() => void stagesQuery.refetch()} />}
        {stagesQuery.data && (stagesQuery.data.items.length ? (
          <div className="stage-grid">{stagesQuery.data.items.map((stage) => <StageCard key={stage.stage_name} stage={stage} />)}</div>
        ) : <EmptyState title="Sin registros de etapas" message="La API no devolvió etapas persistidas para esta ejecución." />)}
      </section>

      <section className="run-detail-section" aria-labelledby="evidence-heading">
        <div className="section-heading compact-heading">
          <div><p className="eyebrow">EVIDENCIA SEGURA</p><h3 id="evidence-heading">Evidencias vinculadas</h3></div>
          <span className="caption">Resumen redactado por el backend; no se descargan artefactos.</span>
        </div>
        {evidenceQuery.isPending && <LoadingState label="Consultando referencias de evidencia…" />}
        {evidenceQuery.isError && <ErrorState title="No se pudieron cargar las referencias" message={evidenceQuery.error.message} onRetry={() => void evidenceQuery.refetch()} />}
        {evidenceQuery.data && (evidenceQuery.data.items.length ? (
          <ul className="run-evidence-list">
            {evidenceQuery.data.items.map((evidence) => (
              <li key={evidence.id}>
                <div className="run-evidence-heading"><FileSearch aria-hidden="true" size={15} /><strong>{evidence.category ?? "Evidencia"}</strong><code>{evidence.id}</code></div>
                <p>{evidence.summary}</p>
                <div className="run-evidence-meta">
                  <span>Origen: {evidence.source}</span>
                  {evidence.repository && <span>Repositorio: {evidence.repository}</span>}
                  {evidence.commit_sha && <span><GitCommitHorizontal aria-hidden="true" size={13} /> {evidence.commit_sha}</span>}
                  <span><Clock3 aria-hidden="true" size={13} /> {formatDate(evidence.timestamp, true)}</span>
                  {evidence.file_path && <span>{evidence.file_path}{evidence.lines?.length ? ` · líneas ${evidence.lines.join(", ")}` : ""}</span>}
                </div>
              </li>
            ))}
          </ul>
        ) : <EmptyState title="Sin evidencias reportadas" message="La consulta no encontró referencias persistidas para esta ejecución." />)}
      </section>

      <div className="run-detail-footer">
        <Link className="text-link" to={`/quality?run_id=${encodeURIComponent(run.run_id)}`}>Revisar calidad y hallazgos <ArrowRight aria-hidden="true" size={14} /></Link>
      </div>
    </section>
  );
}
