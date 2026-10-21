#!/usr/bin/env bash
set -euo pipefail
src_root="$(cd "$(dirname "$0")/.." && pwd)"
for name in api worker frontend; do
  file="$src_root/.local/$name.pid"
  if [[ -f "$file" ]]; then
    process_id="$(cat "$file")"
    if [[ "$process_id" =~ ^[0-9]+$ ]] && kill -0 "$process_id" 2>/dev/null; then
      process_command="$(ps -p "$process_id" -o args=)"
      case "$process_command" in *study_trail.api*|*study_trail.execution*|*vite/bin/vite.js*) kill "$process_id" ;; esac
    fi
    rm -- "$file"
  fi
done
echo 'Study Trail application processes stopped; database and files retained.'
