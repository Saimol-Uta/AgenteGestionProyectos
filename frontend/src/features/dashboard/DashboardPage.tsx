import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  AlertCircle, ArrowDownRight, ArrowRight, CalendarClock, CheckCircle2, CircleHelp,
  CircleSlash2, Clock3, Layers3, ListTodo, ShieldAlert,
} from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip as ChartTooltip,
  XAxis, YAxis,
} from "recharts";
import { api } from "../../api/client";
import { queryKeys, useCaptureHistory, useProject, useProjectSummary, useProjects } from "../../api/queries";
import { formatDate, formatPercentage, riskKindLabel, riskKindTone, taskStateLabel, taskStateTone } from "../../api/formatters";
import { useWorkspace } from "../../app/WorkspaceContext";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import type { DomainTaskState, TaskDetailView, TaskPlanSummary, TaskQuery } from "../../api/types";
import { DecisionCenter } from "../manager/DecisionCenter";
import { ExecutiveInsights } from "./ExecutiveInsights";

const numberFormatter = new Intl.NumberFormat("es-EC");

interface MetricDefinition {
  label: string;
  value: number | string | null;
  caption: string;
  icon: typeof ListTodo;
  href: string;
  tone: "indigo" | "green" | "amber" | "red" | "slate";
  loading?: boolean;
}

function MetricCard({ metric }: { metric: MetricDefinition }) {
  const Icon = metric.icon;
  return (
    <Link className="metric-card metric-card-link" to={metric.href}>
      <div className="metric-card-top">
        <div className="metric-card-leading">
          <span className={`metric-icon metric-icon-${metric.tone}`}><Icon aria-hidden="true" size={14} /></span>
          <span className="metric-label">{metric.label}</span>
        </div>
        <ArrowRight className="metric-arrow" aria-hidden="true" size={13} />
      </div>
      <div className="metric-card-main">
        <strong className="metric-value">
          {metric.loading ? "Cargando…" : metric.value === null ? "Sin dato" : typeof metric.value === "number" ? numberFormatter.format(metric.value) : metric.value}
        </strong>
        <span className="metric-caption">{metric.caption}</span>
      </div>
    </Link>
  );
}

function stateCount(summary: TaskPlanSummary, state: DomainTaskState): number | null {
  const count = summary.state_counts?.[state];
  return typeof count === "number" && Number.isFinite(count) ? count : null;
}

function reportedCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function metricsFor(summary: TaskPlanSummary, criticalCount: number | null, criticalLoading: boolean, criticalFailed: boolean): MetricDefinition[] {
  return [
    { label: "Tareas registradas", value: reportedCount(summary.total_tasks), caption: "En la captura actual", icon: ListTodo, href: "/tasks", tone: "indigo" },
    { label: "Completadas", value: reportedCount(summary.completed_tasks), caption: "Según el estado del dominio", icon: CheckCircle2, href: "/tasks?state=completada", tone: "green" },
    { label: "Pendientes", value: stateCount(summary, "pending"), caption: "Conteo informado por estado", icon: Clock3, href: "/tasks?state=pending", tone: "slate" },
    { label: "En curso", value: stateCount(summary, "en_curso"), caption: "Conteo informado por estado", icon: ArrowDownRight, href: "/tasks?state=en_curso", tone: "indigo" },
    { label: "Avance observado", value: formatPercentage(summary.progress_percentage), caption: "Ver tareas completadas", icon: ArrowDownRight, href: "/tasks?state=completada", tone: "slate" },
    { label: "Vencidas", value: reportedCount(summary.overdue_count), caption: "Al corte de esta captura", icon: CalendarClock, href: "/tasks?overdue_only=true", tone: "red" },
    { label: "Próximas a vencer", value: reportedCount(summary.due_soon_count), caption: "Ventana definida por el backend", icon: AlertCircle, href: "/tasks?due_soon_only=true", tone: "amber" },
    { label: "Bloqueadas", value: reportedCount(summary.blocked_count), caption: "Estado reportado por el backend", icon: ShieldAlert, href: "/tasks?state=bloqueada", tone: "amber" },
    { label: "Actividades críticas", value: criticalCount, caption: criticalFailed ? "No disponible; consulta el detalle para reintentar" : "Total filtrado por la API; puede coincidir con otras alertas", icon: ShieldAlert, href: "/tasks?critical_only=true", tone: "red", loading: criticalLoading },
    { label: "Estado desconocido", value: reportedCount(summary.unknown_count), caption: "Sin mapeo o evidencia suficiente", icon: CircleHelp, href: "/tasks?state=desconocido", tone: "slate" },
  ];
}

