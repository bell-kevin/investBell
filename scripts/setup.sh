#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_BIN="${INVESTBELL_PYTHON:-python3}"

if [[ ! -f "$VENV_DIR/pyvenv.cfg" ]]; then
  "$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"; import venv, ensurepip' || {
    printf '%s\n' 'A Python 3.11+ installation with venv and ensurepip is required.' 'On Debian/Ubuntu, install python3-venv using your normal package-management process, then rerun this script.' >&2
    exit 1
  }
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"; import pip' || {
  printf '%s\n' 'The existing .venv needs a working Python 3.11+ interpreter and pip. Repair it before continuing.' >&2
  exit 1
}
"$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt"
mkdir -p -- "$PROJECT_DIR/data/reports"
printf '%s\n' 'Setup complete. Start the local app with ./scripts/start.sh.' 'No system services or scheduled jobs were enabled.'
