#!/bin/sh
set -e

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-3000}"

echo "Starting web on ${HOST}:${PORT}"
if [ -n "$API_PUBLIC_URL" ]; then
  echo "API_PUBLIC_URL=${API_PUBLIC_URL}"
fi
if [ -n "$BACKEND_PROXY_TARGET" ]; then
  echo "BACKEND_PROXY_TARGET=${BACKEND_PROXY_TARGET}"
fi

exec node .output/server/index.mjs
