import { policyStatusLabel } from "../../api/formatters";
import { EmptyState, Pill } from "../../components/States";
import type { PolicyResult } from "../../api/types";

function policyTone(status: string): "neutral" | "positive" | "warning" | "danger" | "unknown" | "accent" {
  if (status === "pass") return "positive";
  if (status === "fail") return "danger";
  return "unknown";
}

export function PolicyResults({ availability, items }: { availability: "available" | "unknown"; items: PolicyResult[] }) {
  if (availability === "unknown") {
    return <EmptyState title="Políticas desconocidas" message="El backend no dispone de evaluaciones persistidas para mostrar; no se crean filas Q1, Q2 o Q3." />;
  }
  if (items.length === 0) {
    return <EmptyState title="Sin evaluaciones registradas" message="La respuesta disponible no contiene reglas evaluadas." />;
  }

  return (
    <ul className="policy-result-chart" aria-label="Resultados de políticas evaluadas">
      {items.map((policy) => (
        <li key={policy.rule_id} className={`policy-result policy-result-${policy.status}`}>
          <span className="policy-result-rule"><strong>{policy.rule_id}</strong><span>{policy.description}</span></span>
          <span className="policy-result-status"><Pill tone={policyTone(policy.status)}>{policyStatusLabel(policy.status)}</Pill><code>{policy.status}</code></span>
          <span className="policy-result-value">Observado: {policy.observed_value === null ? "Sin dato" : typeof policy.observed_value === "boolean" ? policy.observed_value ? "Sí" : "No" : String(policy.observed_value)}</span>
          <span className="policy-result-mandatory">{policy.mandatory ? "Obligatoria" : "Opcional"}</span>
          {policy.reason && <p>{policy.reason}</p>}
        </li>
      ))}
    </ul>
  );
}
