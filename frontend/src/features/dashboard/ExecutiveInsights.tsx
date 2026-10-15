import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis,
} from "recharts";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { formatDate, formatDurationSeconds, formatPercentage, runStatusLabel, stageStatusLabel } from "../../api/formatters";
import { useExecutiveSummary, useRuns } from "../../api/queries";
import { ErrorState, EmptyState, LoadingState, Pill } from "../../components/States";
import { PolicyResults } from "../quality/PolicyResults";
import type { ExecutiveSummary, RunProgressPoint, RunSummary } from "../../api/types";

const numberFormatter = new Intl.NumberFormat("es-EC");

function latestStatusTone(status: RunSummary["status"]): "neutral" | "positive" | "warning" | "danger" | "unknown" | "accent" {
  if (status === "completed") return "positive";
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "needs_attention" || status === "awaiting_human") return "warning";
  if (status === "unknown") return "unknown";
  return status === "queued" ? "neutral" : "accent";
}

function RunHistoryChart({ runs }: { runs: ExecutiveSummary["runs"] }) {
  if (runs.series.length === 0) {
    return <EmptyState title="Sin historial de ejecuciones" message="El backend no reportó puntos de runs persistidos." />;
  }
  if (runs.series.length < 2) {
    return <EmptyState title="Una sola ejecución disponible" message="No se dibuja una serie de duración con una sola observación." />;
  }

  const points = runs.series
    .filter((run): run is RunProgressPoint & { duration_seconds: number } => typeof run.duration_seconds === "number" && Number.isFinite(run.duration_seconds))
    .map((run) => ({
      timestamp: new Date(run.created_at).getTime(),
      created_at: run.created_at,
      run_id: run.run_id,
      status: run.status,
      duration_seconds: run.duration_seconds,
    }))
    .sort((left, right) => left.timestamp - right.timestamp);

  if (points.length < 2) {
    return <EmptyState title="Duraciones históricas insuficientes" message="Hay ejecuciones registradas, pero no suficientes duraciones informadas para dibujar una serie." />;
  }

  return (
    <div className="chart-frame" role="img" aria-label="Historial de análisis por estado">
      <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={170} initialDimension={{ width: 600, height: 185 }}>
        <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -14 }}>
          <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" vertical={false} />
          <XAxis type="number" dataKey="timestamp" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={(value: number) => new Intl.DateTimeFormat("es-EC", { timeZone: "America/Guayaquil", month: "short", day: "numeric" }).format(new Date(value))} tickLine={false} axisLine={false} tick={{ fill: "var(--text-muted)", fontSize: 10 }} />
          <YAxis tickLine={false} axisLine={false} tick={{ fill: "var(--text-muted)", fontSize: 10 }} tickFormatter={(value: number) => `${numberFormatter.format(value)} s`} />
          <ChartTooltip
            cursor={{ stroke: "var(--chart-grid)" }}
            contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border-strong)", borderRadius: 10, color: "var(--text-primary)" }}
            labelFormatter={(_, payload) => payload?.[0]?.payload?.created_at ? formatDate(payload[0].payload.created_at, true) : "Ejecución"}
            formatter={(value, _name, payload) => [
              `${formatDurationSeconds(typeof value === "number" ? value : null)} · ${runStatusLabel(String(payload?.payload?.status ?? "unknown"))}`,
              String(payload?.payload?.run_id ?? "Ejecución"),
            ]}
          />
          <Area type="linear" dataKey="duration_seconds" stroke="var(--chart-primary)" strokeWidth={2.5} fill="var(--chart-primary)" fillOpacity={0.12} connectNulls={false} dot={{ r: 3, fill: "var(--chart-primary)", strokeWidth: 0 }} activeDot={{ r: 5 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function LatestAnalysis({ projectId }: { projectId: string }) {
  const runsQuery = useRuns(projectId, 1, 1);
  const latest = runsQuery.data?.items[0];

  if (runsQuery.isPending) return <LoadingState label="Consultando la última ejecución…" />;
  if (runsQuery.isError) return <ErrorState title="No se pudo cargar la última ejecución" message={runsQuery.error.message} onRetry={() => void runsQuery.refetch()} />;
  if (!latest) return <p className="executive-unknown">Sin ejecución persistida en el backend.</p>;

  return (
    <div className="latest-analysis-card">
      <div><p className="eyebrow">ÚLTIMO ANÁLISIS REGISTRADO</p><strong>{formatDate(latest.created_at, true)}</strong><p>{latest.run_id}</p></div>
      <Pill tone={latestStatusTone(latest.status)}>{runStatusLabel(latest.status)}</Pill>
      <Link className="text-link" to={`/analysis?run_id=${encodeURIComponent(latest.run_id)}`}>Abrir ejecución <ArrowRight aria-hidden="true" size={14} /></Link>
    </div>
  );
}

export function ExecutiveInsights({ projectId }: { projectId: string }) {
  const executiveQuery = useExecutiveSummary(projectId);
  const data = executiveQuery.data;

  return (
    <section className="panel executive-insights" aria-labelledby="executive-insights-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">CALIDAD, POLÍTICAS E INFORMES</p>
          <h2 id="executive-insights-heading">Informe y políticas de calidad</h2>
          <p className="section-subtitle">Estados y recomendaciones procedentes de registros persistidos del backend.</p>
        </div>
        <ShieldCheck aria-hidden="true" className="executive-heading-icon" size={18} />
      </div>

      <div className="latest-analysis-wrap"><LatestAnalysis projectId={projectId} /></div>
      {executiveQuery.isPending && <LoadingState label="Consultando el resumen ejecutivo…" />}
      {executiveQuery.isError && <ErrorState title="No se pudo cargar el resumen ejecutivo" message={executiveQuery.error.message} onRetry={() => void executiveQuery.refetch()} />}
      {data && <ExecutiveData data={data} />}
    </section>
  );
}

function ExecutiveData({ data }: { data: ExecutiveSummary }) {
  const quality = data.quality;
  const report = data.latest_report;

  return (
    <>
      <div className="executive-summary-grid">
        <article className="executive-summary-card">
          <div className="executive-card-heading"><h3>Estado de calidad</h3><Pill tone={quality?.availability === "available" ? "accent" : "unknown"}>{quality?.availability === "available" ? "Disponible" : "Desconocido"}</Pill></div>
          {quality ? (
            <>
              <p>Etapa Quality: {quality.stage_status ? stageStatusLabel(quality.stage_status) : "Desconocida"}</p>
              <p>Registro de ejecución: <code>{quality.run_id ?? "Sin dato"}</code></p>
              {quality.finding_counts && <p>{numberFormatter.format(quality.finding_counts.candidate)} hallazgos candidatos · {numberFormatter.format(quality.finding_counts.confirmed)} confirmados por verificación</p>}
              {!quality.finding_counts && <p>Conteos de hallazgos no disponibles.</p>}
              {!quality.ci && <p>CI y cobertura: desconocidos en el registro actual.</p>}
              {quality.ci && <p>CI: {quality.ci.status} · Cobertura: {formatPercentage(quality.ci.coverage_percentage)}</p>}
            </>
          ) : <p className="executive-unknown">Sin resumen de calidad informado.</p>}
          <Link className="text-link" to="/quality">Abrir calidad <ArrowRight aria-hidden="true" size={13} /></Link>
        </article>

        <article className="executive-summary-card executive-policies-card">
          <div className="executive-card-heading"><h3>Políticas evaluadas</h3><Pill tone={data.policies.availability === "available" ? "accent" : "unknown"}>{data.policies.availability === "available" ? "Resultados reales" : "Desconocido"}</Pill></div>
          <p>Run asociado: <code>{data.policies.run_id ?? "Sin dato"}</code></p>
          <PolicyResults availability={data.policies.availability} items={data.policies.items} />
          <Link className="text-link" to="/quality">Consultar evaluaciones <ArrowRight aria-hidden="true" size={13} /></Link>
        </article>

        <article className="executive-summary-card executive-report-card">
          <div className="executive-card-heading"><h3>Recomendación del último informe</h3><Pill tone={report ? "accent" : "unknown"}>{report ? "Informe persistido" : "Sin informe"}</Pill></div>
          {report ? (
            <>
              <p className="executive-recommendation">{report.recommendation}</p>
              <p>Fecha de corte: {formatDate(report.cutoff_datetime, true)}</p>
              <p>Decisión humana reportada: <code>{report.human_decision}</code></p>
              <p>{report.summary}</p>
              {report.run_id && <Link className="text-link" to={`/analysis?run_id=${encodeURIComponent(report.run_id)}`}>Abrir ejecución de origen <ArrowRight aria-hidden="true" size={13} /></Link>}
            </>
          ) : <p className="executive-unknown">El backend no informó un informe persistido para este proyecto.</p>}
        </article>
      </div>

      <div className="chart-card executive-run-chart-card">
        <div className="chart-heading">
          <div><h3>Historial de análisis</h3><p>Duración de ejecuciones con marcas de tiempo persistidas. Valores nulos no se convierten en cero.</p></div>
          <Pill tone="neutral">{numberFormatter.format(data.runs.total)} ejecuciones</Pill>
        </div>
        <RunHistoryChart runs={data.runs} />
        {data.runs.series.length > 0 && (
          <ul className="executive-run-links">
            {data.runs.series.slice(-5).reverse().map((run) => (
              <li key={run.run_id}><Link to={`/analysis?run_id=${encodeURIComponent(run.run_id)}`}>{run.run_id} · {runStatusLabel(run.status)} · {formatDurationSeconds(run.duration_seconds)} <ArrowRight aria-hidden="true" size={12} /></Link></li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
