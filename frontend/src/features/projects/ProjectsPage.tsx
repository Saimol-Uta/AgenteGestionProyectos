import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, FolderKanban, Layers3 } from "lucide-react";
import { useProjects } from "../../api/queries";
import { formatDate } from "../../api/formatters";
import { useWorkspace } from "../../app/WorkspaceContext";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";

export function ProjectsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const pageValue = Number(searchParams.get("page") ?? "1");
  const page = Number.isInteger(pageValue) && pageValue > 0 ? pageValue : 1;
  const projectsQuery = useProjects(page, 100);
  const { setSelectedProjectId } = useWorkspace();
  const navigate = useNavigate();

  if (projectsQuery.isPending) return <LoadingState label="Consultando proyectos configurados…" />;
  if (projectsQuery.isError) {
    return <ErrorState message={projectsQuery.error.message} onRetry={() => void projectsQuery.refetch()} />;
  }
  if (projectsQuery.data.items.length === 0) {
    return (
      <section className="content-section">
        <div className="page-heading-row">
          <div><p className="eyebrow">CARTERA</p><h1>Proyectos</h1></div>
        </div>
        <EmptyState title="No hay proyectos configurados" message="La API no reportó proyectos configurados o almacenados." />
      </section>
    );
  }

  return (
    <section className="content-section">
      <div className="page-heading-row">
        <div>
          <p className="eyebrow">CARTERA</p>
          <h1>Proyectos</h1>
          <p className="page-subtitle">Proyectos entregados por la API; no se agregan datos de demostración.</p>
        </div>
        <span className="count-label">{projectsQuery.data.total} registrados por el backend</span>
      </div>

      <div className="project-grid">
        {projectsQuery.data.items.map((project) => (
          <article className="project-card" key={project.id}>
            <div className="project-card-top">
              <span className="project-icon"><FolderKanban aria-hidden="true" size={19} /></span>
              <span className="pill pill-neutral">{project.source_mode}</span>
            </div>
            <h2>{project.name}</h2>
            <p className="project-identifier">Identificador: <code>{project.id}</code></p>
            <dl className="project-meta-grid">
              <div><dt>Última captura</dt><dd>{formatDate(project.latest_capture_at, true)}</dd></div>
              <div><dt>Última ejecución</dt><dd>{project.latest_run_status ?? "Sin dato"}</dd></div>
            </dl>
            {project.summary ? (
              <div className="project-summary-line">
                <Layers3 aria-hidden="true" size={15} />
                <span>{project.summary.total_tasks} tareas en el resumen disponible</span>
              </div>
            ) : (
              <div className="project-summary-line project-summary-muted">El backend no incluye un resumen reciente.</div>
            )}
            <button
              className="button button-secondary project-open"
              type="button"
              onClick={() => {
                setSelectedProjectId(project.id);
                navigate("/");
              }}
            >
              Abrir dashboard <ArrowRight aria-hidden="true" size={15} />
            </button>
          </article>
        ))}
      </div>
      {projectsQuery.data.total > projectsQuery.data.page_size && (
        <div className="table-footer project-pagination">
          <span>Mostrando página {projectsQuery.data.page} de {Math.ceil(projectsQuery.data.total / projectsQuery.data.page_size)} · {projectsQuery.data.total} proyectos</span>
          <div className="pagination-controls">
            <button
              className="button button-secondary button-small"
              type="button"
              disabled={projectsQuery.data.page <= 1}
              aria-label="Página anterior de proyectos"
              onClick={() => {
                const next = new URLSearchParams(searchParams);
                if (projectsQuery.data.page <= 2) next.delete("page");
                else next.set("page", String(projectsQuery.data.page - 1));
                setSearchParams(next);
              }}
            >Anterior</button>
            <button
              className="button button-secondary button-small"
              type="button"
              disabled={projectsQuery.data.page >= Math.ceil(projectsQuery.data.total / projectsQuery.data.page_size)}
              aria-label="Página siguiente de proyectos"
              onClick={() => {
                const next = new URLSearchParams(searchParams);
                next.set("page", String(projectsQuery.data.page + 1));
                setSearchParams(next);
              }}
            >Siguiente</button>
          </div>
        </div>
      )}
    </section>
  );
}
