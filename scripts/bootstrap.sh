#!/usr/bin/env bash
# Bootstrap SAND: create .sand-venv, install editable package + dev deps.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="${SAND_VENV:-.sand-venv}"

pick_python() {
  if [[ -n "${SAND_PYTHON:-}" ]]; then
    echo "$SAND_PYTHON"
    return
  fi
  local candidate
  for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      local ver
      ver="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
      # Prefer 3.11+ (project requires-python)
      "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        && { echo "$candidate"; return; }
    fi
  done
  echo "error: need Python 3.11+ (set SAND_PYTHON=/path/to/python)" >&2
  exit 1
}

PYTHON="$(pick_python)"
echo "==> Using Python: $PYTHON ($("$PYTHON" -V 2>&1))"
echo "==> Virtualenv:   $VENV"

if [[ ! -d "$VENV" ]]; then
  "$PYTHON" -m venv "$VENV"
else
  echo "==> Reusing existing $VENV"
fi

# Prefer the venv's pip/python (avoid broken AppImage/uv interpreters)
# shellcheck disable=SC1091
source "$VENV/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"

# Pre-install DuckDB excel extension (used for native XLSX ingest)
python - <<'PY'
import duckdb
con = duckdb.connect()
con.execute("INSTALL excel")
con.execute("LOAD excel")
print("==> DuckDB excel extension ready")
PY

# Ensure local Plotly.js bundle (uses CDN when online; keeps existing file offline)
python - <<'PY'
from sand.web.plotly_vendor import ensure_local_bundle, check_and_update, current_status
try:
    ensure_local_bundle()
    check_and_update()
except Exception as exc:
    status = current_status()
    if status.get("bundle_ready"):
        print(f"==> Plotly.js local bundle ready ({status.get('version')}); update skipped: {exc}")
    else:
        raise SystemExit(f"error: Plotly.js bundle missing and could not download: {exc}") from exc
else:
    status = current_status()
    print(f"==> Plotly.js {status.get('version')} ({status.get('message')})")
PY

cat <<EOF

SAND bootstrap complete.

  source $VENV/bin/activate
  sand serve                 # http://127.0.0.1:8765
  pytest -q                  # from repo root
  sand ingest file.csv --dataset demo

Optional LLM:
  export SAND_LLM_API_KEY=...
  export SAND_LLM_BASE_URL=https://api.openai.com/v1
  export SAND_LLM_MODEL=gpt-4o-mini

Optional API token (Docker / published binds):
  export SAND_API_TOKEN=...

EOF
