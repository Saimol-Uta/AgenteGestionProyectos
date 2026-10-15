import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  KeyRound,
  LoaderCircle,
  Moon,
  Sun,
  Eye,
  EyeOff,
} from "lucide-react";
import { ApiError } from "../../api/client";
import { useSession } from "./SessionProvider";
import { useTheme } from "../theme/ThemeProvider";
import { useToast } from "../../components/ToastProvider";

export function LoginPage() {
  const navigate = useNavigate();
  const { session, login, isUnavailable, setGuestMode } = useSession();
  const { theme, toggleTheme } = useTheme();
  const { notify } = useToast();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      await login(email, password, false);
      notify("Sesión verificada por el backend.", "success");
      navigate("/");
    } catch (cause) {
      setError(cause instanceof ApiError && cause.status === 401
        ? "Correo electrónico o contraseña incorrectos. Verifique sus credenciales de Gestor del Proyecto."
        : cause instanceof ApiError
          ? cause.message
          : "No se pudo iniciar sesión. Verifica las credenciales e inténtalo de nuevo.");
    } finally {
      setSubmitting(false);
    }
  }

  function handleContinueAsGuest() {
    setGuestMode(true);
    notify("Accediendo en modo de consulta (solo lectura).", "info");
    navigate("/");
  }

  return (
    <div className="login-page-container" style={{
      minHeight: "100vh",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      padding: "24px 16px",
      background: "var(--app-bg)",
      position: "relative",
    }}>
      {/* Theme Toggle Top Right */}
      <div style={{ position: "absolute", top: "16px", right: "20px" }}>
        <button
          type="button"
          className="icon-button theme-toggle"
          onClick={toggleTheme}
          aria-label={theme === "light" ? "Cambiar a modo oscuro" : "Cambiar a modo claro"}
          title={theme === "light" ? "Modo oscuro" : "Modo claro"}
        >
          {theme === "light" ? <Moon aria-hidden="true" size={18} /> : <Sun aria-hidden="true" size={18} />}
        </button>
      </div>

      <div style={{ width: "100%", maxWidth: "440px" }}>
        {/* Brand Header */}
        <div style={{ textAlign: "center", marginBottom: "28px" }}>
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: "52px",
            height: "52px",
            borderRadius: "14px",
            background: "var(--accent)",
            color: "#fff",
            fontSize: "24px",
            fontWeight: "700",
            marginBottom: "12px",
            boxShadow: "0 4px 14px rgb(79 94 215 / 30%)",
          }}>
            G
          </div>
          <h1 style={{
            margin: "0 0 4px",
            fontSize: "22px",
            fontWeight: "700",
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
          }}>
            GestiónPro
          </h1>
          <p style={{
            margin: 0,
            fontSize: "12px",
            color: "var(--text-muted)",
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            fontWeight: "600",
          }}>
            Plataforma de Proyectos y Gobernanza
          </p>
        </div>

        {/* Login Card */}
        <div className="panel" style={{
          padding: "28px 26px",
          borderRadius: "14px",
          boxShadow: "var(--shadow-dialog)",
        }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: "12px", marginBottom: "20px" }}>
            <span className="dialog-icon" style={{ marginTop: "2px" }}>
              <KeyRound aria-hidden="true" size={20} />
            </span>
            <div>
              <h2 style={{
                margin: "0 0 4px",
                fontSize: "17px",
                fontWeight: "670",
                color: "var(--text-primary)",
                letterSpacing: "-0.02em",
              }}>
                Acceso de operador
              </h2>
              <p style={{
                margin: 0,
                fontSize: "12px",
                lineHeight: "1.5",
                color: "var(--text-muted)",
              }}>
                Ingresa con tu correo y contraseña de Gestor del Proyecto. El backend verifica tu identidad y concede las capacidades de esta sesión.
              </p>
            </div>
          </div>

          <form className="form-stack" style={{ marginTop: "16px" }} onSubmit={handleSubmit}>
            <div style={{ marginBottom: "14px" }}>
              <label className="field-label" htmlFor="login-email">
                Correo electrónico
              </label>
              <input
                id="login-email"
                className="text-input"
                type="email"
                autoComplete="email"
                required
                placeholder="gestor@ejemplo.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting || session?.login_available === false}
              />
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label className="field-label" htmlFor="login-password">
                Contraseña
              </label>
              <div style={{ position: "relative" }}>
                <input
                  id="login-password"
                  className="text-input"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={submitting || session?.login_available === false}
                  style={{ paddingRight: "36px" }}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((prev) => !prev)}
                  style={{
                    position: "absolute",
                    right: "10px",
                    top: "50%",
                    transform: "translateY(-50%)",
                    background: "none",
                    border: "none",
                    color: "var(--text-muted)",
                    cursor: "pointer",
                    padding: 0,
                    display: "flex",
                    alignItems: "center",
                  }}
                  aria-label={showPassword ? "Ocultar clave" : "Mostrar clave"}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            {error && (
              <p className="form-error" role="alert" style={{ marginBottom: "14px" }}>
                {error}
              </p>
            )}

            {session?.login_available === false && (
              <p className="form-error" role="status" style={{ marginBottom: "14px" }}>
                El backend no ofrece inicio de sesión en este momento.
              </p>
            )}

            {isUnavailable && (
              <p className="field-help" style={{ marginBottom: "14px" }}>
                No fue posible verificar el estado inicial; el backend validará las credenciales al continuar.
              </p>
            )}

            <button
              className="button button-primary"
              type="submit"
              disabled={submitting || session?.login_available === false}
              style={{
                width: "100%",
                height: "42px",
                fontSize: "13px",
                fontWeight: "600",
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                gap: "8px",
              }}
            >
              {submitting ? (
                <LoaderCircle className="spin" aria-hidden="true" size={17} />
              ) : (
                <KeyRound aria-hidden="true" size={17} />
              )}
              <span>Iniciar Sesión como Gestor</span>
            </button>

            {/* Read-Only Guest Option */}
            <div style={{ textAlign: "center", marginTop: "16px", paddingTop: "14px", borderTop: "1px solid var(--border)" }}>
              <button
                type="button"
                className="button button-ghost"
                onClick={handleContinueAsGuest}
                style={{ fontSize: "11px", color: "var(--text-muted)" }}
              >
                Ingresar en Modo Consulta (Solo lectura)
              </button>
            </div>
          </form>
        </div>

        {/* Footer info */}
        <div style={{ textAlign: "center", marginTop: "20px", color: "var(--text-faint)", fontSize: "11px" }}>
          <span>GESTIÓNPRO · PLATAFORMA DE GESTIÓN Y AUDITORÍA DE PROYECTOS</span>
        </div>
      </div>
    </div>
  );
}
