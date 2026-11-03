#!/usr/bin/env bash
set -euo pipefail
src_root="$(cd "$(dirname "$0")/.." && pwd)"
export STUDY_TRAIL_ENV_FILE="${STUDY_TRAIL_ENV_FILE:-${RUNTIME_ROOT:-$src_root/.local/v004}/backend.env}"
(cd "$src_root/backend" && .venv/bin/python tools/manage_runtime.py stop)
# Database data volumes are retained. Explicitly stop services with:
# docker compose -p study-trail-v004 -f "$src_root/infra/compose.yaml" --env-file "$src_root/.local/v004/infra.env" stop
