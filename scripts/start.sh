#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" || ! -f "$PROJECT_DIR/.venv/pyvenv.cfg" ]]; then
  printf '%s\n' 'Run ./scripts/setup.sh first to create the project environment.' >&2
  exit 1
fi
cd -- "$PROJECT_DIR"
exec "$PYTHON_BIN" -m investbell.server --host 127.0.0.1 --port 8765 --data-dir "$PROJECT_DIR/data" "$@"