const stateOrder: DomainTaskState[] = ["pending", "en_curso", "completada", "bloqueada", "desconocido"];

function stateColor(state: string) {
  switch (state) {
    case "en_curso": return "var(--chart-primary)";
    case "completada": return "var(--chart-secondary)";
    case "bloqueada": return "var(--danger)";
    case "desconocido": return "var(--warning)";
    default: return "var(--text-muted)";
  }
}

function StateDistributionChart({ summary }: { summary: TaskPlanSummary }) {
  const counts = summary.state_counts ?? {};
  const knownStates = stateOrder.filter((state) => Object.hasOwn(counts, state));
  const otherStates = Object.keys(counts).filter((state) => !stateOrder.includes(state as DomainTaskState));
  const data = [...knownStates, ...otherStates].flatMap((state) => {
    const count = counts[state];
    return typeof count === "number" && Number.isFinite(count)
      ? [{ state, label: taskStateLabel(state), count }]
      : [];
  });

  if (summary.total_tasks === 0) {
    return <EmptyState title="Sin tareas en esta captura" message="El backend informa cero tareas; no se dibuja una distribución vacía." />;
  }
  if (data.length === 0) {
    return <EmptyState title="Distribución no disponible" message="El resumen no incluye conteos de estado utilizables." />;
  }

  return (
    <>
      <div className="chart-frame" role="img" aria-label="Distribución de tareas por estado">
        <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={170} initialDimension={{ width: 600, height: 185 }}>
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -14 }}>
            <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "var(--text-muted)", fontSize: 10 }} />
            <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
            <ChartTooltip
              cursor={{ fill: "var(--chart-hover)" }}
              contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border-strong)", borderRadius: 10, color: "var(--text-primary)" }}
              formatter={(value) => [typeof value === "number" ? numberFormatter.format(value) : "Sin dato", "Tareas reportadas"]}
            />
            <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={42}>
              {data.map((item) => <Cell key={item.state} fill={stateColor(item.state)} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ul className="chart-data-list" aria-label="Conteos de tareas reportados por estado">
        {data.map((item) => (
          <li key={item.state}>
            <span><i className="chart-data-marker" style={{ backgroundColor: stateColor(item.state) }} />{item.label}</span>
            <strong>{numberFormatter.format(item.count)}</strong>
          </li>
        ))}
      </ul>
    </>
  );
}

function CriticalSignalsChart({ summary, criticalCount }: { summary: TaskPlanSummary; criticalCount: number | null }) {
  const reportedSignals = [
    { key: "overdue", label: "Vencidas", value: summary.overdue_count, color: "var(--danger)" },
    { key: "due-soon", label: "Próximas", value: summary.due_soon_count, color: "var(--warning)" },
    { key: "blocked", label: "Bloqueadas", value: summary.blocked_count, color: "var(--chart-secondary)" },
    ...(criticalCount === null ? [] : [{ key: "critical", label: "Críticas", value: criticalCount, color: "var(--chart-primary)" }]),
  ];
  const data = reportedSignals.flatMap((signal) => {
    const value = reportedCount(signal.value);
    return value === null ? [] : [{ ...signal, value }];
  });

  if (data.length === 0) {
    return <EmptyState title="Señales críticas no disponibles" message="El resumen y el filtro de tareas no proporcionaron conteos utilizables." />;
  }

  return (
    <>
      <div className="chart-frame" role="img" aria-label="Señales de actividad crítica por categoría">
        <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={170} initialDimension={{ width: 600, height: 185 }}>
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -14 }}>
            <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "var(--text-muted)", fontSize: 10 }} />
            <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
            <ChartTooltip
              cursor={{ fill: "var(--chart-hover)" }}
              contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border-strong)", borderRadius: 10, color: "var(--text-primary)" }}
              formatter={(value) => [typeof value === "number" ? numberFormatter.format(value) : "Sin dato", "Registros de la API"]}
            />
            <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={42}>
              {data.map((signal) => <Cell key={signal.key} fill={signal.color} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ul className="chart-data-list" aria-label="Conteos de señales críticas reportados por la API">
        {data.map((signal) => (
          <li key={signal.key}>
            <span><i className="chart-data-marker" style={{ backgroundColor: signal.color }} />{signal.label}</span>
            <strong>{numberFormatter.format(signal.value)}</strong>
          </li>
        ))}
      </ul>
    </>
  );
}

