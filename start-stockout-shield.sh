#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  for candidate in "$PROJECT_DIR/.venv/bin/python" "/home/ray/Documents/Codex/2026-10-16/i-x20/work/venv/bin/python" "$(command -v python3)"; do
    if [[ -x "$candidate" ]] && "$candidate" -c 'import streamlit, sqlalchemy, scipy' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo 'Install requirements.lock in .venv, then run this launcher again.' >&2
  exit 1
fi
cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -m stockout.server "$@"
