import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Tooltip from "@radix-ui/react-tooltip";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { lazy, Suspense, useEffect, useRef } from "react";
import { AppShell } from "./AppShell";
import { WorkspaceProvider } from "./WorkspaceContext";
import { SessionProvider } from "../features/auth/SessionProvider";
import { ThemeProvider } from "../features/theme/ThemeProvider";
import { ToastProvider } from "../components/ToastProvider";
import { LoadingState } from "../components/States";
import { ProjectsPage } from "../features/projects/ProjectsPage";
import { TasksPage } from "../features/tasks/TasksPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { ScrollToTop } from "./ScrollToTop";

const DashboardPage = lazy(() => import("../features/dashboard/DashboardPage").then(({ DashboardPage: page }) => ({ default: page })));
const AnalysisPage = lazy(() => import("../features/analysis/AnalysisPage").then(({ AnalysisPage: page }) => ({ default: page })));
const QualityPage = lazy(() => import("../features/quality/QualityPage").then(({ QualityPage: page }) => ({ default: page })));
const ProposalsPage = lazy(() => import("../features/proposals/ProposalsPage").then(({ ProposalsPage: page }) => ({ default: page })));
const ReportsPage = lazy(() => import("../features/reports/ReportsPage").then(({ ReportsPage: page }) => ({ default: page })));
const CalendarPage = lazy(() => import("../features/calendar/CalendarPage").then(({ CalendarPage: page }) => ({ default: page })));
const AuditPage = lazy(() => import("../features/audit/AuditPage").then(({ AuditPage: page }) => ({ default: page })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

function RoutedApp() {
  const location = useLocation();
  const previousPathname = useRef<string | null>(null);
  useEffect(() => {
    if (previousPathname.current !== null && previousPathname.current !== location.pathname) {
      const main = document.getElementById("main-content");
      main?.focus({ preventScroll: true });
    }
    previousPathname.current = location.pathname;
  }, [location.pathname]);

  return (
    <>
      <ScrollToTop />
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Suspense fallback={<LoadingState label="Preparando dashboard…" />}><DashboardPage /></Suspense>} />
          <Route path="projects" element={<ProjectsPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="analysis" element={<Suspense fallback={<LoadingState label="Preparando el centro de análisis…" />}><AnalysisPage /></Suspense>} />
          <Route path="quality" element={<Suspense fallback={<LoadingState label="Preparando el centro de calidad…" />}><QualityPage /></Suspense>} />
          <Route path="proposals" element={<Suspense fallback={<LoadingState label="Preparando la bandeja de propuestas…" />}><ProposalsPage /></Suspense>} />
          <Route path="reports" element={<Suspense fallback={<LoadingState label="Preparando los informes…" />}><ReportsPage /></Suspense>} />
          <Route path="calendar" element={<Suspense fallback={<LoadingState label="Preparando la programación semanal…" />}><CalendarPage /></Suspense>} />
          <Route path="audit" element={<Suspense fallback={<LoadingState label="Preparando la línea de auditoría…" />}><AuditPage /></Suspense>} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ThemeProvider>
          <ToastProvider>
            <Tooltip.Provider delayDuration={350}>
              <WorkspaceProvider>
                <SessionProvider>
                  <RoutedApp />
                </SessionProvider>
              </WorkspaceProvider>
            </Tooltip.Provider>
          </ToastProvider>
        </ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
