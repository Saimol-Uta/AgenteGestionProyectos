#!/usr/bin/env bash

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

FILE="src/agente_gestion/adapters/llm/gemini.py"
PYTHON=".venv/bin/python"

RUN_REAL=false

if [[ "${1:-}" == "--real" ]]; then
    RUN_REAL=true
fi

echo "============================================================"
echo " Corrección Gemini structured output"
echo "============================================================"
echo "Proyecto: $ROOT"
echo "Archivo:  $FILE"
echo

# ------------------------------------------------------------
# 1. Comprobaciones previas
# ------------------------------------------------------------

if [[ ! -f "$FILE" ]]; then
    echo "ERROR: No existe $FILE"
    exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: No existe $PYTHON"
    echo "Comprueba que .venv esté creado."
    exit 1
fi

# No se lee ni imprime .env.
# La aplicación seguirá cargándolo mediante su configuración normal.

# ------------------------------------------------------------
# 2. Backup temporal
# ------------------------------------------------------------

BACKUP="/tmp/gemini.py.$(date +%Y%m%d-%H%M%S).bak"
cp "$FILE" "$BACKUP"

echo "Backup temporal:"
echo "  $BACKUP"
echo

# ------------------------------------------------------------
# 3. Aplicar modificación exacta
# ------------------------------------------------------------

"$PYTHON" - "$FILE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

old = '''        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt_text}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": request.max_generation_tokens or self.max_output_tokens,
            },
        }

        if request.json_schema:
            body["generationConfig"]["responseSchema"] = request.json_schema
'''

new = '''        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt_text}],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": request.max_generation_tokens or self.max_output_tokens,
            },
        }

        if request.json_schema:
            body["generationConfig"]["responseFormat"] = {
                "text": {
                    "mimeType": "APPLICATION_JSON",
                    "schema": request.json_schema,
                }
            }
'''

if new in text:
    print("El cambio ya estaba aplicado. No se modificó el archivo.")
    raise SystemExit(0)

if old not in text:
    print(
        "ERROR: No encontré exactamente el bloque esperado.\n"
        "No realizaré una sustitución aproximada para evitar dañar el archivo."
    )
    raise SystemExit(2)

path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Bloque Gemini actualizado correctamente.")
PY

echo

# ------------------------------------------------------------
# 4. Verificar que no quede responseSchema antiguo
# ------------------------------------------------------------

echo "Verificando implementación..."
echo

if rg -n 'responseSchema' "$FILE"; then
    echo
    echo "ADVERTENCIA: todavía existe responseSchema en $FILE"
    echo "Revisa esas coincidencias antes de continuar."
    exit 1
fi

rg -n -C 5 'responseFormat|mimeType|schema' "$FILE" || true

echo

# ------------------------------------------------------------
# 5. Compilación Python
# ------------------------------------------------------------

echo "Comprobando sintaxis Python..."

"$PYTHON" -m compileall -q src/agente_gestion

echo "✓ compileall correcto"
echo

# ------------------------------------------------------------
# 6. git diff --check
# ------------------------------------------------------------

echo "Comprobando whitespace/diff..."

git diff --check -- "$FILE"

echo "✓ git diff --check correcto"
echo

# ------------------------------------------------------------
# 7. Localizar y ejecutar tests relacionados con Gemini
# ------------------------------------------------------------

echo "Buscando tests relacionados con GeminiProvider..."

mapfile -t TEST_FILES < <(
    rg -l 'GeminiProvider|gemini' tests \
        --glob '*.py' 2>/dev/null \
        | sort -u
)

if (( ${#TEST_FILES[@]} > 0 )); then
    printf '  %s\n' "${TEST_FILES[@]}"
    echo
    echo "Ejecutando tests relacionados..."

    "$PYTHON" -m pytest -q "${TEST_FILES[@]}"

    echo
    echo "✓ Tests relacionados completados"
else
    echo "No encontré tests específicos de Gemini."
    echo "No se ejecutará la suite completa automáticamente."
fi

echo

# ------------------------------------------------------------
# 8. Mostrar diff para revisión humana
# ------------------------------------------------------------

echo "============================================================"
echo " DIFF"
echo "============================================================"

git diff -- "$FILE"

echo
echo "============================================================"

# ------------------------------------------------------------
# 9. Por defecto terminar aquí
# ------------------------------------------------------------

if [[ "$RUN_REAL" != true ]]; then
    echo
    echo "Corrección local terminada."
    echo
    echo "NO se realizó ninguna llamada real a Gemini."
    echo
    echo "Revisa el diff de arriba."
    echo
    echo "Si todo está correcto y Ollama está levantado, ejecuta:"
    echo
    echo "  ./scripts/fix_gemini_structured_output.sh --real"
    echo
    exit 0
fi

# ------------------------------------------------------------
# 10. Prueba E2E real
# ------------------------------------------------------------

echo
echo "============================================================"
echo " PRUEBA REAL"
echo "============================================================"
echo
echo "Se ejecutará el pipeline con --allow-remote-ai."
echo "Esto PUEDE consumir cuota real de Gemini."
echo

"$PYTHON" -m agente_gestion run proyecto-demo --allow-remote-ai

echo

# ------------------------------------------------------------
# 11. Localizar último run
# ------------------------------------------------------------

LATEST_RUN="$(find runs -maxdepth 1 -type d -name 'run-*' \
    -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | head -n1 \
    | cut -d' ' -f2-)"

if [[ -z "$LATEST_RUN" ]]; then
    echo "No pude localizar el directorio del último run."
    exit 0
fi

echo "Último run:"
echo "  $LATEST_RUN"
echo

# ------------------------------------------------------------
# 12. Mostrar evidencia relevante
# ------------------------------------------------------------

if command -v jq >/dev/null 2>&1; then

    if [[ -f "$LATEST_RUN/execution_metrics.json" ]]; then
        echo "------------------------------------------------------------"
        echo "execution_metrics.json"
        echo "------------------------------------------------------------"

        jq '{
            run_id,
            status,
            duration_seconds,
            models_used,
            provider_errors,
            escalations,
            total_llm_calls
        }' "$LATEST_RUN/execution_metrics.json"
        echo
    fi

    if [[ -f "$LATEST_RUN/verification.json" ]]; then
        echo "------------------------------------------------------------"
        echo "verification.json"
        echo "------------------------------------------------------------"

        jq . "$LATEST_RUN/verification.json"
        echo
    fi

else
    echo "jq no está instalado; omito visualización estructurada."
fi

echo "============================================================"
echo " Prueba terminada"
echo "============================================================"
