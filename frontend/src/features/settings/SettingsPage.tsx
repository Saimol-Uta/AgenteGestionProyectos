import { EmailDeliveryPanel } from "../notifications/EmailDeliveryPanel";
import { ArrowUpRight, Clock3, Database, Moon, ShieldCheck, Sun } from "lucide-react";
import { Link } from "react-router-dom";
import {
  formatDate,
  providerModelLabel,
  providerNameLabel,
  runStatusLabel,
  stageNameLabel,
} from "../../api/formatters";
import { useProject, useProjects, useProjectConfiguration, useSystemHealth } from "../../api/queries";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import { useWorkspace } from "../../app/WorkspaceContext";
import { useTheme } from "../theme/ThemeProvider";
import { useSession } from "../auth/SessionProvider";

export function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const { selectedProjectId } = useWorkspace();
  const { session } = useSession();
  const projectsQuery = useProjects();
  const healthQuery = useSystemHealth();
  const projects = projectsQuery.data?.items ?? [];
  const listedSelection = projects.find((project) => project.id === selectedProjectId);
  const selectedProjectQuery = useProject(selectedProjectId, Boolean(selectedProjectId && !listedSelection));
  const selectedProject = listedSelection ?? selectedProjectQuery.data ?? (!selectedProjectId ? projects[0] ?? null : null);
  const canReadConfiguration = Boolean(session?.authenticated && session.capabilities.includes("configuration:read"));
  const configurationQuery = useProjectConfiguration(selectedProject?.id ?? null, canReadConfiguration);

  return (
    <section className="settings-page">
      <header className="page-heading-row settings-heading">
        <div>
          <p className="eyebrow">PREFERENCIAS Y ENTORNO</p>
          <h1>Configuración</h1>
          <p className="page-subtitle">
            Preferencias locales y metadatos publicados por el backend. Esta vista no modifica integraciones ni concede autoridad externa.
          </p>
        </div>
        <Pill tone="neutral">Solo lectura del servidor</Pill>
      </header>

      {selectedProject && <EmailDeliveryPanel projectId={selectedProject.id} />}

      <div className="settings-layout">
        <section className="settings-panel settings-preferences" aria-labelledby="settings-preferences-title">
          <div className="settings-section-heading">
            <span className="settings-icon"><Sun aria-hidden="true" size={17} /></span>
            <div>
              <p className="eyebrow">EN ESTE NAVEGADOR</p>
              <h2 id="settings-preferences-title">Preferencias de interfaz</h2>
            </div>
          </div>
          <fieldset className="settings-theme-fieldset" aria-describedby="settings-theme-help">
            <legend>Tema visual</legend>
            <div className="settings-theme-options">
              <label className={`settings-theme-option${theme === "light" ? " settings-theme-option-active" : ""}`}>
                <input
                  type="radio"
                  name="interface-theme"
                  value="light"
                  checked={theme === "light"}
                  onChange={() => setTheme("light")}
                />
                <Sun aria-hidden="true" size={17} />
                <span><strong>Claro</strong><small>Superficies luminosas</small></span>
              </label>
              <label className={`settings-theme-option${theme === "dark" ? " settings-theme-option-active" : ""}`}>
                <input
                  type="radio"
                  name="interface-theme"
                  value="dark"
                  checked={theme === "dark"}
                  onChange={() => setTheme("dark")}
                />
                <Moon aria-hidden="true" size={17} />
                <span><strong>Oscuro</strong><small>Contraste de baja luz</small></span>
              </label>
            </div>
            <p id="settings-theme-help" className="settings-note">Se guarda solo la preferencia visual local; no se envía a la API.</p>
          </fieldset>
        </section>

        <section className="settings-panel settings-runtime" aria-labelledby="settings-runtime-title">
          <div className="settings-section-heading">
            <span className="settings-icon"><ShieldCheck aria-hidden="true" size={17} /></span>
            <div>
              <p className="eyebrow">ESTADO PUBLICADO POR EL SERVIDOR</p>
              <h2 id="settings-runtime-title">Entorno de ejecución</h2>
            </div>
          </div>
          {healthQuery.isPending && <LoadingState label="Consultando el estado del servicio…" />}
          {healthQuery.isError && <ErrorState title="No se pudo consultar el servicio" message={healthQuery.error.message} onRetry={() => void healthQuery.refetch()} />}
          {healthQuery.data && (
            <>
              <div className="settings-runtime-status">
                <div className="settings-runtime-label">
                  <span className={`settings-runtime-dot${healthQuery.data.status === "ok" ? " settings-runtime-dot-ok" : ""}`} aria-hidden="true" />
                  <div><span className="caption">Estado</span><strong>{healthQuery.data.status}</strong></div>
                </div>
                <Pill tone={healthQuery.data.external_writes_enabled ? "danger" : "positive"}>
                  {healthQuery.data.external_writes_enabled ? "Escrituras externas habilitadas" : "Escrituras externas deshabilitadas"}
                </Pill>
              </div>
              <dl className="settings-runtime-facts">
                <div><dt>Versión API</dt><dd><code>{healthQuery.data.version}</code></dd></div>
                <div><dt>Modo de acceso</dt><dd>Consulta local; sin autoridad sobre servicios externos</dd></div>
              </dl>
              <p className="settings-note">
                Una sesión verificada pertenece al servidor local y no demuestra identidad ni permisos de Trello, GitHub o proveedores de IA.
              </p>
            </>
          )}
        </section>

        <section className="settings-panel settings-project" aria-labelledby="settings-project-title">
          <div className="settings-section-heading">
            <span className="settings-icon"><Database aria-hidden="true" size={17} /></span>
            <div>
              <p className="eyebrow">METADATOS DEL BACKEND</p>
              <h2 id="settings-project-title">Proyecto seleccionado</h2>
            </div>
            {selectedProject && <Link className="settings-section-link" to="/projects">Cartera <ArrowUpRight aria-hidden="true" size={14} /></Link>}
          </div>
          {projectsQuery.isPending && <LoadingState label="Consultando proyectos configurados…" />}
          {projectsQuery.isError && <ErrorState title="No se pudieron consultar los proyectos" message={projectsQuery.error.message} onRetry={() => void projectsQuery.refetch()} />}
          {selectedProjectId && selectedProjectQuery.isPending && !selectedProject && <LoadingState label="Consultando el proyecto seleccionado…" />}
          {selectedProjectId && selectedProjectQuery.isError && <ErrorState title="No se pudo cargar el proyecto seleccionado" message={selectedProjectQuery.error.message} onRetry={() => void selectedProjectQuery.refetch()} />}
          {projectsQuery.data && !projectsQuery.data.items.length && !selectedProjectId && (
            <EmptyState title="Sin proyectos publicados" message="La API no informó proyectos configurados o almacenados; no se generan datos locales." />
          )}
          {selectedProject && (
            <>
              <div className="settings-project-identity">
                <div><span className="caption">Nombre del proyecto</span><h3>{selectedProject.name}</h3></div>
                <code>{selectedProject.id}</code>
              </div>
              <dl className="settings-project-facts">
                <div><dt>Modo de origen</dt><dd><code>{selectedProject.source_mode}</code></dd></div>
                <div><dt>Última captura</dt><dd><Clock3 aria-hidden="true" size={13} />{formatDate(selectedProject.latest_capture_at, true)}</dd></div>
                <div><dt>Última ejecución</dt><dd>{selectedProject.latest_run_status ? runStatusLabel(selectedProject.latest_run_status) : "Sin dato registrado"}</dd></div>
                <div><dt>ID de ejecución</dt><dd>{selectedProject.latest_run_id ? <Link to={`/analysis?run_id=${encodeURIComponent(selectedProject.latest_run_id)}`}>{selectedProject.latest_run_id}</Link> : "Sin dato registrado"}</dd></div>
              </dl>
            </>
          )}
          <p className="settings-note">
            Repositorios, tableros, políticas, credenciales y programación solo aparecerán cuando el backend publique sus metadatos seguros. Esta vista no permite crear proyectos ni editar integraciones.
          </p>
        </section>

        <section className="settings-panel settings-project-configuration" aria-labelledby="settings-protected-config-title">
          <div className="settings-section-heading">
            <span className="settings-icon"><ShieldCheck aria-hidden="true" size={17} /></span>
            <div><p className="eyebrow">METADATOS PROTEGIDOS · SOLO LECTURA</p><h2 id="settings-protected-config-title">Configuración general del proyecto</h2></div>
          </div>
          {!canReadConfiguration && <p className="settings-note">La configuración protegida requiere la capacidad configuration:read.</p>}
          {canReadConfiguration && configurationQuery.isPending && <LoadingState label="Consultando metadatos protegidos del proyecto…" />}
          {canReadConfiguration && configurationQuery.isError && <ErrorState title="No se pudo consultar la configuración protegida" message={configurationQuery.error.message} onRetry={() => void configurationQuery.refetch()} />}
          {configurationQuery.data?.availability === "unknown" && <EmptyState title="Disponibilidad de configuración desconocida" message="El backend no confirma la disponibilidad de estos metadatos seguros." />}
          {configurationQuery.data?.availability === "available" && <>
            <div className="settings-config-grid">
              <div className="settings-config-card"><h3>Fuente y calendario</h3><dl><div><dt>Modo de origen</dt><dd><code>{configurationQuery.data.source_mode}</code></dd></div><div><dt>Zona horaria</dt><dd>{configurationQuery.data.timezone ?? "Desconocida"}</dd></div><div><dt>Días para vencimiento próximo</dt><dd>{configurationQuery.data.due_soon_days ?? "Desconocido"}</dd></div><div><dt>Estado de programación</dt><dd>{configurationQuery.data.schedule_status}</dd></div><div><dt>Programación</dt><dd>{configurationQuery.data.schedule ? `${configurationQuery.data.schedule.local_time} · ${configurationQuery.data.schedule.timezone} · ${configurationQuery.data.schedule.window_rule}` : "No publicada"}</dd></div></dl></div>
              <div className="settings-config-card"><h3>Repositorio</h3><dl><div><dt>Configurado</dt><dd>{configurationQuery.data.repository.configured ? "Sí" : "No"}</dd></div><div><dt>Propietario / nombre</dt><dd>{configurationQuery.data.repository.owner && configurationQuery.data.repository.name ? `${configurationQuery.data.repository.owner}/${configurationQuery.data.repository.name}` : `${configurationQuery.data.repository.owner ?? "Desconocido"}/${configurationQuery.data.repository.name ?? "Desconocido"}`}</dd></div><div><dt>Rama</dt><dd>{configurationQuery.data.repository.branch ?? "Desconocida"}</dd></div><div><dt>Commit publicado</dt><dd><code>{configurationQuery.data.repository.commit_sha ?? "Desconocido"}</code></dd></div></dl></div>
              <div className="settings-config-card"><h3>Trello · metadatos</h3><dl><div><dt>Configurado</dt><dd>{configurationQuery.data.trello.configured ? "Sí" : "No"}</dd></div><div><dt>Tablero</dt><dd><code>{configurationQuery.data.trello.board_id ?? "No publicado"}</code></dd></div><div><dt>Presencia de credencial</dt><dd>{configurationQuery.data.trello.credential_status === "configured" ? "Credencial Trello: configurada" : configurationQuery.data.trello.credential_status === "missing" ? "Credencial Trello: no configurada" : "Credencial Trello: desconocida"}</dd></div></dl></div>
              <div className="settings-config-card"><h3>Políticas publicadas</h3><dl><div><dt>Configuradas</dt><dd>{configurationQuery.data.policies.configured === null ? "Desconocido" : configurationQuery.data.policies.configured ? "Sí" : "No"}</dd></div><div><dt>Versión</dt><dd>{configurationQuery.data.policies.version ?? "No publicada"}</dd></div></dl></div>
            </div>
            <section className="settings-providers"><h3>Proveedores configurados</h3><p className="settings-note">Estos metadatos no comprueban disponibilidad ni exponen valores de credenciales. La presencia configurada no inicia una conexión.</p>{configurationQuery.data.providers.length ? <ul>{configurationQuery.data.providers.map((provider, index) => <li key={`${provider.stage}-${provider.provider}-${index}`}><strong>{stageNameLabel(provider.stage)}</strong><span>{providerNameLabel(provider.provider)}</span>{providerModelLabel(provider.provider, provider.model) && <span>{providerModelLabel(provider.provider, provider.model)}</span>}<span>{provider.transport === "remote" ? "Servicios en la nube" : provider.transport === "local" ? "Procesamiento local" : "Ubicación sin confirmar"}</span><span>Acceso: {provider.credential_status === "configured" ? "disponible (valor oculto)" : provider.credential_status === "missing" ? "no disponible" : provider.credential_status === "not_required" ? "no se necesita" : "sin confirmar"}</span></li>)}</ul> : <p className="unknown-note">El backend no publicó proveedores configurados.</p>}</section>
            <p className="settings-note">Ningún campo de configuración es editable desde esta interfaz.</p>
            <section className="settings-providers"><h3>Capabilities devueltas por el servidor</h3>{configurationQuery.data.capabilities.length ? <ul className="settings-capabilities">{configurationQuery.data.capabilities.map((capability) => <li key={capability}><code>{capability}</code></li>)}</ul> : <p className="unknown-note">No hay capabilities publicadas para el proyecto.</p>}<p className="settings-note">Campos editables declarados por el servidor: {configurationQuery.data.editable_fields.length ? configurationQuery.data.editable_fields.join(", ") : "ninguno"}.</p></section>
          </>}
        </section>

        <aside className="settings-security-note" aria-label="Límite de seguridad">
          <ShieldCheck aria-hidden="true" size={16} />
          <p>Las credenciales nunca se solicitan ni se guardan en el navegador. Los valores no publicados por el servidor permanecen como desconocidos.</p>
        </aside>
      </div>
    </section>
  );
}
