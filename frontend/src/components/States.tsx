import { AlertTriangle, Inbox, LoaderCircle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

export function LoadingState({ label = "Cargando información…" }: { label?: string }) {
  return (
    <div className="state-panel state-loading" role="status" aria-live="polite">
      <LoaderCircle className="spin" aria-hidden="true" size={20} />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({
  title = "No se pudo cargar la información",
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <section className="state-panel state-error" role="alert">
      <span className="state-icon"><AlertTriangle aria-hidden="true" size={19} /></span>
      <div className="state-copy">
        <h2>{title}</h2>
        <p>{message}</p>
        {onRetry && (
          <button className="button button-secondary button-small" onClick={onRetry} type="button">
            <RefreshCw aria-hidden="true" size={15} /> Intentar de nuevo
          </button>
        )}
      </div>
    </section>
  );
}

export function EmptyState({
  title,
  message,
}: {
  title: string;
  message: string;
}) {
  return (
    <div className="state-panel state-empty">
      <span className="state-icon"><Inbox aria-hidden="true" size={20} /></span>
      <div className="state-copy">
        <h2>{title}</h2>
        <p>{message}</p>
      </div>
    </div>
  );
}

export function Pill({ children, tone = "neutral" }: {
  children: ReactNode;
  tone?: "neutral" | "positive" | "warning" | "danger" | "unknown" | "accent";
}) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}
