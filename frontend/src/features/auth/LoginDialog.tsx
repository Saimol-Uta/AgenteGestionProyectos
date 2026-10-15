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
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function handleEmailChange(value: string) {
    setEmail(value);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      await login(email, password, true);
      setPassword("");
      setOpen(false);
      notify("Sesión verificada por el backend.", "success");
    } catch (cause) {
      setError(cause instanceof ApiError && cause.status === 401
        ? "Correo electrónico o contraseña incorrectos. Verifique sus credenciales de Gestor del Proyecto."
        : cause instanceof ApiError
          ? cause.message
          : "No se pudo iniciar sesión. Inténtalo de nuevo.");
    } finally {
      setSubmitting(false);
    }
  }

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen);
    if (!nextOpen) {
      setPassword("");
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
                Inicia sesión con el correo y la contraseña de tu cuenta de Gestor del Proyecto.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button className="icon-button dialog-close" aria-label="Cerrar inicio de sesión" type="button">
                <X aria-hidden="true" size={17} />
              </button>
            </Dialog.Close>
          </div>

          <form className="form-stack" onSubmit={handleSubmit}>
            <label className="field-label" htmlFor="operator-email">Correo electrónico</label>
            <div className="input-with-icon" style={{ position: "relative", marginBottom: "12px" }}>
              <input
                id="operator-email"
                className="text-input"
                type="email"
                autoComplete="email"
                required
                placeholder="gestor@ejemplo.com"
                value={email}
                onChange={(event) => handleEmailChange(event.target.value)}
                disabled={submitting || session?.login_available === false}
              />
            </div>

            <label className="field-label" htmlFor="operator-password">Contraseña</label>
            <div className="input-with-icon" style={{ position: "relative", marginBottom: "12px" }}>
              <input
                id="operator-password"
                className="text-input"
                type="password"
                autoComplete="current-password"
                required
                placeholder="Contraseña"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={submitting || session?.login_available === false}
              />
            </div>

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
              <button
                className="button button-primary"
                type="submit"
                disabled={submitting || session?.login_available === false}
              >
                {submitting ? <LoaderCircle className="spin" aria-hidden="true" size={16} /> : <KeyRound aria-hidden="true" size={16} />}
                Iniciar Sesión como Gestor
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
