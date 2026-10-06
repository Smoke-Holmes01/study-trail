#!/usr/bin/env bash
set -euo pipefail
src_root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$src_root/.local"
(cd "$src_root/infra" && docker compose up -d --wait)
(cd "$src_root/backend" && uv sync --frozen && uv run alembic upgrade head)
(cd "$src_root/backend" && exec .venv/bin/python -m uvicorn study_trail.api:app --host 127.0.0.1 --port 8000) >"$src_root/.local/api.log" 2>&1 &
echo "$!" >"$src_root/.local/api.pid"
(cd "$src_root/backend" && exec .venv/bin/python -m study_trail.execution) >"$src_root/.local/worker.log" 2>&1 &
echo "$!" >"$src_root/.local/worker.pid"
(cd "$src_root/frontend" && exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --strictPort) >"$src_root/.local/frontend.log" 2>&1 &
echo "$!" >"$src_root/.local/frontend.pid"
echo 'Study Trail: http://127.0.0.1:5173'
