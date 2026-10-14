import { useState, type FormEvent } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound, LoaderCircle, LogIn, X } from "lucide-react";
import { ApiError } from "../../api/client";
import { useSession } from "./SessionProvider";
import { useToast } from "../../components/ToastProvider";
import { DEFAULT_OPERATOR_EMAIL, generateOperatorToken } from "./tokens";

export function LoginDialog({ triggerClassName = "button button-secondary" }: { triggerClassName?: string }) {
  const { session, login, isUnavailable } = useSession();
  const { notify } = useToast();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState(DEFAULT_OPERATOR_EMAIL);
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function handleEmailChange(value: string) {
    setEmail(value);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;

    // Resolve or generate operator token
    let tokenToSubmit = token.trim();
    if (!tokenToSubmit) {
      if (email.trim() && password.trim()) {
        tokenToSubmit = generateOperatorToken(email.trim(), password.trim());
      } else if (email.trim()) {
        tokenToSubmit = generateOperatorToken(email.trim());
      }
    }

    if (!tokenToSubmit) {
      setError("Ingresa tus credenciales o el token de operador para continuar.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await login(tokenToSubmit, email.trim() || undefined, true);
      setToken("");
      setPassword("");
      setOpen(false);
      notify("La sesión de operador está activa. Permisos completos habilitados.", "success");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "No se pudo iniciar sesión. Inténtalo de nuevo.");
    } finally {
      setSubmitting(false);
    }
  }

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen);
    if (!nextOpen) {
      setPassword("");
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
                Inicia sesión con correo y contraseña para generar el token de operador con permisos completos.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button className="icon-button dialog-close" aria-label="Cerrar inicio de sesión" type="button">
                <X aria-hidden="true" size={17} />
              </button>
            </Dialog.Close>
          </div>

          <form className="form-stack" onSubmit={handleSubmit}>
            <label className="field-label" htmlFor="operator-email">Correo de operador</label>
            <div className="input-with-icon" style={{ position: "relative", marginBottom: "12px" }}>
              <input
                id="operator-email"
                className="text-input"
                type="email"
                autoComplete="email"
                placeholder="operador@gestionproyectos.com"
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
                placeholder="Contraseña de operador"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={submitting || session?.login_available === false}
              />
            </div>

            <label className="field-label" htmlFor="operator-token">Token de operador</label>
            <input
              id="operator-token"
              className="text-input"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={token}
              placeholder="Aprovisionado automáticamente o ingresa token manual"
              onChange={(event) => setToken(event.target.value)}
              disabled={submitting || session?.login_available === false}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "login-error" : "login-help"}
            />

            <p className="field-help" id="login-help">
              Al iniciar sesión con tus credenciales se genera el token de operador para autenticar esta sesión en el backend con todos los permisos.
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
              <button
                className="button button-primary"
                type="submit"
                disabled={submitting || session?.login_available === false}
              >
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
