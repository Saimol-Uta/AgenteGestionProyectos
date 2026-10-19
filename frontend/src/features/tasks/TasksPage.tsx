import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertTriangle, ArrowDown, ArrowUp, CalendarClock, ChevronLeft, ChevronRight,
  CircleHelp, Clock3, ExternalLink, Filter, ListFilter, Search, ShieldAlert, Wrench, X,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { queryKeys, useCaptureHistory, useProject, useProjectSummary, useProjects } from "../../api/queries";
import {
  assigneeDisplay, comparisonValue, fieldLabel, formatDate, hasAssignees, priorityDisplay,
  priorityUnavailableReason, proposalAuthorizationStatusLabel, proposalExecutionStatusLabel,
  proposalFreshnessLabel, proposalOutcomeExplanation, proposalOutcomeLabel, proposalOutcomeTone,
  proposalReviewStatusLabel, proposalStatusTone, proposalTechnicalStatusLabel, riskKindLabel,
  riskKindTone, taskStateLabel, taskStateTone, wasObserved,
} from "../../api/formatters";
import type {
  DomainTaskState, EvidenceItem, ProposalStageDiagnostic, TaskChange, TaskDetailResponse,
  TaskDetailView, TaskQuery, TaskRisk,
} from "../../api/types";
import { useWorkspace } from "../../app/WorkspaceContext";
import { RiskProposalLinks } from "../../components/RiskProposalLinks";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";

const pageSize = 25;
const domainStates: DomainTaskState[] = ["pending", "en_curso", "completada", "bloqueada", "desconocido"];

function observedDate(task: TaskDetailResponse, field: string, value: string | null | undefined) {
  if (value) return formatDate(value, true);
  return wasObserved(task, field) ? "Sin fecha registrada" : "No observada";
}

/**
 * Signals the manager can act on. Every one of them is backed by an observed fact of the
 * capture, so a task that is merely due soon never carries an extra severity of its own.
 */
function taskSignals(task: TaskDetailView) {
  const signals: Array<{
    key: string;
    label: string;
    short: string;
    tone: "danger" | "warning" | "unknown";
    icon: typeof CalendarClock;
  }> = [];
  if (task.is_overdue) {
    signals.push({ key: "overdue", label: "Vencida", short: "Vencida", tone: "danger", icon: CalendarClock });
  }
  if (task.is_due_soon) {
    signals.push({ key: "due-soon", label: "Próxima a vencer", short: "Próxima", tone: "warning", icon: Clock3 });
  }
  if (task.state === "bloqueada") {
    signals.push({ key: "blocked", label: "Bloqueada", short: "Bloqueada", tone: "danger", icon: ShieldAlert });
  }
  if (task.has_contradiction) {
    signals.push({ key: "contradiction", label: "Señales contradictorias", short: "Contradicción", tone: "warning", icon: AlertTriangle });
  }
  if (task.state === "desconocido") {
    signals.push({ key: "unknown", label: "Estado desconocido", short: "Desconocido", tone: "unknown", icon: CircleHelp });
  }
  return signals;
}

function TaskSignalPills({ task, compact }: { task: TaskDetailView; compact?: boolean }) {
  const signals = taskSignals(task);
  if (signals.length === 0) {
    return <span className="text-muted">Sin alertas informadas</span>;
  }
  return (
    <>
      {signals.map((signal) => (
        <Pill key={signal.key} tone={signal.tone}>
          <signal.icon aria-hidden="true" size={compact ? 12 : 13} /> {compact ? signal.short : signal.label}
        </Pill>
      ))}
    </>
  );
}

/** Responsible person of a task, never inventing a name the origin does not report. */
function responsibleDisplay(task: TaskDetailView) {
  const label = assigneeDisplay(task);
  return label === "Sin asignar" ? label : <span className="text-muted">{label}</span>;
}

/** Planned dates of a pending task, shown as dates and never as remaining effort. */
function plannedDates(task: TaskDetailView) {
  if (task.state !== "pending" || (!task.start_date && !task.due_date)) return null;
  return (
    <span className="task-planned-dates">
      {task.start_date && <span>Inicio {formatDate(task.start_date)}</span>}
      {task.start_date && task.due_date && <span aria-hidden="true">·</span>}
      {task.due_date && <span>Vence {formatDate(task.due_date)}</span>}
    </span>
  );
}

function queryState(value: string | null): DomainTaskState | undefined {
  return value && domainStates.includes(value as DomainTaskState) ? value as DomainTaskState : undefined;
}

