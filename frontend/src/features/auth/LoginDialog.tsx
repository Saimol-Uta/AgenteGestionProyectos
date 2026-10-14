import { useState, type FormEvent } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound, LoaderCircle, LogIn, X } from "lucide-react";
import { ApiError } from "../../api/client";
import { useSession } from "./SessionProvider";
import { useToast } from "../../components/ToastProvider";

export function LoginDialog({ triggerClassName = "button button-secondary" }: { triggerClassName?: string }) {
  const { session, login, isUnavailable } = useSession();
  const { notify } = useToast();
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    const submittedToken = token;
    try {
      await login(submittedToken);
      setToken("");
      setOpen(false);
      notify("La sesión de operador está activa.", "success");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "No se pudo iniciar sesión. Inténtalo de nuevo.");
    } finally {
      setToken("");
      setSubmitting(false);
    }
  }

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen);
    if (!nextOpen) {
      setToken("");
      setError(null);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Trigger asChild>
        <button className={triggerClassName} type="button">
          <LogIn aria-hidden="true" size={16} />
          <span>Iniciar sesión</span>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content login-dialog">
          <div className="dialog-heading">
            <span className="dialog-icon"><KeyRound aria-hidden="true" size={19} /></span>
            <div>
              <Dialog.Title className="dialog-title">Acceso de operador</Dialog.Title>
              <Dialog.Description className="dialog-description">
                Ingresa el token de operador aprovisionado por el backend. No es una clave de proveedor.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button className="icon-button dialog-close" aria-label="Cerrar inicio de sesión" type="button">
                <X aria-hidden="true" size={17} />
              </button>
            </Dialog.Close>
          </div>

          <form className="form-stack" onSubmit={handleSubmit}>
            <label className="field-label" htmlFor="operator-token">Token de operador</label>
            <input
              id="operator-token"
              className="text-input"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={token}
              onChange={(event) => setToken(event.target.value)}
              disabled={submitting || session?.login_available === false}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "login-error" : "login-help"}
              required
            />
            <p className="field-help" id="login-help">
              El token solo se envía al backend para verificar esta sesión y no se guarda en este dispositivo.
            </p>
            {error && <p className="form-error" id="login-error" role="alert">{error}</p>}
            {session?.login_available === false && (
              <p className="form-error" role="status">El backend no ofrece inicio de sesión en este momento.</p>
            )}
            {isUnavailable && (
              <p className="field-help">No fue posible consultar el estado de sesión; el backend validará el acceso.</p>
            )}
            <div className="dialog-actions">
              <Dialog.Close asChild>
                <button className="button button-ghost" type="button">Cancelar</button>
              </Dialog.Close>
              <button className="button button-primary" type="submit" disabled={submitting || !token.trim() || session?.login_available === false}>
                {submitting ? <LoaderCircle className="spin" aria-hidden="true" size={16} /> : <KeyRound aria-hidden="true" size={16} />}
                Verificar y continuar
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