function CriticalTaskList({
  items,
  total,
  loading,
  failed,
  errorMessage,
  onRetry,
}: {
  items: TaskDetailView[];
  total: number | null;
  loading: boolean;
  failed: boolean;
  errorMessage?: string;
  onRetry: () => void;
}) {
  if (loading) return <LoadingState label="Consultando tareas críticas en la API…" />;
  if (failed) return <ErrorState title="No se pudieron cargar las tareas críticas" message={errorMessage ?? "La API no entregó esta consulta."} onRetry={onRetry} />;
  if (total === 0) return <EmptyState title="Sin tareas críticas reportadas" message="El filtro de criticidad de la API no devolvió registros en esta captura." />;
  if (total === null) return <EmptyState title="Tareas críticas no disponibles" message="No se interpreta una respuesta ausente como cero." />;

  return (
    <ul className="critical-task-list">
      {items.map((task) => (
        <li key={task.id}>
          <Link to={`/tasks?critical_only=true&q=${encodeURIComponent(task.title)}`}>
            <span className="critical-task-title">{task.title}</span>
            <span className="critical-task-meta">
              <Pill tone={taskStateTone(task.state)}>{taskStateLabel(task.state)}</Pill>
              <span>{task.due_date ? `Vence ${formatDate(task.due_date)}` : "Fecha límite sin dato"}</span>
            </span>
          </Link>
        </li>
      ))}
      <li className="critical-task-footer">
        <Link to="/tasks?critical_only=true">Ver las {numberFormatter.format(total)} tareas críticas reportadas <ArrowRight aria-hidden="true" size={14} /></Link>
      </li>
    </ul>
  );
}

function shortCaptureDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Fecha no válida";
  return new Intl.DateTimeFormat("es-EC", {
    timeZone: "America/Guayaquil",
    month: "short",
    day: "numeric",
  }).format(date);
}

