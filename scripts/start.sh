#!/usr/bin/env bash
set -euo pipefail
src_root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$src_root/.local/v004"
[ ! -f "$src_root/.local/v004/maintenance" ] || { echo "Maintenance active" >&2; exit 1; }
(cd "$src_root/infra" && docker compose -p study-trail-v004 -f "$src_root/infra/compose.yaml" --env-file "$src_root/.local/v004/infra.env" up -d --wait)
(cd "$src_root/backend" && uv sync --frozen && uv run python tools/check_installation.py)
(cd "$src_root/backend" && exec .venv/bin/python -m uvicorn study_trail.api:app --host 127.0.0.1 --port 8000) >"$src_root/.local/v004/api.log" 2>&1 &
echo "$!" >"$src_root/.local/v004/api.pid"
(cd "$src_root/backend" && .venv/bin/python tools/manage_runtime.py record --role api --pid "$(cat "$src_root/.local/v004/api.pid")")
(cd "$src_root/backend" && exec .venv/bin/python -m study_trail.execution) >"$src_root/.local/v004/worker.log" 2>&1 &
echo "$!" >"$src_root/.local/v004/worker.pid"
(cd "$src_root/backend" && .venv/bin/python tools/manage_runtime.py record --role worker --pid "$(cat "$src_root/.local/v004/worker.pid")")
(cd "$src_root/frontend" && exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --strictPort) >"$src_root/.local/v004/frontend.log" 2>&1 &
echo "$!" >"$src_root/.local/v004/frontend.pid"
(cd "$src_root/backend" && .venv/bin/python tools/manage_runtime.py record --role frontend --pid "$(cat "$src_root/.local/v004/frontend.pid")")
echo 'Study Trail: http://127.0.0.1:5173'
