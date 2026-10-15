import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { queryKeys } from "../../api/queries";
import type { OperatorSession } from "../../api/types";

interface SessionContextValue {
  session: OperatorSession | undefined;
  isLoading: boolean;
  isUnavailable: boolean;
  isGuest: boolean;
  setGuestMode: (value: boolean) => void;
  login: (email: string, password: string, fromGuest?: boolean) => Promise<void>;
  logout: () => Promise<void>;
  isLoggingOut: boolean;
}

const SessionContext = createContext<SessionContextValue | null>(null);

const GUEST_STORAGE_KEY = "gestionpro_guest_mode";

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [isGuest, setIsGuest] = useState<boolean>(() => {
    try {
      return sessionStorage.getItem(GUEST_STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });
  const sessionQuery = useQuery({
    queryKey: queryKeys.session,
    queryFn: api.getSession,
    staleTime: 30_000,
    retry: false,
  });

  const setGuestMode = useCallback((value: boolean) => {
    setIsGuest(value);
    try {
      if (value) {
        sessionStorage.setItem(GUEST_STORAGE_KEY, "true");
      } else {
        sessionStorage.removeItem(GUEST_STORAGE_KEY);
      }
    } catch {
      // Ignore storage restrictions
    }
  }, []);

  const loggedFromGuestRef = useRef<boolean>(false);

  const login = useCallback(async (email: string, password: string, fromGuest?: boolean) => {
    const authenticatedSession = await api.login(email, password);
    if (!authenticatedSession.authenticated) {
      throw new Error("El backend no confirmó una sesión autenticada.");
    }
    loggedFromGuestRef.current = fromGuest ?? isGuest;
    queryClient.setQueryData(queryKeys.session, authenticatedSession);
    setGuestMode(false);
  }, [queryClient, isGuest, setGuestMode]);

  const logout = useCallback(async () => {
    const csrfToken = sessionQuery.data?.csrf_token;
    if (!sessionQuery.data?.authenticated || !csrfToken) {
      throw new Error("La sesión no contiene una credencial CSRF válida; no se envió el cierre.");
    }
    setIsLoggingOut(true);
    try {
      await api.logout(csrfToken);
      if (loggedFromGuestRef.current) {
        setGuestMode(true);
      } else {
        setGuestMode(false);
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.session });
    } finally {
      setIsLoggingOut(false);
    }
  }, [queryClient, sessionQuery.data, setGuestMode]);

  const value = useMemo(() => ({
    session: sessionQuery.data,
    isLoading: sessionQuery.isLoading,
    isUnavailable: sessionQuery.isError,
    isGuest,
    setGuestMode,
    login,
    logout,
    isLoggingOut,
  }), [
    sessionQuery.data,
    sessionQuery.isLoading,
    sessionQuery.isError,
    isGuest,
    setGuestMode,
    login,
    logout,
    isLoggingOut,
  ]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession debe usarse dentro de SessionProvider.");
  return context;
}