function taskFilters(params: URLSearchParams, captureId?: string): TaskQuery {
  const pageNumber = Number(params.get("page") ?? "1");
  const sort = params.get("sort");
  const direction = params.get("direction");
  return {
    capture_id: captureId,
    q: params.get("q") || undefined,
    state: queryState(params.get("state")),
    assignee_id: params.get("assignee_id") || undefined,
    list_name: params.get("list_name") || undefined,
    critical_only: params.get("critical_only") === "true" ? true : undefined,
    overdue_only: params.get("overdue_only") === "true" ? true : undefined,
    due_soon_only: params.get("due_soon_only") === "true" ? true : undefined,
    sort: sort === "title" || sort === "due_date" ? sort : "due_date",
    direction: direction === "desc" ? "desc" : "asc",
    page: Number.isInteger(pageNumber) && pageNumber > 0 ? pageNumber : 1,
    page_size: pageSize,
  };
}

function TaskDetailDialog({
  projectId,
  taskId,
  captureId,
  diagnostic,
  onSelectTask,
  onClose,
}: {
  projectId: string;
  taskId: string | null;
  captureId?: string;
  diagnostic?: ProposalStageDiagnostic | null;
  onSelectTask: (taskId: string) => void;
  onClose: () => void;
}) {
  const [showComparison, setShowComparison] = useState(false);
  const [beforeId, setBeforeId] = useState("");
  const [afterId, setAfterId] = useState("");
  const detailQuery = useQuery({
    queryKey: queryKeys.task(projectId, taskId ?? "", captureId),
    queryFn: () => api.getTask(projectId, taskId!, captureId),
    enabled: Boolean(taskId),
  });
  const historyQuery = useCaptureHistory(taskId ? projectId : null);
  const captures = useMemo(() => [...(historyQuery.data?.items ?? [])].sort((left, right) => {
    return new Date(left.captured_at).getTime() - new Date(right.captured_at).getTime();
  }), [historyQuery.data?.items]);
  const previousDefault = captures.at(-2)?.capture_id ?? captures[0]?.capture_id ?? "";
  const currentDefault = captures.at(-1)?.capture_id ?? "";
  const before = beforeId || previousDefault;
  const after = afterId || currentDefault;
  const beforeCapture = captures.find((capture) => capture.capture_id === before);
  const afterCapture = captures.find((capture) => capture.capture_id === after);
  const chronologyReversed = Boolean(
    beforeCapture && afterCapture && new Date(beforeCapture.captured_at).getTime() > new Date(afterCapture.captured_at).getTime(),
  );
  const invalidCapturePair = Boolean(before && after && (before === after || chronologyReversed));
  const canCompare = Boolean(showComparison && before && after && !invalidCapturePair);
  const comparisonQuery = useQuery({
    queryKey: queryKeys.comparison(projectId, before, after),
    queryFn: () => api.compareCaptures(projectId, before, after),
    enabled: canCompare,
  });

  const task = detailQuery.data;
  const taskChanges = task?.history;
  const comparison = comparisonQuery.data;
  const matchingChanges = comparison?.changes.filter((change) => change.task_id === taskId) ?? [];

  return (
    <Dialog.Root open={Boolean(taskId)} onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content task-drawer">
          <div className="drawer-header">
            <div className="drawer-title-wrap">
              <p className="eyebrow">DETALLE DE TAREA</p>
              <Dialog.Title className="dialog-title">{task?.title ?? "Cargando tarea…"}</Dialog.Title>
              {task && <code className="drawer-task-id">{task.id}</code>}
            </div>
            <Dialog.Close asChild>
              <button type="button" className="icon-button" aria-label="Cerrar detalle de tarea"><X aria-hidden="true" size={18} /></button>
            </Dialog.Close>
          </div>

          {detailQuery.isPending && <LoadingState label="Consultando detalle y evidencia…" />}
          {detailQuery.isError && <ErrorState title="No se pudo cargar el detalle" message={detailQuery.error.message} onRetry={() => void detailQuery.refetch()} />}
          {task && (
            <div className="drawer-content-scroll">
              <div className="task-status-row">
                <Pill tone={taskStateTone(task.state)}>{taskStateLabel(task.state)}</Pill>
                <TaskSignalPills task={task} />
              </div>

              <section className="detail-section">
                <h3>Datos observados</h3>
                <dl className="detail-grid">
                  <div><dt>Fase o lista</dt><dd>{task.list_name ?? (wasObserved(task, "list_name") ? "Sin fase registrada" : "No observada")}</dd></div>
                  <div><dt>Responsable</dt><dd>{assigneeDisplay(task)}</dd></div>
                  <div><dt>Inicio planificado</dt><dd>{observedDate(task, "start_date", task.start_date)}</dd></div>
                  <div><dt>Vencimiento planificado</dt><dd>{observedDate(task, "due_date", task.due_date)}</dd></div>
                  <div><dt>Prioridad</dt><dd>{priorityDisplay(task)}</dd></div>
                  <div><dt>Incluida en el avance</dt><dd>{task.is_included ? "Sí" : "No"}</dd></div>
                </dl>
                {hasAssignees(task) && (
                  <p className="field-help">
                    El origen solo informa identificadores de miembro; los nombres de los responsables no están disponibles en esta fuente.
                  </p>
                )}
                {priorityUnavailableReason(task) && (
                  <p className="field-help">{priorityUnavailableReason(task)}</p>
                )}
                {task.detail && <p className="task-description">{task.detail}</p>}
              </section>

              <RiskSection task={task} diagnostic={diagnostic} onSelectTask={onSelectTask} />
              <CorrectiveActionsSection task={task} diagnostic={diagnostic} />
              <DependencySection task={task} />
              <EvidenceSection task={task} captureCount={captures.length} />
              <TaskHistorySection history={taskChanges} />

              <section className="detail-section comparison-section">
                <div className="detail-section-heading">
                  <div><h3>Cambios desde la última observación</h3><p>Compara la última captura con la anterior y muestra solo diferencias verificadas.</p></div>
                  <button
                    className="button button-ghost button-small"
                    type="button"
                    onClick={() => setShowComparison((current) => !current)}
                    aria-expanded={showComparison}
                  >
                    {showComparison ? "Ocultar" : "Ver cambios"}
                  </button>
                </div>
                {showComparison && (
                  <div className="comparison-controls">
                    {historyQuery.isPending && <LoadingState label="Cargando capturas…" />}
                    {historyQuery.isError && <p className="inline-error">No se pudo consultar el historial: {historyQuery.error.message}</p>}
                    {historyQuery.data && captures.length < 2 && (
                      <p className="field-help">Se necesitan dos capturas persistidas para comparar; no se generaron datos sintéticos.</p>
                    )}
                    {captures.length >= 2 && invalidCapturePair && (
                      <p className="field-help" role="status">Selecciona dos capturas distintas en orden cronológico (anterior y actual).</p>
                    )}
                    {captures.length >= 2 && (
                      <div className="comparison-selectors">
                        <label><span>Captura anterior</span>
                          <select value={before} onChange={(event) => setBeforeId(event.target.value)}>
                            {captures.map((capture) => <option key={capture.capture_id} value={capture.capture_id}>{formatDate(capture.captured_at, true)}</option>)}
                          </select>
                        </label>
                        <label><span>Captura actual</span>
                          <select value={after} onChange={(event) => setAfterId(event.target.value)}>
                            {captures.map((capture) => <option key={capture.capture_id} value={capture.capture_id}>{formatDate(capture.captured_at, true)}</option>)}
                          </select>
                        </label>
                      </div>
                    )}
                    {canCompare && comparisonQuery.isPending && <LoadingState label="Comparando capturas…" />}
                    {comparisonQuery.isError && <ErrorState title="No se pudo comparar" message={comparisonQuery.error.message} onRetry={() => void comparisonQuery.refetch()} />}
                    {comparison && <ComparisonDiff changes={matchingChanges} warnings={comparison.warnings} />}
                  </div>
                )}
              </section>

              <TechnicalInformationSection task={task} />
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}


/**
 * Other tasks that share the same alert. The manager reads a name and opens the task,
 * never a bare identifier, and the alert of this task stays separated from the group.
 */
function AffectedTasks({
  risk,
  currentTaskId,
  onSelectTask,
}: {
  risk: TaskRisk;
  currentTaskId: string;
  onSelectTask: (taskId: string) => void;
}) {
  const titles = risk.task_titles ?? {};
  const others = risk.task_ids.filter((taskId) => taskId !== currentTaskId);
  if (others.length === 0) {
    return <p className="field-help">Esta es la única tarea con esta alerta en la captura.</p>;
  }
  return (
    <div className="affected-tasks">
      <p className="field-help">Otras {others.length} tarea(s) con la misma alerta:</p>
      <ul className="risk-task-links">
        {others.map((taskId) => (
          <li key={taskId}>
            <button type="button" className="task-link-button" onClick={() => onSelectTask(taskId)}>
              {titles[taskId] || taskId}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Alerts of one task, explained in plain language with the observed condition and its date. */
function RiskSection({
  task,
  diagnostic,
  onSelectTask,
}: {
  task: TaskDetailResponse;
  diagnostic?: ProposalStageDiagnostic | null;
  onSelectTask: (taskId: string) => void;
}) {
  const risks = task.risks ?? [];
  const captureId = task.capture_id ?? null;
  return (
    <section className="detail-section">
      <div className="detail-section-heading">
        <div>
          <h3>¿Por qué requiere atención?</h3>
          <p>Alertas de la tarea calculadas sobre la captura actual. Cada una indica la condición observada y la fecha de referencia.</p>
        </div>
        <ShieldAlert aria-hidden="true" size={16} />
      </div>
      {risks.length === 0 ? (
        <p className="field-help">Esta tarea no tiene alertas en la captura consultada.</p>
      ) : (
        <ul className="risk-detail-list">
          {risks.map((risk) => (
            <li key={risk.risk_id}>
              <div className="risk-detail-head">
                <Pill tone={riskKindTone(risk.kind)}>{riskKindLabel(risk.kind)}</Pill>
                <span className="risk-detail-summary">Afecta a {risk.task_count} tarea(s) de la captura</span>
              </div>
              <dl className="alert-facts">
                <div><dt>Condición observada</dt><dd>{risk.summary}</dd></div>
                <div>
                  <dt>Fecha de referencia</dt>
                  <dd>{risk.cutoff_datetime ? formatDate(risk.cutoff_datetime, true) : "No disponible"}</dd>
                </div>
                <div><dt>Motivo</dt><dd>{risk.basis}</dd></div>
              </dl>
              <p className="field-help">Trazabilidad: captura {risk.capture_id} · identificación {risk.risk_id}</p>
              <AffectedTasks risk={risk} currentTaskId={task.id} onSelectTask={onSelectTask} />
              <RiskProposalLinks risk={risk} captureId={captureId} diagnostic={diagnostic} />
            </li>
          ))}
        </ul>
      )}
      {risks.length > 0 && (
        <p className="risk-panel-note">
          Una propuesta aprobada o ejecutada no demuestra que el riesgo haya desaparecido: hace falta evidencia posterior de la captura.
        </p>
      )}
    </section>
  );
}

/**
 * Corrective actions of the task: the proposals that answer one of its alerts. When there is
 * none, the absence is declared and the persisted diagnostic explains the cause when it can.
 */
function CorrectiveActionsSection({
  task,
  diagnostic,
}: {
  task: TaskDetailResponse;
  diagnostic?: ProposalStageDiagnostic | null;
}) {
  const risks = task.risks ?? [];
  const entries = risks.flatMap((risk) =>
    (risk.proposals ?? []).map((proposal) => ({ risk, proposal })));
  const unknown = risks.filter((risk) => risk.proposals === undefined);

  return (
    <section className="detail-section">
      <div className="detail-section-heading">
        <div><h3>Acciones correctivas</h3><p>Propuestas vinculadas a las alertas de esta tarea.</p></div>
        <Wrench aria-hidden="true" size={16} />
      </div>
      {risks.length === 0 ? (
        <p className="field-help">Esta tarea no tiene alertas, así que no hay acciones correctivas vinculadas.</p>
      ) : entries.length === 0 ? (
        <div className="risk-proposals">
          <p className="risk-proposals-empty">No hay una propuesta vinculada a esta tarea</p>
          {diagnostic && (
            <div className="risk-proposals-diagnostic">
              <Pill tone={proposalOutcomeTone(diagnostic.outcome)}>{proposalOutcomeLabel(diagnostic.outcome)}</Pill>
              <p>{proposalOutcomeExplanation(diagnostic)}</p>
            </div>
          )}
        </div>
      ) : (
        <ul className="risk-proposal-links">
          {entries.map(({ risk, proposal }) => (
            <li key={proposal.proposal_id} className="risk-proposal-link">
              <Link to={`/proposals?proposal_id=${encodeURIComponent(proposal.proposal_id)}`}>
                {proposal.proposal_id}
              </Link>
              <span className="risk-proposal-field">{fieldLabel(proposal.field)}</span>
              <Pill tone={riskKindTone(risk.kind)}>{riskKindLabel(risk.kind)}</Pill>
              <Pill tone={proposalStatusTone(proposal.review_status)}>{proposalReviewStatusLabel(proposal.review_status)}</Pill>
              <Pill tone={proposalStatusTone(proposal.execution_status)}>{proposalExecutionStatusLabel(proposal.execution_status)}</Pill>
              <Pill tone={proposalStatusTone(proposal.technical_status)}>{proposalTechnicalStatusLabel(proposal.technical_status)}</Pill>
              <span className="risk-proposal-meta">
                Vigencia {proposalFreshnessLabel(proposal.freshness).toLocaleLowerCase("es-EC")} · {proposalAuthorizationStatusLabel(proposal.authorization_status)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {unknown.length > 0 && (
        <p className="field-help">
          {unknown.length} alerta(s) no informan si tienen propuestas vinculadas.
        </p>
      )}
    </section>
  );
}

/**
 * Technical information kept out of the main detail and available for diagnosis and audit:
 * the internal identifiers and the list of fields the source reported.
 */
function TechnicalInformationSection({ task }: { task: TaskDetailResponse }) {
  return (
    <details className="detail-section technical-section">
      <summary>Información técnica de la tarea</summary>
      <dl className="detail-grid">
        <div><dt>Identificador de la tarea</dt><dd><code>{task.id}</code></dd></div>
        <div><dt>Identificador de fase</dt><dd><code>{task.list_id ?? "No observado"}</code></dd></div>
        <div>
          <dt>Identificadores de responsable</dt>
          <dd>{task.assignee_ids.length ? task.assignee_ids.join(", ") : "Sin responsables registrados"}</dd>
        </div>
        <div>
          <dt>Tarjeta archivada</dt>
          <dd>{task.is_archived === null || task.is_archived === undefined ? "No observado" : task.is_archived ? "Sí" : "No"}</dd>
        </div>
        <div><dt>Última actividad en Trello</dt><dd>{observedDate(task, "last_activity_at", task.last_activity_at)}</dd></div>
        <div><dt>Etiquetas</dt><dd>{task.labels.length ? task.labels.join(", ") : "Sin etiquetas"}</dd></div>
      </dl>
      <p className="field-help">
        {task.observed_fields === null || task.observed_fields === undefined
          ? "El registro no especifica qué campos fueron observados."
          : task.observed_fields.length
            ? `Campos observados: ${task.observed_fields.map(fieldLabel).join(", ")}.`
            : "No se informaron campos observados."}
      </p>
    </details>
  );
}

function DependencySection({ task }: { task: TaskDetailResponse }) {
  return (
    <section className="detail-section">
      <h3>Dependencias explícitas</h3>
      {task.explicit_dependencies.length === 0 ? (
        <p className="field-help">El backend no reporta dependencias explícitas para esta tarea.</p>
      ) : (
        <>
          <ul className="dependency-list">
            {task.explicit_dependencies.map((dependency) => <li key={dependency}><code>{dependency}</code></li>)}
          </ul>
          {task.dependency_impacts.length > 0 && (
            <ul className="dependency-impact-list">
              {task.dependency_impacts.map((impact, index) => <li key={`${index}-${impact}`}>{impact}</li>)}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

/** Observations of the same origin card, ordered from the oldest to the most recent. */
interface EvidenceGroup {
  key: string;
  source: string;
  items: EvidenceItem[];
}

function groupEvidence(items: EvidenceItem[]): EvidenceGroup[] {
  const groups = new Map<string, EvidenceGroup>();
  for (const item of items) {
    const cardId = item.provenance.external_id ?? null;
    const key = `${item.provenance.source}:${cardId ?? item.provenance.id}`;
    const group = groups.get(key);
    if (group) {
      group.items.push(item);
      continue;
    }
    groups.set(key, { key, source: item.provenance.source, items: [item] });
  }
  return [...groups.values()].map((group) => ({
    ...group,
    items: [...group.items].sort(
      (left, right) => new Date(left.provenance.timestamp).getTime() - new Date(right.provenance.timestamp).getTime(),
    ),
  }));
}

/** Changes the backend verified between captures, indexed by the evidence that supports them. */
function verifiedChangesByEvidence(history: TaskChange[] | undefined) {
  const index = new Map<string, TaskChange[]>();
  for (const change of history ?? []) {
    for (const evidenceId of [...change.previous_evidence_ids, ...change.current_evidence_ids]) {
      const entries = index.get(evidenceId) ?? [];
      entries.push(change);
      index.set(evidenceId, entries);
    }
  }
  return index;
}

function verifiedChangesOf(group: EvidenceGroup, index: Map<string, TaskChange[]>) {
  const seen = new Set<string>();
  const changes: TaskChange[] = [];
  for (const item of group.items) {
    for (const change of index.get(item.provenance.id) ?? []) {
      if (seen.has(change.id)) continue;
      seen.add(change.id);
      changes.push(change);
    }
  }
  return changes;
}

/**
 * Evidence of one task. Every historical record is kept, but the observations of the same
 * card are grouped and ordered, and only the differences the backend verified are presented
 * as changes: a new observation of unchanged data is never shown as a change.
 */
function EvidenceSection({ task, captureCount }: { task: TaskDetailResponse; captureCount: number }) {
  const groups = task.evidence === undefined ? [] : groupEvidence(task.evidence);
  const changesIndex = verifiedChangesByEvidence(task.history);
  const comparable = captureCount >= 2;

  return (
    <section className="detail-section">
      <div className="detail-section-heading">
        <div><h3>Evidencias de Trello</h3><p>Una entrada por tarjeta de origen, con el historial de observaciones.</p></div>
        <ExternalLink aria-hidden="true" size={16} />
      </div>
      {task.evidence === undefined ? (
        <p className="unknown-note"><CircleHelp aria-hidden="true" size={16} /> La respuesta no incluye información de evidencia para esta tarea.</p>
      ) : groups.length === 0 ? (
        <p className="field-help">No hay elementos de evidencia asociados en esta respuesta.</p>
      ) : (
        <ul className="evidence-groups">
          {groups.map((group) => {
            const latest = group.items[group.items.length - 1];
            const verified = verifiedChangesOf(group, changesIndex);
            return (
              <li key={group.key} className="evidence-group">
                <div className="evidence-group-head">
                  <span className="evidence-source">{group.source}</span>
                  <strong>{latest.summary}</strong>
                  <span>Observación más reciente: {formatDate(latest.provenance.timestamp, true)}</span>
                </div>
                {verified.length > 0 ? (
                  <div className="evidence-verified">
                    <p className="evidence-verified-title">Cambios comprobados ({verified.length}):</p>
                    <ul className="change-list">
                      {verified.map((change) => (
                        <li key={change.id}>
                          <span className="change-marker" aria-hidden="true" />
                          <div>
                            <strong>{fieldLabel(change.field)}</strong>
                            <p>
                              {comparisonValue(change.before, change.field)} <span aria-hidden="true">→</span> {comparisonValue(change.after, change.field)}
                            </p>
                            {change.observed_at && <small>Comprobado el {formatDate(change.observed_at, true)}</small>}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : group.items.length > 1 ? (
                  <p className="field-help">
                    {comparable
                      ? `Se registraron ${group.items.length} observaciones y ninguna diferencia comprobada entre capturas: los datos observados no cambiaron.`
                      : `Se registraron ${group.items.length} observaciones de la misma captura; hace falta otra captura para comprobar cambios.`}
                  </p>
                ) : (
                  <p className="field-help">Una sola observación; no hay cambios comprobados.</p>
                )}
                {group.items.length > 1 && (
                  <details className="evidence-history">
                    <summary>Historial de observaciones ({group.items.length})</summary>
                    <ol className="evidence-timeline">
                      {group.items.map((item) => (
                        <li key={item.provenance.id}>
                          <span>{formatDate(item.provenance.timestamp, true)}</span>
                          <span>{item.summary}</span>
                          <code>Ref. {item.provenance.id}</code>
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}


function TaskHistorySection({ history }: { history?: TaskChange[] }) {
  return (
    <section className="detail-section">
      <div className="detail-section-heading">
        <div><h3>Historial de cambios</h3><p>Diferencias verificadas entre capturas consecutivas. Los cambios no se atribuyen a una persona sin identidad verificada.</p></div>
        <Clock3 aria-hidden="true" size={16} />
      </div>
      {history === undefined ? (
        <p className="unknown-note"><CircleHelp aria-hidden="true" size={16} /> El historial de cambios no está disponible en esta respuesta.</p>
      ) : history.length === 0 ? (
        <p className="field-help">No se verificaron cambios entre las capturas disponibles.</p>
      ) : (
        <ul className="change-list">
          {history.map((change) => (
            <li key={change.id}>
              <span className="change-marker" aria-hidden="true" />
              <div>
                <strong>{fieldLabel(change.field)}</strong>
                <p>{comparisonValue(change.before, change.field)} <span aria-hidden="true">→</span> {comparisonValue(change.after, change.field)}</p>
                {change.observed_at && <small>Captura observada: {formatDate(change.observed_at, true)}</small>}
                {(change.previous_capture_id || change.current_capture_id || change.capture_id) && (
                  <small>Captura(s): {[change.previous_capture_id, change.current_capture_id].filter(Boolean).join(" → ") || change.capture_id}</small>
                )}
                <small>Autor: {change.actor === null ? "no determinado" : "no informado"}</small>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ComparisonDiff({ changes, warnings }: {
  changes: TaskChange[];
  warnings: string[];
}) {
  const warningLabels: Record<string, string> = {
    unknown_task_scope: "Alcance de tareas desconocido",
    limited_task_enumeration: "Enumeración de tareas limitada",
    conflicting_task_evidence: "Evidencia contradictoria",
  };
  return (
    <div className="comparison-result" aria-live="polite">
      {warnings.length > 0 && (
        <div className="comparison-warnings">
          {warnings.map((warning) => <Pill key={warning} tone="warning">{warningLabels[warning] ?? warning}</Pill>)}
        </div>
      )}
      {changes.length === 0 ? (
        <p className="unknown-note"><CircleHelp aria-hidden="true" size={16} /> No se verificaron diferencias de esta tarea entre las dos capturas.</p>
      ) : (
        <ul className="visual-diff-list">
          {changes.map((change) => (
            <li key={change.id}>
              <span className="diff-field">{fieldLabel(change.field)}</span>
              <div className="diff-values">
                <span className="diff-before"><del>{comparisonValue(change.before, change.field)}</del></span>
                <span aria-hidden="true" className="diff-arrow">→</span>
                <span className="diff-after">{comparisonValue(change.after, change.field)}</span>
              </div>
              {(change.previous_evidence_ids.length > 0 || change.current_evidence_ids.length > 0) && (
                <small>Evidencia: {[...change.previous_evidence_ids, ...change.current_evidence_ids].join(", ")}</small>
              )}
              <small>Autor del cambio: no determinado</small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SortHeader({
  label,
  sortKey,
  activeSort,
  direction,
  onSort,
}: {
  label: string;
  sortKey: "title" | "due_date";
  activeSort: string;
  direction: string;
  onSort: (sort: "title" | "due_date") => void;
}) {
  const active = activeSort === sortKey;
  return (
    <th aria-sort={active ? direction === "asc" ? "ascending" : "descending" : "none"}>
      <button className="sort-button" type="button" onClick={() => onSort(sortKey)}>
        {label}
        {active && (direction === "asc" ? <ArrowUp aria-hidden="true" size={13} /> : <ArrowDown aria-hidden="true" size={13} />)}
      </button>
    </th>
  );
}

export function TasksPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const searchParamsRef = useRef(searchParams);
  const pendingSearchStringRef = useRef<string | null>(null);
  const { selectedProjectId } = useWorkspace();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const needsProjectLookup = Boolean(selectedProjectId && !listedProject);
  const selectedProjectQuery = useProject(selectedProjectId, needsProjectLookup);
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const filters = useMemo(() => taskFilters(searchParams, project?.summary?.capture_id), [searchParams, project?.summary?.capture_id]);
  const tasksQuery = useQuery({
    queryKey: queryKeys.tasks(project?.id ?? "", filters),
    queryFn: () => api.getTasks(project!.id, filters),
    enabled: Boolean(project),
    placeholderData: (previous) => previous,
  });
  const summaryQuery = useProjectSummary(project?.id ?? null);
  const diagnostic = summaryQuery.data?.proposals_context?.diagnostic ?? null;
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const deepLinkedTaskId = searchParams.get("task") ?? "";
  const deepLinkedCaptureId = searchParams.get("capture_id") ?? "";
  const openTaskId = selectedTaskId ?? (deepLinkedTaskId || null);
  const search = searchParams.get("q") ?? "";
  const state = queryState(searchParams.get("state")) ?? "";
  const assignee = searchParams.get("assignee_id") ?? "";
  const phase = searchParams.get("list_name") ?? "";
  const criticalOnly = searchParams.get("critical_only") === "true";
  const overdueOnly = searchParams.get("overdue_only") === "true";
  const dueSoonOnly = searchParams.get("due_soon_only") === "true";
  const sort = filters.sort ?? "due_date";
  const direction = filters.direction ?? "asc";

  function updateFilters(values: Record<string, string | null>, resetPage = true) {
    const next = new URLSearchParams(searchParamsRef.current);
    for (const [key, value] of Object.entries(values)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    if (resetPage) next.delete("page");
    searchParamsRef.current = next;
    pendingSearchStringRef.current = next.toString();
    setSearchParams(next, { replace: true });
  }

  function toggleBooleanFilter(key: "critical_only" | "overdue_only" | "due_soon_only", checked: boolean) {
    updateFilters({ [key]: checked ? "true" : null });
  }

  function changeSort(nextSort: "title" | "due_date") {
    const nextDirection = sort === nextSort && direction === "asc" ? "desc" : "asc";
    updateFilters({ sort: nextSort, direction: nextDirection });
  }

  useEffect(() => {
    const actual = searchParams.toString();
    if (pendingSearchStringRef.current === null || pendingSearchStringRef.current === actual) {
      searchParamsRef.current = searchParams;
      pendingSearchStringRef.current = null;
    }
  }, [searchParams]);

  const totalPages = tasksQuery.data ? Math.max(1, Math.ceil(tasksQuery.data.total / Math.max(1, tasksQuery.data.page_size))) : 1;
  const currentPage = filters.page;

  if (projectsQuery.isPending) return <LoadingState label="Cargando proyecto activo…" />;
  if (needsProjectLookup && selectedProjectQuery.isPending) return <LoadingState label="Consultando proyecto activo…" />;
  if (needsProjectLookup && selectedProjectQuery.isError) return <ErrorState title="No se pudo cargar el proyecto activo" message={selectedProjectQuery.error.message} onRetry={() => void selectedProjectQuery.refetch()} />;
  if (projectsQuery.isError) return <ErrorState title="No se pudieron cargar los proyectos" message={projectsQuery.error.message} onRetry={() => void projectsQuery.refetch()} />;
  if (!project) return <EmptyState title="No hay un proyecto activo" message="El backend no reportó proyectos para consultar sus tareas." />;

  return (
    <section className="tasks-page">
      <div className="page-heading-row">
        <div>
          <p className="eyebrow">SEGUIMIENTO · {project.name}</p>
          <h1>Tareas</h1>
          <p className="page-subtitle">Filtra tareas observadas por el backend. Los valores nulos se mantienen como desconocidos.</p>
        </div>
        <span className="task-total-label"><ListFilter aria-hidden="true" size={15} /> {tasksQuery.data ? `${tasksQuery.data.total} resultados` : "Consulta en curso"}</span>
      </div>

      <section className="panel task-filters" aria-labelledby="filter-heading">
        <div className="filter-heading-row">
          <h2 id="filter-heading"><Filter aria-hidden="true" size={17} /> Filtros</h2>
          <button className="button button-ghost button-small" type="button" onClick={() => updateFilters({ q: null, state: null, assignee_id: null, list_name: null, critical_only: null, overdue_only: null, due_soon_only: null, sort: null, direction: null })}>
            Limpiar filtros
          </button>
        </div>
        <div className="filter-grid">
          <label className="filter-search">
            <span>Buscar</span>
            <span className="input-with-icon"><Search aria-hidden="true" size={16} /><input aria-label="Buscar tareas" type="search" value={search} placeholder="Título o texto observado" onChange={(event) => updateFilters({ q: event.target.value })} /></span>
          </label>
          <label><span>Estado</span>
            <select aria-label="Estado" value={state} onChange={(event) => updateFilters({ state: event.target.value || null })}>
              <option value="">Todos los estados</option>
              <option value="pending">Pendiente</option>
              <option value="en_curso">En curso</option>
              <option value="completada">Completada</option>
              <option value="bloqueada">Bloqueada</option>
              <option value="desconocido">Desconocido</option>
            </select>
          </label>
          <label><span>ID de responsable</span><input aria-label="ID de responsable" type="search" value={assignee} placeholder="Identificador observado" onChange={(event) => updateFilters({ assignee_id: event.target.value })} /></label>
          <label><span>Fase o lista</span><input aria-label="Fase o lista" type="search" value={phase} placeholder="Nombre de la lista" onChange={(event) => updateFilters({ list_name: event.target.value })} /></label>
        </div>
        <div className="filter-toggles">
          <label className="check-filter"><input aria-label="Solo con alertas" type="checkbox" checked={criticalOnly} onChange={(event) => toggleBooleanFilter("critical_only", event.target.checked)} /><span>Solo con alertas</span></label>
          <label className="check-filter"><input aria-label="Solo vencidas" type="checkbox" checked={overdueOnly} onChange={(event) => toggleBooleanFilter("overdue_only", event.target.checked)} /><span>Solo vencidas</span></label>
          <label className="check-filter"><input aria-label="Próximas a vencer" type="checkbox" checked={dueSoonOnly} onChange={(event) => toggleBooleanFilter("due_soon_only", event.target.checked)} /><span>Próximas a vencer</span></label>
        </div>
      </section>

      {tasksQuery.isPending && <LoadingState label="Consultando tareas…" />}
      {tasksQuery.isError && <ErrorState title="No se pudieron cargar las tareas" message={tasksQuery.error.message} onRetry={() => void tasksQuery.refetch()} />}
      {tasksQuery.data && tasksQuery.data.items.length === 0 && (
        <EmptyState title="No se encontraron tareas" message="No hay tareas que coincidan con los filtros actuales o la API no devolvió resultados." />
      )}
      {tasksQuery.data && tasksQuery.data.items.length > 0 && (
        <div className="panel task-table-panel">
          <div className="table-scroll">
            <table className="task-table">
              <thead><tr>
                <th scope="col">Estado</th>
                <SortHeader label="Tarea" sortKey="title" activeSort={sort} direction={direction} onSort={changeSort} />
                <th scope="col">Fase / lista</th>
                <th scope="col">Responsable</th>
                <SortHeader label="Vencimiento" sortKey="due_date" activeSort={sort} direction={direction} onSort={changeSort} />
                <th scope="col">Señales</th>
              </tr></thead>
              <tbody>
                {tasksQuery.data.items.map((task) => (
                  <tr key={task.id}>
                    <td><Pill tone={taskStateTone(task.state)}>{taskStateLabel(task.state)}</Pill></td>
                    <td className="task-title-cell">
                      <button className="task-title-button" type="button" aria-label={`Ver detalle de ${task.title}`} onClick={() => setSelectedTaskId(task.id)}>
                        <strong>{task.title}</strong><code>{task.id}</code>
                      </button>
                    </td>
                    <td>{task.list_name ?? "No observada"}</td>
                    <td>{responsibleDisplay(task)}</td>
                    <td>
                      <span className="task-due-date">{formatDate(task.due_date)}</span>
                      {plannedDates(task)}
                    </td>
                    <td><div className="task-signals"><TaskSignalPills task={task} compact /></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-footer">
            <span>Mostrando página {currentPage} de {totalPages} · {tasksQuery.data.total} tareas</span>
            <div className="pagination-controls">
              <button className="button button-secondary button-small" type="button" disabled={currentPage <= 1} aria-label="Página anterior" onClick={() => updateFilters({ page: String(currentPage - 1) }, false)}><ChevronLeft aria-hidden="true" size={15} /> Anterior</button>
              <button className="button button-secondary button-small" type="button" disabled={currentPage >= totalPages} aria-label="Página siguiente" onClick={() => updateFilters({ page: String(currentPage + 1) }, false)}>Siguiente <ChevronRight aria-hidden="true" size={15} /></button>
            </div>
          </div>
        </div>
      )}

      <TaskDetailDialog
        projectId={project.id}
        taskId={openTaskId}
        captureId={deepLinkedCaptureId || project.summary?.capture_id}
        diagnostic={diagnostic}
        onSelectTask={setSelectedTaskId}
        onClose={() => {
          setSelectedTaskId(null);
          if (deepLinkedTaskId || deepLinkedCaptureId) updateFilters({ task: null, capture_id: null }, false);
        }}
      />
    </section>
  );
}
