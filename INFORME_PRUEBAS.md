# Pruebas de modelos en tu laptop

Ejecución del 2 de octubre de 2026, hora de Guayaquil. La laptop pudo ejecutar
los tres candidatos. **Qwen3.5 2B fue el más rápido y se cargó completamente en GPU;
los dos modelos de 4B repartieron la carga entre CPU y GPU.** La elección de
configuración afecta la precisión: ningún modelo superó todas las comprobaciones.

Se instalaron Ollama 0.35.1 y los modelos dentro del proyecto. El runtime se
verificó con el SHA-256 publicado en su release oficial. El equipo detectado fue
Ryzen 5 6600H, 14,8 GiB de RAM utilizable y RTX 3050 Laptop de 4096 MiB, con
controlador NVIDIA 615.71.09 y backend CUDA. Las funciones cloud se desactivaron.

Se ejecutaron **83 solicitudes de prueba**, además de calentamientos: 72 para las
dos comparaciones de casos cortos, 9 con más datos y 2 con razonamiento activado.
81 produjeron JSON válido; las otras dos agotaron su presupuesto de salida.
El comparador también pasó siete comprobaciones de validación y puntuación.

## Comparación inicial: temperatura cero

Cuatro casos simulados —seguimiento, revisión de código, rechazo justificado y
aprobación justificada—, tres repeticiones, contexto 4096, máximo 768 tokens de
salida, razonamiento desactivado, una petición a la vez y seis hilos de CPU.

| Modelo Q4_K_M | Mediana tok/s | Mediana de respuesta | VRAM máxima observada | Aciertos de campos |
|---|---:|---:|---:|---:|
| Qwen3.5 2B | 95,6 | 1,98 s | 2456 MiB | 45/54 (83,3%) |
| Qwen3 4B Instruct 2507 | 33,8 | 6,59 s | 2736 MiB | 42/54 (77,8%) |
| Qwen3.5 4B | 22,7 | 8,21 s | 3065 MiB | 48/54 (88,9%) |

La latencia excluye el calentamiento inicial. La VRAM indicada pertenece a toda
la GPU; la asignación específica del modelo está en `/api/ps`, conservada con
cada respuesta. RAM y swap también se midieron para todo el sistema. No son
mediciones exclusivas del proceso de inferencia.

[Resumen y CSV iniciales](resultados/20261002-150741-270144/resumen.md).

## Comparación con los parámetros de Qwen