function CaptureCharts({ captures }: { captures: Array<{ captured_at: string; summary: TaskPlanSummary }> }) {
  if (captures.length === 0) {
    return <EmptyState title="Sin capturas históricas" message="La API todavía no reporta resúmenes de capturas para este proyecto." />;
  }
  if (captures.length === 1) {
    return <EmptyState title="Una sola captura disponible" message="Se muestra su resumen actual arriba. No se dibuja una serie temporal con una sola observación." />;
  }

  const points = captures.map(({ captured_at, summary }) => ({
    date: shortCaptureDate(captured_at),
    timestamp: new Date(captured_at).getTime(),
    captured_at,
    progress: summary.progress_percentage,
    total: summary.total_tasks,
  }));

  return (
    <div className="chart-grid">
      <article className="chart-card">
        <div className="chart-heading">
          <div><h3>Avance por captura</h3><p>Porcentaje informado en cada resumen histórico.</p></div>
          <Pill tone="accent">{captures.length} capturas</Pill>
        </div>
        <div className="chart-frame" role="img" aria-label="Gráfico de avance porcentual por captura histórica">
          <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={170} initialDimension={{ width: 600, height: 185 }}>
            <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -14 }}>
              <defs>
                <linearGradient id="progressFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--chart-primary)" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="var(--chart-primary)" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" vertical={false} />
              <XAxis type="number" dataKey="timestamp" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={(value: number) => shortCaptureDate(new Date(value).toISOString())} tickLine={false} axisLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <YAxis domain={[0, 100]} tickLine={false} axisLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickFormatter={(value: number) => `${value}%`} />
              <ChartTooltip
                cursor={{ stroke: "var(--chart-grid)" }}
                contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border-strong)", borderRadius: 10, color: "var(--text-primary)" }}
                labelFormatter={(_, payload) => payload?.[0]?.payload?.captured_at ? formatDate(payload[0].payload.captured_at, true) : "Captura"}
                formatter={(value) => [formatPercentage(typeof value === "number" ? value : null), "Avance"]}
              />
              <Area type="linear" dataKey="progress" stroke="var(--chart-primary)" strokeWidth={2.5} fill="url(#progressFill)" connectNulls={false} dot={{ r: 3, fill: "var(--chart-primary)", strokeWidth: 0 }} activeDot={{ r: 5 }} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </article>

      <article className="chart-card">
        <div className="chart-heading">
          <div><h3>Tareas por captura</h3><p>Total incluido en cada resumen histórico.</p></div>
          <Pill tone="neutral">Conteos del backend</Pill>
        </div>
        <div className="chart-frame" role="img" aria-label="Gráfico del total de tareas por captura histórica">
          <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={170} initialDimension={{ width: 600, height: 185 }}>
            <BarChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -14 }}>
              <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" vertical={false} />
              <XAxis dataKey="date" tickLine={false} axisLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <ChartTooltip
                cursor={{ fill: "var(--chart-hover)" }}
                contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border-strong)", borderRadius: 10, color: "var(--text-primary)" }}
                labelFormatter={(_, payload) => payload?.[0]?.payload?.captured_at ? formatDate(payload[0].payload.captured_at, true) : "Captura"}
                formatter={(value) => [typeof value === "number" ? numberFormatter.format(value) : "Sin dato", "Tareas"]}
              />
              <Bar dataKey="total" fill="var(--chart-secondary)" radius={[4, 4, 0, 0]} maxBarSize={36} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </article>
    </div>
  );
}

