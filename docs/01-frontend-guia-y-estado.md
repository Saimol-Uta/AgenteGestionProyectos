# Guía de la aplicación web

La web ayuda al Gestor del Proyecto a consultar información, analizarla y decidir acciones. Se implementa con React 19, TypeScript, Vite, React Router y TanStack Query; las métricas, permisos y estados oficiales proceden del backend FastAPI.

## Mapa de navegación

| Área | Para qué sirve |
|---|---|
| Dashboard | Resumen de avance, alertas, historial de capturas, calidad e informes. |
| Proyectos | Seleccionar el proyecto dentro del alcance disponible. |
| Tareas | Filtrar, ordenar e inspeccionar tareas y evidencia observada. |
| Análisis | Preparar, confirmar y seguir el trabajo del análisis. |
| Calidad | Revisar hallazgos candidatos, evidencia y propuesta de código. |
| Propuestas | Evaluar cambios sugeridos para tareas y enviar una decisión humana. |
| Informes | Leer, descargar y decidir sobre informes ejecutivos. |
| Calendario | Consultar la programación y los recibos de outbox disponibles. |
| Auditoría | Consultar decisiones y eventos persistidos autorizados para la sesión. |
| Ajustes | Consultar capacidades y configuración pública; no permite cambiar secretos del servidor. |

## Sesión del Gestor

El acceso humano se realiza con correo y contraseña enviados a la API. El backend confirma la identidad, emite una cookie `HttpOnly` y entrega un token CSRF para las mutaciones. La UI conserva la sesión en memoria; no almacena tokens de proveedor ni convierte el modo de consulta en una sesión privilegiada. Las credenciales de demostración se indican únicamente en el [README](../README.md) y se deben reemplazar antes de producción.

Las solicitudes de escritura verifican en backend la sesión, el proyecto, el origen, CSRF, capability e idempotencia. El actor procede de la sesión validada, no de un campo editable del navegador. Un `false` explícito en una capacidad del servidor desactiva la función correspondiente aunque exista login.

## Análisis accionable y transparente

El recorrido del gestor es:

1. Preparar el análisis y revisar `ready`/`eligible`, fuentes, proveedores, modo previsto y `fallback_reasons`.
2. Dar consentimiento remoto explícito cuando el plan transferirá datos a proveedores/fuentes remotos. El permiso `ALLOW_REMOTE_AI` del servidor no sustituye esa confirmación.
3. Confirmar el análisis; el backend vuelve a validar sesión, proyecto y huella de configuración antes de encolar.
4. Seguir el trabajo y consultar el resultado persistido. La interfaz muestra el modo informado por el servidor (`live`, `sandbox` o `mixed`) y no deduce éxito a partir de un trabajo aceptado.

Las claves remotas son opcionales para que el flujo pueda usar fallback determinista local por componente. La causa se comunica, y los hallazgos sin evidencia suficiente permanecen pendientes/insuficientes. La UI no transforma un fallback en una ejecución cloud ni una ausencia de CI en cobertura aprobada.

## Propuestas, código y decisión final

- **Tareas/Trello:** el Ejecutor puede originar propuestas; el backend realiza revisión técnica y preflight. El gestor decide aprobar, modificar el campo de la propuesta o rechazarla con motivo. Si no se cumplen las condiciones de live, el backend informa sandbox antes de cualquier escritura. Un fallo después de iniciar una operación live no se muestra como simulación exitosa.
- **Código/GitHub:** el Revisor produce hallazgos candidatos. El gestor compara fuente y sugerencia; el servidor valida fuente completa y parche antes de crear una rama. Un artefacto local de sandbox no es una rama GitHub y una rama GitHub no abre automáticamente un Pull Request.
- **Informes:** el agente de reporte sintetiza hechos y recomendaciones. No es el Aprobador con autoridad. El Gestor del Proyecto conserva la decisión final, que se registra mediante el endpoint de decisión apropiado.
- **Correo:** el correo verificado de la sesión es el destinatario inicial de alertas. El gestor puede cambiarlo para enviar una copia. El recibo diferencia outbox local, aceptación SMTP y error SMTP; la aceptación del relay no asegura llegada al buzón.
- **Auditoría:** muestra únicamente eventos persistidos y disponibles para el proyecto. La UI no fabrica motivos, actores, estados de proveedor ni registros de ejecución.

## Estructura de frontend

```text
frontend/src/
├── api/          cliente HTTP, tipos, consultas y formato
├── app/          shell, rutas, sesión y proyecto seleccionado
├── components/   componentes compartidos y estados accesibles
├── features/     auth, dashboard, tasks, analysis, quality, proposals,
│                 reports, calendar, audit, projects, settings y notifications
└── styles.css    tokens visuales y estilos globales
```

El cliente central de `api/` usa rutas relativas `/api/v1`, maneja errores tipados y adjunta CSRF/idempotencia a operaciones protegidas. TanStack Query administra lectura, caché e invalidación; no convierte una respuesta fallida en un dato de éxito.

## Verificación de frontend

Desde la raíz del repositorio:

```sh
cd frontend && npm run typecheck
cd frontend && npm run build
cd frontend && npx playwright test
```

La integración actual pasó chequeo de tipos, compilación y 92 pruebas Playwright. Los casos incluyen el destinatario verificado, preparación local/mixta, consentimiento remoto y el campo de trabajo `execution_mode`. Playwright intercepta la API; las pruebas Python validan por separado el motor con corrida e informe persistidos. Los resultados y advertencias observados están en [INFORME_PRUEBAS.md](../INFORME_PRUEBAS.md).

## Contratos relacionados

- [Catálogo REST, sesión y permisos](02-backend-arquitectura-y-api.md)
- [Flujo de agentes y matriz REQ-01 a REQ-11](03-motor-de-agentes-y-requisitos.md)
- [Capas y contratos del sistema](04-analisis-comparativo-arquitectura.md)
- [Arquitectura del sistema](../ARCHITECTURA.md)