Mismos cuatro casos y límites, con temperatura 0,7, top_p 0,8, top_k 20, min_p 0,
repeat_penalty 1 y semillas 42, 43 y 44. Qwen3.5 usa presence_penalty 1,5;
Qwen3 Instruct usa 0. Estos ajustes proceden de las fichas oficiales para el modo
sin razonamiento y tareas generales. [Qwen3.5](https://huggingface.co/Qwen/Qwen3.5-4B#best-practices),
[Qwen3 Instruct](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507#best-practices).

| Modelo Q4_K_M | Mediana tok/s | Mediana de respuesta | Aciertos de campos |
|---|---:|---:|---:|
| Qwen3.5 2B | 92,6 | 2,32 s | 38/54 (70,4%) |
| Qwen3 4B Instruct 2507 | 33,9 | 6,75 s | 44/54 (81,5%) |
| Qwen3.5 4B | 22,3 | 8,12 s | 43/54 (79,6%) |

En esta muestra, los ajustes mejoraron la puntuación de Qwen3 Instruct y
redujeron la de Qwen3.5. No hay una configuración ganadora universal. Tres
repeticiones y cuatro casos sirven para orientar el prototipo; no permiten
generalizar un porcentaje de precisión a proyectos reales.

[Resumen y CSV del perfil Qwen](resultados/20261002-152233-648943/resumen.md).

## Entrada más larga

Contexto 8192 y 66 tarjetas: las seis originales más 60 pendientes futuras.
Esta variante usó temperatura cero, tres repeticiones y el caso de seguimiento.
Los prompts tenían 4186 tokens en Qwen3.5 y 4402 en Qwen3: superaron los 4096
tokens de la primera ventana y cabían en la ventana de 8192 con la reserva de salida.

| Modelo | Mediana tok/s | Mediana de respuesta | Aciertos de campos |
|---|---:|---:|---:|
| Qwen3.5 2B | 98,2 | 2,20 s | 9/18 |
| Qwen3 4B Instruct 2507 | 16,0 | 14,44 s | 9/18 |
| Qwen3.5 4B | 15,1 | 48,17 s | 6/18 |

Los tres completaron el JSON, pero calcularon mal totales, porcentajes y vencidas.
El resultado correcto era 66 tarjetas, 2 completadas, 3,0% de avance y solo
T2 y T3 vencidas. Poder cargar una ventana de contexto no garantiza interpretar
correctamente todos sus datos. La mayor temperatura de GPU observada entre
todas las pruebas fue de 68 °C; el muestreo de un segundo puede omitir picos breves.

[Resumen de contexto](resultados/20261002-151306-939052/resumen.md).

## Razonamiento activado

Qwen3.5 4B se probó con los casos cortos de seguimiento y código, contexto 8192,
temperatura cero y límite de 2048 tokens de salida. En ambos casos consumió los
2048 tokens en razonamiento sin producir el JSON final, con tiempos de 108,76
y 108,24 segundos. No fue útil con ese presupuesto y esa configuración. Esta
prueba no evalúa su precisión con un presupuesto mayor o con muestreo distinto.

[Resultados de razonamiento](resultados/20261002-151752-257375/resumen.md).

## Qué reveló la revisión manual

La puntuación automática comprueba campos y referencias de línea; no certifica
que las explicaciones y correcciones sean correctas. Los modelos confundieron
tarjetas completadas con vencidas o usaron IDs de reglas incorrectos. Qwen3.5
2B llegó a afirmar que la división de una lista vacía devolvía cero.

Qwen3.5 4B propuso un gestor de contexto de `sqlite3.Connection` para cerrar la
conexión, pero ese gestor controla transacciones y no cierra la conexión. Se
verificó localmente y en la [documentación de Python](https://docs.python.org/3/library/sqlite3.html#how-to-use-the-connection-context-manager).
La corrección debe garantizar el cierre, por ejemplo mediante `try/finally`
o `contextlib.closing`. Por eso un resultado de 4/4 en detección de defectos
todavía necesita revisar sus soluciones.

## Cómo usar estos resultados en el proyecto

Para experimentar con respuestas rápidas, Qwen3.5 2B tiene el mejor rendimiento
observado y deja más margen de GPU. Para investigar la revisión de código,
Qwen3.5 4B detectó los tres defectos y sus líneas en todas las repeticiones
cortas, con respuestas más lentas. Esto es una orientación limitada a esta muestra.

El backend debería calcular totales, porcentajes y vencimientos, y validar las
reglas objetivas usando los resultados de CI. El modelo puede redactar el reporte,
explicar los hallazgos y sugerir acciones con esos hechos ya verificados. La
decisión final sigue perteneciendo al gestor, como plantea la conversación.
Las pruebas todavía no conectan Trello/GitHub ni ejecutan el flujo completo de agentes.

## Repetir las pruebas

Ollama se detuvo después de las pruebas; los modelos y los archivos se conservan.
Desde esta carpeta, abre una terminal y ejecuta:

```sh
bash pruebas/ollama-local.sh serve
```

En otra terminal:

```sh
python3 pruebas/benchmark.py run --sampling greedy
python3 pruebas/benchmark.py run --sampling qwen
```

La [guía del proyecto](README.md) incluye diagnóstico, descarga, contexto,
razonamiento y el formato de resultados. Cada carpeta conserva respuestas
completas, métricas, configuración, casos y detalles de los modelos.
