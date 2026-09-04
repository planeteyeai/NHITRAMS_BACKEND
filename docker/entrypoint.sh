#!/bin/sh
set -eu

echo "[entrypoint] Starting NHIT API container..."

# Optional wait for Postgres (useful with docker-compose local DB)
if [ "${WAIT_FOR_DB:-true}" = "true" ] && [ -n "${DB_HOST:-}" ]; then
  DB_PORT_VALUE="${DB_PORT:-5432}"
  echo "[entrypoint] Waiting for database ${DB_HOST}:${DB_PORT_VALUE}..."
  i=0
  max_attempts="${DB_WAIT_ATTEMPTS:-60}"
  while [ "$i" -lt "$max_attempts" ]; do
    if nc -z "$DB_HOST" "$DB_PORT_VALUE" >/dev/null 2>&1; then
      echo "[entrypoint] Database port is open."
      break
    fi
    i=$((i + 1))
    sleep 1
  done
  if [ "$i" -ge "$max_attempts" ]; then
    echo "[entrypoint] WARNING: timed out waiting for ${DB_HOST}:${DB_PORT_VALUE}; continuing anyway."
  fi
fi

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "[entrypoint] Applying database migrations..."
  python manage.py migrate --noinput
fi

if [ "${COLLECT_STATIC:-true}" = "true" ]; then
  echo "[entrypoint] Collecting static files..."
  python manage.py collectstatic --noinput
fi

# If CMD is gunicorn without explicit bind/workers, inject defaults.
if [ "$#" -gt 0 ] && [ "$1" = "gunicorn" ]; then
  shift
  set -- gunicorn \
    --bind "${GUNICORN_BIND:-0.0.0.0:${PORT:-8000}}" \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-180}" \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    "$@"
fi

echo "[entrypoint] Exec: $*"
exec "$@"
