#!/bin/sh
set -e

: "${NGINX_USER:?NGINX_USER is required}"
: "${NGINX_PASSWORD:?NGINX_PASSWORD is required}"

printf '%s' "$NGINX_PASSWORD" | htpasswd -ci /etc/nginx/.htpasswd "$NGINX_USER"
echo "[nginx-entrypoint] .htpasswd generated for user: $NGINX_USER"

exec nginx -g "daemon off;"
