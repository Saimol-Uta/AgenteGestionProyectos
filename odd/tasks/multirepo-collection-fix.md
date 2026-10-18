# Conservative MultiRepo collection fix
Branch: `feature/multirepo`; baseline: `8e7d970f9044595f1a850e1ac52e2a8685ed9255`. Delivery: local work-unit commits only, no publication.
Runner: root `.venv/bin/python`, `PYTHONPATH=<worktree>/src`, `EXTERNAL_WRITES_ENABLED=false`, `PYTHONDONTWRITEBYTECODE=1`. Forecast: approximately 350–450 authored lines; keep behavior/tests together, no artificial splitting.

## Specs
S1. "Corregir `github.py` para distinguir ausencia de patch, disponibilidad de contenido completo y agotamiento real de límites."
S2. "Modificar `composite.py` para conservar diffs y evidencias parciales ya validados, sin convertir automáticamente una ausencia de patch en `limit_exceeded`."
S3. "Desacoplar la consulta de GitHub Actions del éxito completo de la recopilación de código, únicamente cuando exista identidad de repositorio y SHA objetivo confiables."
S4. "Conservar la procedencia exacta de cada archivo, workflow, ejecución e intento."
S5. "Corregir la contabilización y persistencia interna de recibos de presupuesto, sin romper contratos públicos ni datos históricos."
S6. "Actualizar las pruebas existentes cuya expectativa de fallo total ya no corresponda con el comportamiento corregido."
S7. "RDD permanece desactivado." "`EXTERNAL_WRITES_ENABLED=false`." "No modificar los repositorios backend ni frontend de HORMIGESTION." "No realizar push ni merge." "No ejecutar análisis completos con Gemini o Groq." "No aumentar los límites HTTP, bytes, archivos o tokens."
S8. "No debilitar validaciones de SHA, identidad, contenido, secretos, autenticación o permisos." "No interpretar contenido completo recuperado como un diff que GitHub no proporcionó." "No declarar completa una recopilación con evidencia faltante." "No convertir una falla de seguridad, procedencia o validación en éxito parcial." "No cambiar todavía las políticas de análisis de calidad sobre evidencia parcial."
S9. "Primero ejecuta pruebas focalizadas para los escenarios reproducidos. Después realiza una regresión limitada de las funcionalidades afectadas." "Los archivos binarios sin patch no provoquen un falso agotamiento de presupuesto." "Los CSS recuperados con validaciones correctas conserven su evidencia." "Los diffs válidos no se descarten por archivos incompletos." "CI pueda consultarse con un SHA confiable aunque la recopilación de código sea parcial." "Un workflow fallido conserve su estado real." "Los resultados de backend y frontend permanezcan aislados." "Los límites reales continúen bloqueando la recopilación cuando corresponda."
S10. "Minimiza el consumo de tokens y evita repetir pruebas que no se vean afectadas." "Al finalizar, presenta los archivos modificados, las pruebas ejecutadas, los resultados obtenidos y cualquier limitación pendiente." "No inicies nuevas funcionalidades ni el análisis incremental. Detente después de esta corrección y su verificación local."

## Tasks
T1 [S1–S4,S6–S9] route=delegated (context backstop signaled); status=done; RED observed (5 tests, 6 cases failing), GREEN 5 focused and 203 limited-regression tests; parent spot-check 3 tests and diff whitespace pass; commit=4cd4b5c. Risk: item 2 (preserve fail-closed validation while admitting partial evidence).
T2 [S5,S7–S9] route=delegated plus bounded inline compatibility correction; status=done; RED three tests (2 missing-field errors, singleton passed), GREEN 27 limited orchestration/persistence tests; legacy hash correction RED1/GREEN25 plus exact baseline/current comparison; commit=49b6e5c. Risk: item 1 (additive persisted diagnostic data, preserve historical reads).
T3 [S4,S7–S10] route=inline plus independent verifier (items 1/2); status=done; seam224, independent15, final affected25, baseline/current singleton stdout/stderr/exit match, local commits and structural checks observed; evidence commit=docs(odd): close conservative multirepo collection fix.

## Log
L1. User authorization (verbatim):
Autorizo implementar la corrección mínima conservadora propuesta para resolver el fallo `limit_exceeded` en MultiRepo.

Mantén la rama `feature/multirepo` y continúa en la sesión actual de diagnóstico, utilizando el contexto y las evidencias ya obtenidas.

### Alcance autorizado

