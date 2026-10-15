import { useEffect, useRef, useState, type FormEvent } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Activity, Archive, BarChart3, CalendarDays, CheckSquare2, ChevronDown, CircleHelp,
  Clock3, FolderKanban, Gauge, Menu, Moon, Search, Settings2, ShieldCheck,
  Sparkles, Sun, X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useProject, useProjects, useSystemHealth } from "../api/queries";
import { useWorkspace } from "./WorkspaceContext";
import { useSession } from "../features/auth/SessionProvider";
import { LoginDialog } from "../features/auth/LoginDialog";
import { useTheme } from "../features/theme/ThemeProvider";
import { useToast } from "../components/ToastProvider";
import { AgentWorkflowBar } from "../components/AgentWorkflowBar";
import { useDecisionSummary } from "../features/manager/useDecisionSummary";
import { Tooltip } from "../components/Tooltip";

const navigation: Array<{
  heading: string;
  items: Array<{ label: string; path: string; icon: LucideIcon }>;
}> = [
  { heading: "1. Seguimiento y Control", items: [
    { label: "Dashboard", path: "/", icon: Gauge },
    { label: "Proyectos", path: "/projects", icon: FolderKanban },
    { label: "Tareas", path: "/tasks", icon: CheckSquare2 },
    { label: "Análisis de IA", path: "/analysis", icon: Sparkles },
  ] },
  { heading: "2. Bandeja de Decisiones", items: [
    { label: "Propuestas", path: "/proposals", icon: Archive },
    { label: "Calidad de código", path: "/quality", icon: ShieldCheck },
  ] },
  { heading: "3. Reportes y Auditoría", items: [
    { label: "Informes", path: "/reports", icon: BarChart3 },
    { label: "Calendario", path: "/calendar", icon: CalendarDays },
    { label: "Auditoría", path: "/audit", icon: Activity },
    { label: "Configuración", path: "/settings", icon: Settings2 },
  ] },
];

const routeTitles: Record<string, string> = {
  "/": "Dashboard",
  "/dashboard": "Dashboard",
  "/projects": "Proyectos",
  "/tasks": "Tareas",
  "/analysis": "Análisis de IA",
  "/quality": "Calidad de código",
  "/proposals": "Propuestas",
  "/reports": "Informes",
  "/calendar": "Calendario",
  "/audit": "Auditoría",
  "/settings": "Configuración",
};

