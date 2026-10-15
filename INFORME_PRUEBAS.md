# Verificación del sistema

La integración actual se verificó con estado temporal, fuentes deterministas y transportes simulados. No se acredita disponibilidad de proveedores, escrituras remotas reales ni recepción de correo por las pruebas locales.

## Resultados observados

| Área | Comando o comprobación | Resultado |
|---|---|---|
| Backend completo | `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` | Código 0; 845 pruebas ejecutadas en 17,550 s: 844 aprobadas y 1 optativa omitida |
| Tipos web | `cd frontend && npm run typecheck` | Aprobado, código 0 |
| Compilación web | `cd frontend && npm run build` | Aprobado, código 0 |
| Navegador completo | `cd frontend && npx playwright test` | Código 0; 92 pruebas aprobadas en 38,3 s |
| Análisis/configuración | `.venv/bin/python -m unittest tests.test_web_api_jobs tests.test_unified_runtime_config -v` | 16 pruebas aprobadas al retomar la integración, incluido pipeline determinista con corrida e informe persistidos |
| Configuración y consultas | Suite focalizada de configuración, aliases, precedencia y consultas de proyectos | 60 pruebas aprobadas |
| Gobernanza Trello e integración | `.venv/bin/python -m unittest tests.test_phase2_integration tests.test_phase2_trello -v` | 27 pruebas aprobadas |
| Defaults de agentes e informe | `.venv/bin/python -m unittest tests.test_agents tests.test_due_soon -q` | 28 pruebas aprobadas |
| Verificación independiente | `.venv/bin/python -m unittest tests.test_web_api_jobs tests.test_unified_runtime_config tests.test_phase2_integration tests.test_phase2_trello -v` y probe HTTP sintético aislado | Código 0; 45 pruebas aprobadas en 5,784 s y probe aprobado. Dictamen sin bloqueos sobre configuración, fallback, consentimiento, destinatario, retirada de proveedores, interfaz, documentación y permisos |

La prueba optativa omitida es `tests.test_real_models.RealModelsTestCase.test_real_gemini_remote_smoke`: requiere `RUN_REAL_LLM_TESTS=1` y credencial de Gemini. No se activó esa llamada remota. No se presenta el resultado como 845 aprobadas ni como aceptación live.

La prueba focalizada del modo mixto, el chequeo de tipos, la compilación y la suite completa de navegador terminaron con código 0 en 48 s. El contrato `AnalysisJob.execution_mode` se probó primero con una aserción fallida y luego con la misma aserción aprobada tras alinear consumidores y fixtures.

## Análisis sin claves externas

La regresión `test_missing_credentials_execute_deterministic_pipeline_and_persist_run_and_report` utiliza el ejecutor del motor y consulta la corrida y el informe mediante HTTP. No sustituye ese ejecutor por una función vacía. La preparación de un proyecto real sin credenciales devuelve elegibilidad y modo sandbox explícito; confirmar no requiere consentimiento remoto cuando ningún componente va a usar servicios remotos. Los casos de claves parciales verifican modo mixto y mantienen el consentimiento para los componentes que sí salen del equipo.

Los aliases `/api/v1/analysis/prepare` y `/api/v1/analysis/confirm` exigen `project_id` en el cuerpo y conservan sesión, origen, CSRF, idempotencia y vínculo de preparación. Las rutas con proyecto siguen disponibles.

Las pruebas Playwright usan respuestas HTTP interceptadas. Cubren confirmación habilitada cuando corresponde, estados de finalización, modo y causas de alternativa local, destinatario de sesión y ausencia de aprobación local. Son evidencia del contrato de la interfaz; la ejecución persistida del motor se comprueba por separado en Python.

## Permisos y acciones

- Trello sin credenciales o tablero vinculado selecciona sandbox antes de construir una escritura live.
- El preflight de membresía usa lecturas acotadas de usuario, tablero y membresías, sin redirecciones; observador o acceso denegado seleccionan sandbox.
- Un fallo de red o respuesta no verificable al comprobar permiso no se presenta como éxito sandbox.
- El error de una escritura live sigue siendo un error: no se reemplaza por un recibo local exitoso.
- GitHub conserva la comprobación de permiso `push`, fuente completa y SHA de base antes de crear una rama separada.
- Los tests de diagnósticos CLI bloquean HTTP externo y exigen SHA explícito para consultar Actions. Un diagnóstico no sustituye fuentes ausentes por un éxito simulado.

## Documentación y limpieza

Los siete documentos principales se reescribieron y cuatro guías redundantes se redujeron a índices. Se retiraron los ocho documentos obsoletos y las siete piezas de adaptadores/herramientas/datos que dependían de proveedores retirados. El catálogo coincide con las 47 rutas presentes en los routers; los enlaces locales de los 11 documentos actuales resuelven. El chequeo de whitespace terminó sin errores.

Los defaults directos de agentes y de composición usan Gemini para seguimiento/síntesis y Groq para calidad, con configuración explícita por rol. Un informe sin metadatos de proveedor informa que no se registraron; no inventa un modelo utilizado. Se conserva la detección de formatos antiguos de secretos exclusivamente como protección de redacción, no como proveedor seleccionable.

## Advertencias y límites

- Se observaron advertencias de deprecación de Starlette sobre `httpx`/`TestClient` y `ResourceWarning` por conexiones SQLite de pruebas sin cerrar. La suite terminó sin fallos; no se afirma que esté libre de advertencias.
- Un modo sandbox identifica datos/efectos simulados, no una operación remota.
- Credenciales presentes no prueban conectividad, permisos efectivos de cada token ni entrega.
- `smtp_email_sent` acredita aceptación del relay, no llegada a la bandeja.
- CI ausente, mediciones no observadas y hallazgos no corroborados conservan estados desconocidos o insuficientes.
- La matriz REQ-01 a REQ-11 enlaza implementación y límites; no atribuye un porcentaje global ni aceptación productiva sin evidencia externa.
- La evaluación nativa no estuvo disponible por el ámbito de archivos sin seguimiento; no se afirma aprobación nativa. La verificación independiente finalizó sin bloqueos, con 45 pruebas y un probe HTTP propios aprobados.
- Como observación no bloqueante, falta un test específico a nivel de ruta que compruebe ausencia de reserva ante una respuesta Trello de preflight malformada o inesperada. El orden del código verifica permiso antes de reservar; no se presenta esa inspección como una prueba automatizada adicional.
