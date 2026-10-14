import { ArrowUpRight, LockKeyhole, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";

export function PendingModulePage({ moduleName }: { moduleName: string }) {
  return (
    <section className="pending-page">
      <div className="page-heading-row">
        <div>
          <p className="eyebrow">MÓDULO DEL PRODUCTO</p>
          <h1>{moduleName}</h1>
          <p className="page-subtitle">Esta sección espera la integración de su contrato y operaciones reales del backend.</p>
        </div>
      </div>
      <div className="pending-card">
        <div className="pending-illustration" aria-hidden="true">
          <Sparkles size={27} />
          <span className="pending-lock"><LockKeyhole size={14} /></span>
        </div>
        <div className="pending-copy">
          <span className="pill pill-warning">Pendiente de integración</span>
          <h2>{moduleName} todavía no está disponible</h2>
          <p>
            Esta navegación se conserva para orientar el producto, pero no presenta datos de ejemplo ni acciones simuladas.
            Cuando la API publique este módulo, aquí se mostrarán sus resultados verificados.
          </p>
          <Link className="button button-secondary" to="/">
            Volver al dashboard <ArrowUpRight aria-hidden="true" size={15} />
          </Link>
        </div>
      </div>
    </section>
  );
}
