import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { CircleCheck, CircleX, Info, X } from "lucide-react";
import { Link } from "react-router-dom";

export type ToastTone = "success" | "error" | "info";

/** Optional destination offered inside a notice, so it can lead to the result. */
export interface ToastAction {
  label: string;
  to: string;
}

interface ToastEntry {
  id: number;
  message: string;
  tone: ToastTone;
  action: ToastAction | null;
}

interface ToastContextValue {
  notify: (message: string, tone?: ToastTone, action?: ToastAction) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);
let nextToastId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const notify = useCallback((message: string, tone: ToastTone = "info", action?: ToastAction) => {
    const id = ++nextToastId;
    setToasts((current) => [...current, { id, message, tone, action: action ?? null }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, 5000);
  }, []);

  const value = useMemo(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-region" aria-live="polite" aria-relevant="additions text">
        {toasts.map((toast) => {
          const Icon = toast.tone === "success" ? CircleCheck : toast.tone === "error" ? CircleX : Info;
          return (
            <div className={`toast toast-${toast.tone}`} key={toast.id} role="status">
              <Icon aria-hidden="true" size={18} />
              <span>{toast.message}</span>
              {toast.action && (
                <Link className="toast-action" to={toast.action.to} onClick={() => dismiss(toast.id)}>
                  {toast.action.label}
                </Link>
              )}
              <button
                type="button"
                className="icon-button toast-dismiss"
                aria-label="Descartar aviso"
                onClick={() => dismiss(toast.id)}
              >
                <X aria-hidden="true" size={16} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast debe usarse dentro de ToastProvider.");
  return context;
}
