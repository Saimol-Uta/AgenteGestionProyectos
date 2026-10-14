import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { queryKeys } from "../../api/queries";
import type { OperatorSession } from "../../api/types";

interface SessionContextValue {
  session: OperatorSession | undefined;
  isLoading: boolean;
  isUnavailable: boolean;
  login: (token: string) => Promise<void>;
  logout: () => Promise<void>;
  isLoggingOut: boolean;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const sessionQuery = useQuery({
    queryKey: queryKeys.session,
    queryFn: api.getSession,
    staleTime: 30_000,
    retry: false,
  });

  const login = useCallback(async (token: string) => {
    await api.login(token);
    await queryClient.invalidateQueries({ queryKey: queryKeys.session });
  }, [queryClient]);

  const logout = useCallback(async () => {
    const csrfToken = sessionQuery.data?.csrf_token;
    if (!sessionQuery.data?.authenticated || !csrfToken) {
      throw new Error("La sesión no contiene una credencial CSRF válida; no se envió el cierre.");
    }
    setIsLoggingOut(true);
    try {
      await api.logout(csrfToken);
      await queryClient.invalidateQueries({ queryKey: queryKeys.session });
    } finally {
      setIsLoggingOut(false);
    }
  }, [queryClient, sessionQuery.data]);

  const value = useMemo(() => ({
    session: sessionQuery.data,
    isLoading: sessionQuery.isLoading,
    isUnavailable: sessionQuery.isError,
    login,
    logout,
    isLoggingOut,
  }), [sessionQuery.data, sessionQuery.isLoading, sessionQuery.isError, login, logout, isLoggingOut]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession debe usarse dentro de SessionProvider.");
  return context;
}
