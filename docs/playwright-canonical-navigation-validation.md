# Validación de navegación canónica y estados de etapas

**Las ocho pruebas afectadas pasaron (8/8 en 11.0 s) y Playwright completo pasó (135/135 en 42.8 s), sin omisiones.** Typecheck y build terminaron con exit 0. Se conserva la navegación canónica y los seis commits anteriores.

## Alcance

- La evidencia MultiRepo permanece exclusivamente en `/analysis/:runId`. No se reinsertan paneles en Proyectos, Calidad ni en el centro de Análisis.
- Los enlaces existentes de Análisis → ejecución son suficientes: no se agregan rutas, persistencia de selección ni controles nuevos.
- `RunDetailPage` usa `verifier`, nombre real definido por `api/repository.py:_RUN_STAGES` y el orquestador. No se introducen aliases históricos no respaldados.
- Una etapa sin registro se muestra como **No registrado; estado desconocido**. Solo un `skipped` registrado se muestra como **Omitida explícitamente**; se mantienen **Completada**, **Fallida** y **Desconocida** según la respuesta pública.
- No se modifican pipeline, API, backend, formatter compartido, permisos, dependencias ni configuración de despliegue.

## Comprobaciones conservadas

| Grupo | Evidencia ejercitada hasta el final |
|---|---|
| Identidad, CI y cobertura | Repositorios independientes, SHA, workflow/intento, cobertura cero y alcance; fallo parcial sin cobertura global inventada |
| Refresco | Evidencia de ejecución anterior, ausencia de préstamo a la actual, invalidación tras job terminal y nueva evidencia de la ejecución actual |
| Workflows contradictorios | Identidad distinta no atribuida ni certificada para el informe |
| Legacy y permisos | Disponibilidad desconocida sin repositorio inventado; sin botones protegidos ni permiso concedido |
| Cambio de proyecto | Hallazgo viejo cerrado, nombre anterior ausente, selección secundaria conservada mediante navegación SPA y evidencia del proyecto correcto |
| Encabezado | Desktop/mobile y layout originales, polling con diálogo cerrado, reapertura, ausencia de doble confirmación y botón habilitado al terminar |
| Etapas | Caso original desconocido/omitido más una regresión pública adicional para completado, fallido, omitido registrado, desconocido registrado y no registrado |

La comparación estructural mediante AST TypeScript conserva las **91 aserciones originales**: 88 expresiones idénticas y tres correcciones semánticas explícitas (locator de opción oculta, texto del botón en curso y texto vigente del aviso de permisos). Sus garantías no se eliminan ni se convierten en skips. El chequeo diferencial lee Git únicamente desde un helper temporal de auditoría; las pruebas permanentes no leen historial Git.

## Resultados

| Comprobación | Resultado |
|---|---|
| RED de etapas | 2 fallos esperados, exit 1 |
| GREEN de etapas | 2 aprobadas en 3.4 s; Playwright y wrapper exit 0 |
| Ocho casos afectados | 8 aprobadas en 11.0 s; Playwright y wrapper exit 0 |
| Playwright completo | 135 aprobadas en 42.8 s, 0 fallos y 0 omitidas; exit 0 |
| Typecheck | `tsc --noEmit`, exit 0; stderr vacío |
| Build | Exit 0; Vite 1.55 s; aviso no fatal del directorio aislado de salida |
| Regresiones backend | No aplican: ningún contrato compartido ni código backend fue modificado |

Se conservan los resultados intermedios: un GREEN funcional cuyo wrapper falló por una variable de reporte no asignada, y selecciones de 6/8, 7/8 y 7/8 antes de corregir los locators, el copy de permisos y el montaje/navegación del proyecto secundario. No se cambiaron guardas, timeouts ni aserciones de seguridad para obtener verde. No se identificó un defecto de aplicación adicional que requiriera ampliar el alcance.

## Aislamiento y evidencia

El montaje nuevo está en `.runtime/playwright-canonical-navigation/`, separado de la base real y de ejecuciones históricas. Usa checkout y HOME/TMP/cachés/salidas nuevos, `env -i`, `EXTERNAL_WRITES_ENABLED=false`, dotenv deshabilitado y dependencias/Chromium ya instalados. No inicia backend ni realiza análisis reales: las respuestas API son fixtures interceptados por Playwright.

Se reutilizan sin edición las guardas Node/navegador existentes del scratch T6. Preflight comprueba cinco denegaciones deliberadas separadas, métodos íntegros en procesos hijos, hooks activos/limpios por prueba y cero intentos externos inesperados. Se preservan hashes de fuentes y prefijos exactos de pruebas antes de añadir hooks solo en el checkout desechable; ambos puertos quedan libres.

- GREEN de etapas: `runs/renewed-frontend-final-20261019T124559Z-2733991/results/`.
- Ocho casos: `runs/renewed-frontend-final-20261019T125430Z-2746392/results/`.
- Suite completa, tipos y build: `runs/renewed-frontend-final-20261019T125529Z-2749014/results/`.
- Auditoría de aserciones: `assertion-preservation-ast.json`.
- Las rutas anteriores son relativas al montaje nuevo. Sus runners locales requieren conservar los helpers temporales T6; no instalan ni descargan dependencias.

El único aviso de build indica que el directorio de salida está fuera del frontend aislado y no se vacía automáticamente. Es una carpeta nueva y propia del run; no se cambia esa protección ni se borra historial. Playwright y typecheck no emitieron stderr.

La prueba manual completa queda pendiente de revisión humana. No hubo llamadas a LLM reales, escrituras Trello/GitHub, push ni merge.