export function DashboardPage() {
  const projectsQuery = useProjects();
  const { selectedProjectId } = useWorkspace();
  const projects = projectsQuery.data?.items ?? [];
  const listedProject = projects.find((item) => item.id === selectedProjectId);
  const needsProjectLookup = Boolean(selectedProjectId && !listedProject);
  const selectedProjectQuery = useProject(selectedProjectId, needsProjectLookup);
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projects[0] ?? null : null);
  const summaryQuery = useProjectSummary(project?.id ?? null);
  const historyQuery = useCaptureHistory(project?.id ?? null);
  const captureRisks = summaryQuery.data?.risks ?? [];
  const criticalFilter = useMemo<TaskQuery>(() => ({
    capture_id: summaryQuery.data?.capture_id,
    critical_only: true,
    sort: "due_date",
    direction: "asc",
    page: 1,
    page_size: 5,
  }), [summaryQuery.data?.capture_id]);
  const criticalTasksQuery = useQuery({
    queryKey: queryKeys.tasks(project?.id ?? "", criticalFilter),
    queryFn: () => api.getTasks(project!.id, criticalFilter),
    enabled: Boolean(project && summaryQuery.data),
  });

  const captures = useMemo(() => {
    return [...(historyQuery.data?.items ?? [])].sort((left, right) => {
      return new Date(left.captured_at).getTime() - new Date(right.captured_at).getTime();
    });
  }, [historyQuery.data?.items]);

  if (projectsQuery.isPending) return <LoadingState label="Cargando proyectos del backend…" />;
  if (needsProjectLookup && selectedProjectQuery.isPending) return <LoadingState label="Consultando proyecto activo…" />;
  if (needsProjectLookup && selectedProjectQuery.isError) return <ErrorState title="No se pudo cargar el proyecto activo" message={selectedProjectQuery.error.message} onRetry={() => void selectedProjectQuery.refetch()} />;
  if (projectsQuery.isError) {
    return <ErrorState title="No se pudo cargar el proyecto" message={projectsQuery.error.message} onRetry={() => void projectsQuery.refetch()} />;
  }
  if (!project) {
    return <EmptyState title="No hay proyectos configurados" message="El backend no reportó ningún proyecto que pueda mostrarse en el dashboard." />;
  }

  return (
    <section className="dashboard-page">
      <div className="page-heading-row dashboard-heading">
        <div>
          <p className="eyebrow">VISTA GENERAL · {project.name}</p>
          <h1>Resumen del proyecto</h1>
          <p className="page-subtitle">Indicadores de planificación y capturas históricas, sin estimaciones locales.</p>
        </div>
        <Link className="button button-secondary" to="/tasks">
          <ListTodo aria-hidden="true" size={16} /> Ver tareas <ArrowRight aria-hidden="true" size={15} />
        </Link>
      </div>

      <DecisionCenter projectId={project.id} />

      {summaryQuery.isPending && <LoadingState label="Consultando métricas de la captura…" />}
      {summaryQuery.isError && (
        <ErrorState
          title="No se pudo cargar el resumen"
          message={summaryQuery.error.message}
          onRetry={() => void summaryQuery.refetch()}
        />
      )}
      {summaryQuery.data && (
        <>
          <div className="capture-note">
            <span className="capture-note-icon"><Clock3 aria-hidden="true" size={16} /></span>
            <span>Captura analizada: <strong>{formatDate(summaryQuery.data.captured_at, true)}</strong></span>
            <span className="capture-note-divider" aria-hidden="true" />
            <span>Zona horaria: <strong>America/Guayaquil</strong></span>
          </div>

          <div className="metric-grid">
            {metricsFor(
              summaryQuery.data,
              criticalTasksQuery.isError ? null : reportedCount(criticalTasksQuery.data?.total),
              criticalTasksQuery.isPending,
              criticalTasksQuery.isError,
            ).map((metric) => <MetricCard key={metric.label} metric={metric} />)}
          </div>

          <section className="panel executive-signals" aria-labelledby="signals-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">ESTADOS Y ALERTAS</p>
                <h2 id="signals-heading">Distribución y señales críticas</h2>
                <p className="section-subtitle">Conteos leídos de la captura y filtros del backend; no se recalculan en el navegador.</p>
              </div>
            </div>
            <div className="chart-grid">
              <article className="chart-card">
                <div className="chart-heading">
                  <div><h3>Tareas por estado</h3><p>Valores exactos de state_counts en la captura.</p></div>
                  <Pill tone="neutral">Resumen API</Pill>
                </div>
                <StateDistributionChart summary={summaryQuery.data} />
              </article>
              <article className="chart-card">
                <div className="chart-heading">
                  <div><h3>Actividad crítica</h3><p>Conteos de vencimiento, plazo, bloqueo y criticidad.</p></div>
                  <Pill tone="warning">Categorías solapadas</Pill>
                </div>
                <CriticalSignalsChart
                  summary={summaryQuery.data}
                  criticalCount={criticalTasksQuery.isError ? null : reportedCount(criticalTasksQuery.data?.total)}
                />
                <p className="signal-overlap-note">Las categorías de alerta se solapan; no deben sumarse como un total.</p>
              </article>
            </div>
            <div className="critical-activity-list">
              <div className="chart-heading">
                <div><h3>Tareas críticas reportadas</h3><p>Registros devueltos por el filtro de criticidad de la API.</p></div>
                <Link className="text-link" to="/tasks?critical_only=true">Abrir filtro <ArrowRight aria-hidden="true" size={14} /></Link>
              </div>
              <CriticalTaskList
                items={criticalTasksQuery.data?.items ?? []}
                total={criticalTasksQuery.isError ? null : reportedCount(criticalTasksQuery.data?.total)}
                loading={criticalTasksQuery.isPending}
                failed={criticalTasksQuery.isError}
                errorMessage={criticalTasksQuery.error?.message}
                onRetry={() => void criticalTasksQuery.refetch()}
              />
            </div>
          </section>

          <section className="panel alert-shortcuts" aria-labelledby="alerts-heading">
            <div className="section-heading compact-heading">
              <div>
                <p className="eyebrow">CONSULTA DE TAREAS</p>
                <h2 id="alerts-heading">Atajos de seguimiento</h2>
              </div>
              <span className="caption">Los filtros se ejecutan en la API.</span>
            </div>
            <div className="shortcut-list">
              <Link to="/tasks?overdue_only=true" className="shortcut-link"><CalendarClock aria-hidden="true" size={16} /> Tareas vencidas <ArrowRight aria-hidden="true" size={14} /></Link>
              <Link to="/tasks?due_soon_only=true" className="shortcut-link"><AlertCircle aria-hidden="true" size={16} /> Próximas a vencer <ArrowRight aria-hidden="true" size={14} /></Link>
              <Link to="/tasks?critical_only=true" className="shortcut-link"><ShieldAlert aria-hidden="true" size={16} /> Tareas críticas <ArrowRight aria-hidden="true" size={14} /></Link>
              <Link to="/tasks?state=desconocido" className="shortcut-link"><CircleSlash2 aria-hidden="true" size={16} /> Estado desconocido <ArrowRight aria-hidden="true" size={14} /></Link>
            </div>
          </section>

          <section className="panel history-section" aria-labelledby="history-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">DATOS HISTÓRICOS</p>
                <h2 id="history-heading">Evolución de capturas</h2>
                <p className="section-subtitle">Cada punto corresponde a un resumen persistido por el backend.</p>
              </div>
              <span className="history-source"><Layers3 aria-hidden="true" size={15} /> Capturas reales</span>
            </div>
            {historyQuery.isPending && <LoadingState label="Consultando capturas históricas…" />}
            {historyQuery.isError && <ErrorState title="No se pudo cargar el historial" message={historyQuery.error.message} onRetry={() => void historyQuery.refetch()} />}
            {historyQuery.data && <CaptureCharts captures={captures} />}
            {historyQuery.data?.items.length ? (
              <div className="recent-captures">
                <h3>Capturas disponibles</h3>
                <ul>
                  {captures.slice(-4).reverse().map((capture) => (
                    <li key={capture.capture_id}>
                      <span className="capture-marker" aria-hidden="true" />
                      <span className="capture-date">{formatDate(capture.captured_at, true)}</span>
                      <span>{numberFormatter.format(capture.summary.total_tasks)} tareas</span>
                      <span>{formatPercentage(capture.summary.progress_percentage)} de avance</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          <ExecutiveInsights projectId={project.id} />

          {captureRisks.length > 0 && (
            <section className="panel risks-panel" aria-labelledby="risks-heading">
              <div className="section-heading compact-heading">
                <div><p className="eyebrow">RESUMEN DE PLANIFICACIÓN</p><h2 id="risks-heading">Riesgos de la captura</h2></div>
                <Pill tone="warning">{captureRisks.length} informados</Pill>
              </div>
              <p className="section-subtitle">
                Clasificación determinista calculada sobre la captura. Las observaciones narrativas del análisis no se presentan como riesgos confirmados.
              </p>
              <ul className="risk-list">
                {captureRisks.map((risk) => (
                  <li key={risk.risk_id} className="risk-item">
                    <div className="risk-item-head">
                      <Pill tone={riskKindTone(risk.kind)}>{riskKindLabel(risk.kind)}</Pill>
                      <span className="risk-item-basis">{risk.basis}</span>
                    </div>
                    <p className="risk-item-tasks">Tareas afectadas ({risk.task_count}):</p>
                    <ul className="risk-task-links">
                      {risk.task_ids.map((taskId) => (
                        <li key={taskId}>
                          <Link to={`/tasks?task=${encodeURIComponent(taskId)}`}>{taskId}</Link>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
      {!summaryQuery.isPending && !summaryQuery.isError && !summaryQuery.data && (
        <EmptyState title="Sin resumen disponible" message="La API no reportó métricas para la captura seleccionada." />
      )}
    </section>
  );
}