function SidebarNavigation({ onNavigate }: { onNavigate?: () => void }) {
  const { selectedProjectId } = useWorkspace();
  const counts = useDecisionSummary(selectedProjectId);
  return (
    <nav className="sidebar-navigation" aria-label="Navegación principal">
      {navigation.map((group) => (
        <div className="nav-group" key={group.heading}>
          <p className="nav-heading">{group.heading}</p>
          {group.items.map(({ label, path, icon: Icon }) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/"}
              onClick={onNavigate}
              className={({ isActive }) => `nav-link${isActive ? " nav-link-active" : ""}`}
              aria-label={label}
            >
              <Icon aria-hidden="true" size={17} strokeWidth={1.8} />
              <span>{label}</span>
              {(path === "/proposals" || path === "/quality") && <span className="nav-decision-badge" aria-label="Pendientes según el backend">{(path === "/proposals" ? counts.proposals : counts.findings) ?? "—"}</span>}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <Link className="brand" to="/" aria-label="Gestión de Proyectos, ir al dashboard">
      <span className="brand-mark" aria-hidden="true"><span>G</span></span>
      <span className="brand-copy">
        <strong>GestiónPro</strong>
        <small>PLATAFORMA DE PROYECTOS</small>
      </span>
    </Link>
  );
}

function ProjectSelect({ compact = false }: { compact?: boolean }) {
  const projectsQuery = useProjects();
  const { selectedProjectId, setSelectedProjectId } = useWorkspace();
  const projects = projectsQuery.data?.items ?? [];
  const selectedFromList = projects.find((project) => project.id === selectedProjectId);
  const selectedProjectQuery = useProject(selectedProjectId, Boolean(selectedProjectId && !selectedFromList));
  const selected = selectedFromList ?? selectedProjectQuery.data ?? (!selectedProjectId ? projects[0] : undefined);
  const projectOptions = selected && !projects.some((project) => project.id === selected.id)
    ? [selected, ...projects]
    : projects;

  if (projectsQuery.isError) {
    return <span className={`project-select-error${compact ? " compact" : ""}`}>Proyectos no disponibles</span>;
  }
  if (selectedProjectQuery.isError && selectedProjectId && !selectedFromList) {
    return <span className={`project-select-error${compact ? " compact" : ""}`}>Proyecto activo no disponible</span>;
  }

  return (
    <label className={`project-select-wrap${compact ? " compact" : ""}`}>
      {!compact && <span className="sr-only">Proyecto activo</span>}
      <FolderKanban aria-hidden="true" size={16} />
      <select
        aria-label="Proyecto activo"
        value={selected?.id ?? ""}
        disabled={projectsQuery.isLoading || (Boolean(selectedProjectId && !selectedFromList) && selectedProjectQuery.isPending) || projectOptions.length === 0}
        onChange={(event) => setSelectedProjectId(event.target.value || null)}
      >
        {projectsQuery.isLoading && <option value="">Cargando proyectos…</option>}
        {!projectsQuery.isLoading && projectOptions.length === 0 && <option value="">Sin proyectos configurados</option>}
        {projectOptions.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
      </select>
      <ChevronDown aria-hidden="true" className="project-chevron" size={14} />
    </label>
  );
}

function SystemStatus() {
  // Health is queried independently so a projects outage is not mistaken for system health.
  const health = useSystemHealth();
  const status = health.isPending
    ? "Consultando estado"
    : health.isError
      ? "Estado no disponible"
      : health.data.status === "ok"
        ? "Sistema operativo"
        : `Sistema: ${health.data.status}`;
  const isOperational = health.data?.status === "ok";
  const writesMessage = health.data?.external_writes_enabled === false
    ? "El backend informa que las escrituras externas están deshabilitadas."
    : health.data?.external_writes_enabled === true
      ? "El backend informa que las escrituras externas están habilitadas; esta interfaz no expone acciones externas."
      : "No se pudo verificar la configuración de escrituras externas.";

  return (
    <Tooltip content={`${status}. ${writesMessage}`}>
      <span className={`system-status${isOperational ? "" : " system-status-unknown"}`} role="status" tabIndex={0}>
        <span className="status-dot" aria-hidden="true" />
        <span className="system-status-label">{status}</span>
      </span>
    </Tooltip>
  );
}


function SessionControl() {
  const { session, isLoading, isUnavailable, logout, isLoggingOut } = useSession();
  const { notify } = useToast();

  async function handleLogout() {
    try {
      await logout();
      notify("La sesión se cerró correctamente.", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "No fue posible cerrar la sesión.", "error");
    }
  }

  if (session?.authenticated) {
    const displayName = session.email || session.actor || "Gestor verificado";
    const initial = displayName.slice(0, 1).toLocaleUpperCase("es-EC");
    return (
      <div className="session-control">
        <span className="operator-avatar" aria-hidden="true">{initial}</span>
        <span className="operator-name">{displayName}</span>
        <button className="button button-ghost button-small" type="button" onClick={handleLogout} disabled={isLoggingOut}>
          {isLoggingOut ? "Cerrando…" : "Cerrar sesión"}
        </button>
      </div>
    );
  }

  if (isLoading) return <span className="session-muted">Verificando sesión…</span>;
  return (
    <div className="session-control">
      <span className="session-muted">{isUnavailable ? "Sesión no disponible" : "Consulta de solo lectura"}</span>
      <LoginDialog triggerClassName="button button-primary button-small" />
    </div>
  );
}

function GlobalSearch() {
  const [value, setValue] = useState("");
  const [mobileOpen, setMobileOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (mobileOpen) inputRef.current?.focus();
  }, [mobileOpen]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const q = value.trim();
    navigate(q ? `/tasks?q=${encodeURIComponent(q)}` : "/tasks");
  }

  return (
    <form className={`global-search${mobileOpen ? " mobile-search-open" : ""}`} role="search" onSubmit={(event) => { submit(event); setMobileOpen(false); }}>
      <button
        className="mobile-search-trigger"
        type="button"
        aria-label={mobileOpen ? "Cerrar búsqueda global" : "Abrir búsqueda global"}
        onClick={() => setMobileOpen((open) => !open)}
      >
        {mobileOpen ? <X aria-hidden="true" size={16} /> : <Search aria-hidden="true" size={16} />}
      </button>
      <Search className="desktop-search-icon" aria-hidden="true" size={16} />
      <input
        ref={inputRef}
        type="search"
        aria-label="Buscar tareas globalmente"
        placeholder="Buscar tareas…"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      <kbd aria-hidden="true">↵</kbd>
    </form>
  );
}

function Breadcrumbs() {
  const location = useLocation();
  const title = routeTitles[location.pathname] ?? "Espacio de trabajo";
  return (
    <nav className="breadcrumbs" aria-label="Ruta de navegación">
      <Link to="/">Inicio</Link>
      <span aria-hidden="true">/</span>
      <span aria-current="page">{title}</span>
    </nav>
  );
}

function MobileNavigation() {
  const [open, setOpen] = useState(false);
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button type="button" className="icon-button mobile-menu-trigger" aria-label="Abrir navegación">
          <Menu aria-hidden="true" size={20} />
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay mobile-nav-overlay" />
        <Dialog.Content className="mobile-nav-dialog" aria-describedby={undefined}>
          <Dialog.Title className="sr-only">Navegación principal</Dialog.Title>
          <div className="mobile-nav-header">
            <Brand />
            <Dialog.Close asChild>
              <button className="icon-button" aria-label="Cerrar navegación" type="button"><X aria-hidden="true" size={18} /></button>
            </Dialog.Close>
          </div>
          <ProjectSelect compact />
          <SidebarNavigation onNavigate={() => setOpen(false)} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function AppShell() {
  const { theme, toggleTheme } = useTheme();
  const projectsQuery = useProjects();
  const { selectedProjectId, setSelectedProjectId } = useWorkspace();
  const projects = projectsQuery.data?.items ?? [];

  useEffect(() => {
    if (projects.length && !selectedProjectId) {
      setSelectedProjectId(projects[0].id);
    }
  }, [projects, selectedProjectId, setSelectedProjectId]);



  return (
    <div className="app-frame min-h-screen">
      <a className="skip-link" href="#main-content">Saltar al contenido principal</a>
      <aside className="desktop-sidebar">
        <Brand />
        <ProjectSelect />
        <SidebarNavigation />
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <CircleHelp aria-hidden="true" size={16} />
            <span>Datos respaldados por capturas del backend.</span>
          </div>
          <span className="sidebar-version">GESTIÓN Y SEGUIMIENTO DE PROYECTOS</span>
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="topbar-leading">
            <MobileNavigation />
            <div className="mobile-brand"><Brand /></div>
            <GlobalSearch />
          </div>
          <div className="topbar-actions">
            <div className="topbar-project"><ProjectSelect compact /></div>
            <SystemStatus />
            <Link to="/audit" className="icon-button topbar-history" aria-label="Historial de eventos">
              <Clock3 aria-hidden="true" size={18} />
            </Link>
            <button
              type="button"
              className="icon-button theme-toggle"
              onClick={toggleTheme}
              aria-label={theme === "light" ? "Cambiar a modo oscuro" : "Cambiar a modo claro"}
              title={theme === "light" ? "Modo oscuro" : "Modo claro"}
            >
              {theme === "light" ? <Moon aria-hidden="true" size={18} /> : <Sun aria-hidden="true" size={18} />}
            </button>
            <SessionControl />
          </div>
        </header>

        <main className="page-container" id="main-content" tabIndex={-1}>
          <Breadcrumbs />
          <AgentWorkflowBar />
          <Outlet />
        </main>
      </div>
    </div>
  );
}
