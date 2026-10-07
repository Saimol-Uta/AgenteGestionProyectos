#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export OLLAMA_MODELS="$PROJECT_DIR/.runtime/models"
export OLLAMA_HOST=127.0.0.1:11435
export OLLAMA_CONTEXT_LENGTH=4096
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NO_CLOUD=1
export OLLAMA_KEEP_ALIVE=5m
if [[ ! -x "$PROJECT_DIR/.runtime/ollama/bin/ollama" ]]; then
  echo 'Primero ejecuta: python3 pruebas/instalar_ollama.py' >&2
  exit 1
fi
exec "$PROJECT_DIR/.runtime/ollama/bin/ollama" "$@"
