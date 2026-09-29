#!/usr/bin/env bash
# Levanta el banco en MODO MOCK en Linux/macOS (sin hardware).
# Uso:  scripts/dev_run.sh [puerto]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8080}"
if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements-dev.txt
fi
export BENCH_MOCK=1 PYTHONUNBUFFERED=1
exec .venv/bin/python -m uvicorn --factory app.main:create_app --host 127.0.0.1 --port "$PORT"