1. Corregir `github.py` para distinguir ausencia de patch, disponibilidad de contenido completo y agotamiento real de límites.
2. Modificar `composite.py` para conservar diffs y evidencias parciales ya validados, sin convertir automáticamente una ausencia de patch en `limit_exceeded`.
3. Desacoplar la consulta de GitHub Actions del éxito completo de la recopilación de código, únicamente cuando exista identidad de repositorio y SHA objetivo confiables.
4. Conservar la procedencia exacta de cada archivo, workflow, ejecución e intento.
5. Corregir la contabilización y persistencia interna de recibos de presupuesto, sin romper contratos públicos ni datos históricos.
6. Actualizar las pruebas existentes cuya expectativa de fallo total ya no corresponda con el comportamiento corregido.

### Restricciones

- RDD permanece desactivado.
- `EXTERNAL_WRITES_ENABLED=false`.
- No modificar los repositorios backend ni frontend de HORMIGESTION.
- No realizar push ni merge.
- No ejecutar análisis completos con Gemini o Groq.
- No aumentar los límites HTTP, bytes, archivos o tokens.
- No debilitar validaciones de SHA, identidad, contenido, secretos, autenticación o permisos.
- No interpretar contenido completo recuperado como un diff que GitHub no proporcionó.
- No declarar completa una recopilación con evidencia faltante.
- No convertir una falla de seguridad, procedencia o validación en éxito parcial.
- No cambiar todavía las políticas de análisis de calidad sobre evidencia parcial.

### Verificación

Primero ejecuta pruebas focalizadas para los escenarios reproducidos. Después realiza una regresión limitada de las funcionalidades afectadas.

Comprueba específicamente que:

- Los archivos binarios sin patch no provoquen un falso agotamiento de presupuesto.
- Los CSS recuperados con validaciones correctas conserven su evidencia.
- Los diffs válidos no se descarten por archivos incompletos.
- CI pueda consultarse con un SHA confiable aunque la recopilación de código sea parcial.
- Un workflow fallido conserve su estado real.
- Los resultados de backend y frontend permanezcan aislados.
- Los límites reales continúen bloqueando la recopilación cuando corresponda.

Minimiza el consumo de tokens y evita repetir pruebas que no se vean afectadas.

Al finalizar, presenta los archivos modificados, las pruebas ejecutadas, los resultados obtenidos y cualquier limitación pendiente.

**No inicies nuevas funcionalidades ni el análisis incremental. Detente después de esta corrección y su verificación local.**

L2. Worktree isolation: current checkout contains unrelated human changes on feature/header-action-modals. Created registered same-clone `.runtime/multirepo-collection-fix` on existing feature/multirepo without touching those changes. Reuse diagnostic map and 198-test baseline; no repeated exploration needed. No remote GitHub or model calls.
L3. Implementation route is a single bounded worker due to the runtime context backstop, not file count. Parent retains tracking and final review. Proposed fix must keep missing patch completeness false even after full_file recovery. CI may proceed only after all code/provenance/secret validations succeed; security failures still publish no code/CI. Do not invent APIs, flags, migrations or quality policies. Work-unit size is advisory, never omit meaningful checks to reduce lines.
L4. T1 regression203 and parent spot3 passed; T2 focused27 passed; combined seam224 passed. Independent verifier passed S1–S10 with15 selected unittest tests and isolated baseline/current public-orchestrator comparisons. Pytest unavailable (exit1, no tests), equivalent unittest succeeded; no dependency installation. Native ASSESS remained unassessable due untracked task file, no RDD start/retry; high-risk escalations require independent verification, completed. Differential found an unintended singleton stage data_hash delta despite unchanged summary; preserve legacy hash when no additive diagnostics and pin with RED/GREEN (bounded inline edit). No security blocker or live service checks.
L5. Compatibility correction RED1 then GREEN25 passed. Public singleton regression ran against isolated baseline source and current source; stdout, stderr and exit matched, including legacy data_hash and idempotent artifact preservation assertions. No additional full regression repeated. Independent verifier total15 selected unittest tests passed; pytest unavailable and not installed. Review workload is 754 authored implementation/test/doc lines (mostly regression assertions), kept as two local coherent commits; no PR or publication is initiated.
L6. Local correction slice boundaries are baseline 8e7d970f9044595f1a850e1ac52e2a8685ed9255 -> 4cd4b5c (457 authored lines, validated partial code/CI and tests/docs), then 4cd4b5c -> 49b6e5c (297 authored lines, scoped receipts/compatibility and tests/docs). The pre-existing feature branch accumulated history is not part of this correction or a newly authorized PR. No publication strategy is selected because no PR/push/merge is authorized. Evidence-only closure commit records these verified boundaries.
L7. Final limitations: no live GitHub/HORMIGESTION re-execution or external quota proof; historical receipts cannot be reconstructed. All executable functional checks passed after documented RED and harness corrections. Native read-only ASSESS was unavailable with the initially untracked task document, not retried; independent high-risk verification completed. RDD stayed off; no limits, quality policies, public models, historical artifacts or HORMIGESTION repositories changed.
