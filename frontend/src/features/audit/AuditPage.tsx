import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Activity, ChevronLeft, ChevronRight, LockKeyhole } from "lucide-react";
import { useWorkspace } from "../../app/WorkspaceContext";
import { useProject, useProjects, useAudit, useAuditEvent } from "../../api/queries";
import { formatDate } from "../../api/formatters";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import { useSession } from "../auth/SessionProvider";

const pageSize = 25;

function actorLabel(verification: "verified_local_session" | "unknown", actorId: string | null) {
  return verification === "verified_local_session"
    ? `Sesión local verificada: ${actorId ?? "actor desconocido"}`
    : "Actor no verificado";
}

export function AuditPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectedProjectId } = useWorkspace();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const selectedProjectQuery = useProject(selectedProjectId, Boolean(selectedProjectId && !listedProject));
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const page = Math.max(1, Number(searchParams.get("page") ?? 1) || 1);
  const eventType = searchParams.get("event_type") ?? "";
  const since = searchParams.get("since") ?? "";
  const until = searchParams.get("until") ?? "";
  const eventId = searchParams.get("event_id");
  const [draftEventType, setDraftEventType] = useState(eventType);
  const [draftSince, setDraftSince] = useState(since);
  const [draftUntil, setDraftUntil] = useState(until);
  const { session } = useSession();
  const canReadAudit = Boolean(session?.authenticated && session.capabilities.includes("audit:read"));
  const filters = { event_type: eventType || undefined, since: since || undefined, until: until || undefined };
  const eventsQuery = useAudit(project?.id ?? null, page, pageSize, filters, canReadAudit);
  const detailQuery = useAuditEvent(project?.id ?? null, eventId, canReadAudit);

  useEffect(() => {
    setDraftEventType(eventType);
    setDraftSince(since);
    setDraftUntil(until);
  }, [eventType, since, until]);

  function applyFilters() {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of [["event_type", draftEventType], ["since", draftSince], ["until", draftUntil]] as const) {
      if (value.trim()) next.set(key, value.trim());
      else next.delete(key);
    }
    next.delete("page");
    setSearchParams(next);
  }

  function clearFilters() {
    const next = new URLSearchParams();
    if (eventId) next.set("event_id", eventId);
    setSearchParams(next);
    setDraftEventType("");
    setDraftSince("");
    setDraftUntil("");
  }

  function detailHref(id: string) {
    const next = new URLSearchParams(searchParams);
    next.set("event_id", id);
    return { pathname: "/audit", search: `?${next.toString()}` };
  }

  function changePage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    if (nextPage > 1) next.set("page", String(nextPage));
    else next.delete("page");
    setSearchParams(next);
  }

  const pages = Math.max(1, Math.ceil((eventsQuery.data?.total ?? 0) / pageSize));

  return (
    <section className="governance-page audit-page">
      <header className="page-heading-row"><div><p className="eyebrow">TRAZABILIDAD DEL BACKEND</p><h1>Auditoría y actividad</h1><p className="page-subtitle">Consulta eventos persistidos y sus detalles allowlisted. Los identificadores de actor no se presentan como identidad externa verificada.</p></div><Pill tone="neutral">Lectura protegida</Pill></header>
      {!canReadAudit && <div className="inline-warning"><LockKeyhole aria-hidden="true" size={16} /> Se requiere una sesión local con la capacidad <code>audit:read</code>.</div>}
      <section className="audit-filter-panel panel" aria-label="Filtros de auditoría">
        <label className="filter-control"><span>Tipo de evento</span><input value={draftEventType} onChange={(event) => setDraftEventType(event.target.value)} placeholder="Todos los tipos" /></label>
        <label className="filter-control"><span>Desde (ISO 8601 con zona)</span><input type="text" value={draftSince} onChange={(event) => setDraftSince(event.target.value)} placeholder="2026-01-12T00:00:00-05:00" /></label>
        <label className="filter-control"><span>Hasta (ISO 8601 con zona)</span><input type="text" value={draftUntil} onChange={(event) => setDraftUntil(event.target.value)} placeholder="2026-01-12T23:59:59-05:00" /></label>
        <button type="button" className="button button-secondary" onClick={applyFilters}>Aplicar filtros de auditoría</button>
        <button type="button" className="button button-ghost" onClick={clearFilters}>Limpiar filtros</button>
      </section>
      {canReadAudit && eventsQuery.isPending && <LoadingState label="Consultando eventos de auditoría…" />}
      {canReadAudit && eventsQuery.isError && <ErrorState title="No se pudo cargar la auditoría" message={eventsQuery.error.message} onRetry={() => void eventsQuery.refetch()} />}
      {canReadAudit && eventsQuery.data?.availability === "unknown" && <EmptyState title="Disponibilidad de auditoría desconocida" message="La API no confirma la disponibilidad de la bitácora; no se muestra como vacía." />}
      {canReadAudit && eventsQuery.data?.availability === "available" && eventsQuery.data.items.length === 0 && <EmptyState title="No hay eventos para estos filtros" message="No se infiere actor, ejecución ni eventos adicionales a partir de una búsqueda vacía." />}

      {canReadAudit && eventsQuery.data?.availability === "available" && eventsQuery.data.items.length > 0 && <div className="audit-workspace">
        <section className="panel audit-event-list" aria-labelledby="audit-event-list-title"><div className="section-heading"><div><p className="eyebrow">EVENTOS APPEND-ONLY</p><h2 id="audit-event-list-title">Actividad persistida</h2></div><span className="count-label">{eventsQuery.data.total} eventos</span></div>
          <div className="audit-list">{eventsQuery.data.items.map((event) => <Link to={detailHref(event.event_id)} key={event.event_id} className={`audit-list-item${event.event_id === eventId ? " audit-list-item-active" : ""}`} aria-label={`Ver evento ${event.event_id}`}>
            <span className="audit-list-top"><strong>{event.event_type}</strong><Pill tone={event.outcome === "recorded" || event.outcome === "success" ? "positive" : event.outcome === "unknown" ? "unknown" : "neutral"}>{event.outcome}</Pill></span><span>{event.summary}</span><span>{formatDate(event.occurred_at, true)}</span><span>{actorLabel(event.actor_verification, event.actor_id)}</span><code>{event.event_id}</code>
          </Link>)}</div>
          <div className="table-footer"><span>Página {page} de {pages}</span><div className="pagination-controls"><button type="button" className="icon-button" aria-label="Página anterior de auditoría" disabled={page <= 1} onClick={() => changePage(page - 1)}><ChevronLeft aria-hidden="true" size={16} /></button><button type="button" className="icon-button" aria-label="Página siguiente de auditoría" disabled={page >= pages} onClick={() => changePage(page + 1)}><ChevronRight aria-hidden="true" size={16} /></button></div></div>
        </section>
        <section className="panel audit-detail" aria-labelledby="audit-detail-title">
          {!eventId && <EmptyState title="Selecciona un evento" message="El detalle allowlisted se obtiene únicamente al elegir un evento." />}
          {eventId && detailQuery.isPending && <LoadingState label="Consultando detalle de auditoría…" />}
          {eventId && detailQuery.isError && <ErrorState title="No se pudo cargar el evento" message={detailQuery.error.message} onRetry={() => void detailQuery.refetch()} />}
          {detailQuery.data && <>
            <div className="audit-detail-heading"><div><p className="eyebrow">DETALLE DE EVENTO</p><h2 id="audit-detail-title">Evento {detailQuery.data.event_id}</h2><code>{detailQuery.data.event_type}</code></div><Activity aria-hidden="true" size={19} /></div>
            <p className="audit-detail-summary">{detailQuery.data.summary}</p>
            <dl className="audit-detail-grid"><div><dt>Resultado</dt><dd>{detailQuery.data.outcome}</dd></div><div><dt>Momento</dt><dd>{formatDate(detailQuery.data.occurred_at, true)}</dd></div><div><dt>Actor</dt><dd>{actorLabel(detailQuery.data.actor_verification, detailQuery.data.actor_id)}{detailQuery.data.actor_id ? <small>La sesión local no verifica identidad externa.</small> : null}</dd></div><div><dt>Origen</dt><dd>{detailQuery.data.source}</dd></div><div><dt>Recurso</dt><dd>{detailQuery.data.resource_type ?? "Desconocido"} · {detailQuery.data.resource_id ?? "ID desconocido"}</dd></div></dl>
            <section className="detail-section"><h3>Detalles allowlisted</h3><p className="caption">Datos mostrados como texto estructurado; no se interpretan como HTML.</p><pre className="audit-details-json">{JSON.stringify(detailQuery.data.details, null, 2)}</pre></section>
          </>}
        </section>
      </div>}
    </section>
  );
}
