# Acciones del encabezado

Las acciones **Sincronizar proyecto** y **Enviar correo** están en el encabezado y disponibles desde las secciones del espacio de trabajo. Abrir cualquiera de sus diálogos solo muestra la interfaz: no prepara, confirma ni envía nada. La navegación, búsqueda, selección de proyecto, tema y sesión permanecen en el encabezado.

## Sincronizar proyecto

1. Abre **Sincronizar proyecto** y elige **Preparar análisis** para solicitar al servidor una revisión de permisos y configuración.
2. Si la preparación requiere servicios en la nube, revisa los proveedores y el uso de datos; marca **Autorizo el uso de servicios en la nube** solo si das ese consentimiento.
3. **Confirmar análisis** queda habilitado únicamente con una preparación válida, permiso del servidor y, cuando corresponda, consentimiento explícito. La confirmación usa protección CSRF e idempotencia.

El estado del trabajo se puede consultar al volver a abrir el diálogo. El seguimiento continúa mientras esté cerrado y, al terminar, se actualizan los resultados del proyecto.

## Enviar correo

Revisa el destinatario —correo verificado o configurado, o preferencia local del proyecto— y usa **Enviar correo** para abrir la confirmación. Solo **Confirmar envío a [destinatario]** solicita la entrega. La interfaz muestra el canal real: que un mensaje quede guardado en el outbox local no significa que se haya enviado a una bandeja; los fallos o la aceptación del servicio también se informan sin afirmar recepción.

Los diálogos tienen título y descripción accesibles, devuelven el foco al control que los abrió, se cierran con Escape y permiten desplazamiento en pantallas pequeñas. Las secciones mantienen superficies y separadores visibles en ambos temas.
