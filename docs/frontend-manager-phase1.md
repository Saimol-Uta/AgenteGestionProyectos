# Interfaz del gestor: continuidad visual de Fase 1

La interfaz de Fase 1 conserva navegación, temas, gráficos históricos, comparador de capturas y centro de decisiones. Su antiguo flujo operativo de decisiones locales y revisión/autorización/simulación manual **se retiró por decisión de arquitectura**; este documento ya no sirve como guía para ejecutar esas acciones.

## Flujo vigente

Consultá [Fase 2: acciones protegidas y correo](phase2-fullstack.md) para el flujo **Ejecutor → Revisor automático → Aprobador humano → ejecución**.

- Propuestas: la revisión técnica y la simulación son automáticas; aprobar, modificar o rechazar se registra en el backend con motivo y auditoría.
- Calidad: el código y su explicación vienen del backend; aprobar requiere fuente completa y validación del archivo modificado completo. No hay aprobación local, ejemplo inventado ni rama de fragmentos.
- Correo: la confirmación identifica al destinatario y el resultado distingue SMTP, error y HTML local. Un recibo local no acredita entrega a una bandeja.
- Consulta: no otorga permisos para aprobar ni ejecutar. Las preferencias de presentación del navegador no son decisiones ni autorizaciones del servidor.
- Demostración: los datos se regeneran desde cero con el esquema vigente y al menos dos snapshots completos para conservar comparación e historial.

## Verificación

Desde `frontend/`: `npm run typecheck`, `npm run build` y `npx playwright test`. Las pruebas del flujo vigente deben usar los contratos reales del backend; no conservar escenarios de aprobación local ni la cadena manual retirada. Los resultados comprobados y pendientes están en `odd/tasks/backend-fullstack-phase2.md`.
