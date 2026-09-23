#!/bin/sh
set -e
# Railway injects PORT. Bind nginx to it so the platform healthcheck can reach us.
PORT="${PORT:-8080}"
case "$PORT" in
  ''|*[!0-9]*)
    echo "Invalid PORT: ${PORT}" >&2
    exit 1
    ;;
esac
sed -i "s/listen 8080;/listen ${PORT};/g" /etc/nginx/conf.d/default.conf
echo "Starting frontend nginx on 0.0.0.0:${PORT}"
exec nginx -g "daemon off;"
