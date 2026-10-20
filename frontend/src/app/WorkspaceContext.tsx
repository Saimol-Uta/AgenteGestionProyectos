import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

interface WorkspaceContextValue {
  selectedProjectId: string | null;
  setSelectedProjectId: (projectId: string | null) => void;
}

/**
 * Project the manager works on. It is the default selection when the session is
 * allowed to see it; every other authorized project stays selectable.
 */
export const PREFERRED_PROJECT = "HORMIGESTION";

/** Minimum shape the selection policy needs from a project. */
export interface SelectableProject {
  id: string;
  name: string;
}

/**
 * Initial project selection: the preferred project when the session can see it,
 * and otherwise the first project the session is allowed to use. The caller
 * already receives only the projects the session may access.
 */
export function preferredProjectId(projects: SelectableProject[]): string | null {
  if (projects.length === 0) return null;
  const preferred = projects.find(
    (project) => project.id === PREFERRED_PROJECT || project.name === PREFERRED_PROJECT,
  );
  return preferred ? preferred.id : projects[0].id;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const value = useMemo(
    () => ({ selectedProjectId, setSelectedProjectId }),
    [selectedProjectId],
  );
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("useWorkspace debe usarse dentro de WorkspaceProvider.");
  return context;
}
