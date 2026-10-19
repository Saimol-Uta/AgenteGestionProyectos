# G3 — Cadena Riesgo → Tarea → Propuesta → Revisión → Decisión → Resultado

Este documento describe **G3**: los riesgos deterministas de G2 se vinculan con las propuestas correctivas y con las decisiones del gestor, de modo que el recorrido completo sea trazable.

## 1. El vínculo se declara por tipo, no por identificador

El `risk_id` de G2 depende de la captura: identifica un riesgo **de una captura concreta**. Guardarlo en las reglas del propietario lo volvería frágil (la misma regla dejaría de valer en la siguiente captura) y lo convertiría en una referencia permanente a algo que ya no existe.

Por eso las reglas declaran el **tipo** de riesgo que atienden:

```json
{
  "rule_id": "demo-g3-riesgo-proxima-vencimiento",
  "task_ids": ["6a9582c16a2c16f6106609e2"],
  "field": "due_date",
  "allowed_values": ["2026-10-20T17:00:00-05:00"],
  "risk_kind": "due_soon"
}
```

`risk_kind` es opcional y validado contra el conjunto cerrado de `RiskKind` (`blocked`, `overdue`, `due_soon`, `contradictory_state`, `unknown_state`). **Una regla sin `risk_kind` conserva exactamente su serialización anterior**: el `config_hash`, el `proposal_id` y el cuerpo persistido de las propuestas que ya existían no cambian.

## 2. Resolución durante Monitoring

El riesgo concreto se resuelve contra **la misma captura y la misma evidencia** con las que se construye la propuesta:

1. El orquestador calcula una sola vez los riesgos deterministas de la captura (`risks_from_metrics` sobre las métricas de esa captura).
2. Ese mismo objeto viaja al agente de Monitoring y al guardado de propuestas, así que el candidato y la propuesta histórica coinciden por construcción.
3. `monitoring_choices` solo ofrece un candidato cuando la tarea tiene un riesgo del tipo declarado. Si no lo tiene, no hay candidato y queda el motivo `risk_not_present`.

La resolución ocurre después de confirmar la tarea y la visibilidad del campo, y antes de evaluar el valor y la evidencia: `unknown_task`, `conflicting_task` y `field_not_visible` conservan su prioridad.

El agente elige **índices de opción**, nunca objetos: las referencias viajan en los objetos canónicos, de modo que el LLM no puede inventarlas ni alterarlas.

## 3. La propuesta conserva la referencia

`ActionProposal` admite `risk_refs`: la referencia al riesgo concreto (`risk_id`), a su tipo, a la tarea y a la captura de origen.

- Una propuesta **con** riesgo usa el esquema `action_proposal_v2` y lleva la referencia.
- Una propuesta **sin** riesgo conserva el esquema `action_proposal_v1` y su cuerpo byte a byte idéntico al anterior. El campo opcional se omite del cuerpo cuando está vacío, y el almacén lo acepta mediante `OPTIONAL_SUBMISSION_FIELDS` sin relajar la invariante "todo campo del modelo está en el cuerpo" para el resto.
- A las propuestas históricas no se les inventa ninguna asociación: siguen siendo v1 y su `risk_refs` es una lista vacía.

El `proposal_id` ata la asociación (el volcado de la opción canónica entra en el hash), de modo que el identificador de una propuesta con riesgo es nuevo y determinista, y el de una propuesta sin riesgo no cambia.

`validate_action_proposal` comprueba la coherencia interna de la referencia: la captura, la tarea, el tipo y la versión del esquema.

## 4. Lo que ve el gestor

| Superficie | Novedad |
| --- | --- |
| Resumen del proyecto | Cada riesgo trae sus `proposals`: las propuestas del mismo proyecto y captura que realmente lo atienden, con su estado de revisión, autorización y ejecución. `proposals_context` expone el diagnóstico persistido de la última ejecución, con su `run_id` y su `capture_id`. |
| Detalle de tarea | `capture_id` y, por riesgo, las propuestas vinculadas. |
| Propuestas | Cada propuesta expone `risk_refs`: el riesgo que la motivó y si todavía se puede verificar contra la clasificación de esa captura (`resolved`). |

Una referencia que no se resuelve se publica con `resolved: false`: no se oculta ni se presenta como válida.

## 5. Lo que el sistema NO afirma

- **Un riesgo no se marca como resuelto** porque una propuesta se apruebe o se ejecute. Hace falta evidencia posterior: una captura nueva en la que la tarea ya no se clasifique con ese riesgo. La interfaz lo declara de forma explícita.
- La relación es **por captura**. Si una captura posterior cambia el riesgo, la relación antigua se conserva como histórica y no se presenta como la situación actual.
- La narrativa del LLM (`MonitoringDraft.risks`, `suggested_actions`) no es autoridad: no crea propuestas ni referencias de riesgo.
- Un riesgo sin propuesta muestra que no hay una propuesta vinculada y, cuando el diagnóstico persistido lo explica, el motivo. Nunca una recomendación inventada.

## 6. Aislamiento

Los enlaces se construyen con las propuestas del mismo proyecto (`list_action_proposals(project_id)`) y la misma captura. Un proyecto, una captura o una tarea ajenos siguen respondiendo 404, y una propuesta de otro proyecto nunca aparece en los riesgos de otro.

## 7. Recorrido manual reproducible

El arnés `tests/g3_hormigestion_demo_server.py` sirve la aplicación real sobre una **copia temporal** de la captura real de HORMIGESTION (la base de origen se abre en solo lectura y su SHA-256 no cambia). Las reglas de demostración viven solo en ese directorio temporal y están marcadas como caso de prueba.

```bash
# Terminal 1: API aislada con la regla que declara el tipo de riesgo
cd frontend
EXTERNAL_WRITES_ENABLED=false PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python ../tests/g3_hormigestion_demo_server.py   # 127.0.0.1:4175

# Terminal 2: interfaz
cd frontend && npm run dev -- --host=127.0.0.1 --port=4173 --strictPort
# con H5_API_PROXY_TARGET=http://127.0.0.1:4175
```

Recorrido: iniciar sesión local → *Análisis* → *Preparar análisis* (debe anunciar "Reglas de propuestas cargadas") → *Confirmar análisis* → *Tablero*: el panel **Riesgos de la captura** muestra el riesgo "Próxima a vencer" con la propuesta vinculada y su estado, y el riesgo "Estado desconocido" declara que no hay propuesta vinculada → pulsar el identificador de la tarea del riesgo: se abre el detalle con la clasificación, su fundamento y la trazabilidad a la captura → *Propuestas*: la bandeja muestra la etiqueta del riesgo y el detalle la sección **Riesgo que motivó la acción** con el enlace a su tarea de origen → aprobar con motivo: el resultado es sandbox y deja recibo durable, y el riesgo **sigue vigente** porque una propuesta aprobada no demuestra que la condición haya desaparecido.

## 8. G3 en una frase

La regla declara el **tipo** de riesgo; Monitoring resuelve el riesgo **concreto** contra la captura real; la propuesta lo **conserva**; la interfaz lo **muestra** junto al estado de revisión y decisión; y nada de eso afirma que el riesgo haya desaparecido.
