#!/usr/bin/env python3
"""Descarga y verifica una versión fijada de Ollama en el proyecto, sin sudo."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.35.1"
EXPECTED_SHA256 = "9fcd79ac4575b2bd31b992eee18b1000c8ad126b451627c8f8cd091714cfbb10"
URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-linux-amd64.tar.zst"


def main():
    for tool in ("curl", "tar", "zstd"):
        if not shutil.which(tool):
            raise RuntimeError(f"Falta {tool}")
    archive = ROOT / ".runtime/downloads" / f"ollama-linux-amd64-v{VERSION}.tar.zst"
    runtime = ROOT / ".runtime/ollama"
    archive.parent.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    if not archive.exists() or archive.stat().st_size != 1439658961:
        subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--continue-at", "-",
                        "--output", str(archive), URL], check=True)
    print("Verificando SHA-256…", flush=True)
    with archive.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != EXPECTED_SHA256:
        raise RuntimeError("SHA-256 incorrecto; no se extraerá el archivo. Conservado para inspección.")
    subprocess.run(["tar", "--zstd", "-xf", str(archive), "-C", str(runtime)], check=True)
    print(f"Ollama {VERSION} preparado en {runtime}")
    print("Inicia el servidor: bash pruebas/ollama-local.sh serve")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
