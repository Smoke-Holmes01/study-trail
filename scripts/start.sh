#!/usr/bin/env bash
set -euo pipefail
src_root="$(cd "$(dirname "$0")/.." && pwd)"
runtime_root="${RUNTIME_ROOT:-$src_root/.local/v004}"
project="${STUDY_TRAIL_COMPOSE_PROJECT:-study-trail-v004}"
runtime_root="$(realpath -m "$runtime_root")"
daily_root="$(realpath -m "$src_root/.local/v004")"
if [[ "$runtime_root" == "$daily_root" ]]; then
    [[ "$project" == 'study-trail-v004' ]] || { echo 'Runtime/project identity mismatch' >&2; exit 1; }
else
    [[ "$project" != 'study-trail-v004' ]] || { echo 'Runtime/project identity mismatch' >&2; exit 1; }
fi
export RUNTIME_ROOT="$runtime_root" STUDY_TRAIL_ENV_FILE="${STUDY_TRAIL_ENV_FILE:-$runtime_root/backend.env}"
api_port="${API_PORT:-8000}"
ui_port="${UI_PORT:-5173}"
if [ "$project" != 'study-trail-v004' ]; then api_port=8010; ui_port=5180; fi
export VITE_API_TARGET="http://127.0.0.1:$api_port"
mkdir -p "$runtime_root"
[ ! -f "$runtime_root/maintenance" ] || { echo "Maintenance active" >&2; exit 1; }
(cd "$src_root/infra" && docker compose -p "$project" -f "$src_root/infra/compose.yaml" --env-file "$runtime_root/infra.env" up -d --wait)
(cd "$src_root/backend" && uv sync --frozen && uv run python tools/check_installation.py)
(cd "$src_root/backend" && exec .venv/bin/python -m uvicorn study_trail.api:app --host 127.0.0.1 --port "$api_port") >"$runtime_root/api.log" 2>&1 &
echo "$!" >"$runtime_root/api.pid"
(cd "$src_root/backend" && .venv/bin/python tools/manage_runtime.py record --role api --pid "$(cat "$runtime_root/api.pid")")
(cd "$src_root/backend" && exec .venv/bin/python -m study_trail.execution) >"$runtime_root/worker.log" 2>&1 &
echo "$!" >"$runtime_root/worker.pid"
(cd "$src_root/backend" && .venv/bin/python tools/manage_runtime.py record --role worker --pid "$(cat "$runtime_root/worker.pid")")
(cd "$src_root/frontend" && exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port "$ui_port" --strictPort) >"$runtime_root/frontend.log" 2>&1 &
echo "$!" >"$runtime_root/frontend.pid"
(cd "$src_root/backend" && .venv/bin/python tools/manage_runtime.py record --role frontend --pid "$(cat "$runtime_root/frontend.pid")")
echo "Study Trail: http://127.0.0.1:$ui_port"
