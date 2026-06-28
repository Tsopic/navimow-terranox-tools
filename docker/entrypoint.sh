#!/bin/sh
set -eu

cd /app

command="${1:-serve}"
if [ "$command" = "serve" ]; then
  host="${HOST:-0.0.0.0}"
  port="${PORT:-8765}"
  db="${DB:-data/navimow.sqlite}"
  output="${VIEWER_OUTPUT:-viewer/navimow-map}"
  status_only="${STATUS_ONLY:-auto}"
  satellite="${SATELLITE:-0}"

  mkdir -p "$(dirname "$db")" "$output"

  build_args="--db $db --output $output"
  if [ "$satellite" != "1" ]; then
    build_args="$build_args --no-satellite"
  fi

  if [ "$status_only" = "1" ] || [ "$status_only" = "true" ]; then
    build_args="$build_args --status-only"
  elif [ "$status_only" = "auto" ]; then
    if ! python - "$db" <<'PY'
import sqlite3
import sys
from pathlib import Path

db = Path(sys.argv[1])
if not db.exists():
    raise SystemExit(1)

con = sqlite3.connect(db)
tables = {
    row[0]
    for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
}
required = {"map_detail_snapshots", "map_render_metadata"}
raise SystemExit(0 if required <= tables else 1)
PY
    then
      build_args="$build_args --status-only"
    fi
  fi

  # shellcheck disable=SC2086
  python tools/build_navimow_map_viewer.py $build_args
  exec python tools/navimow_viewer_server.py --host "$host" --port "$port" --directory "$output"
fi

if [ "$command" = "test" ]; then
  exec python -B -m pytest -q -p no:cacheprovider tests
fi

exec "$@"
