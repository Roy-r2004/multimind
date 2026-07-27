#!/bin/sh
# Ephemeral test runner: install declared [dev] extras, then execute the given command.
# Does not alter the production API/worker images' default entrypoint.
set -e

if [ -n "$DATABASE_URL" ]; then
  case "$DATABASE_URL" in
    postgresql://*)
      export DATABASE_URL="$(printf '%s' "$DATABASE_URL" | sed 's|^postgresql://|postgresql+asyncpg://|')"
      ;;
  esac
fi

echo "Installing declared test dependencies (.[dev])..."
uv pip install --system -e '.[dev]'

if [ "$#" -eq 0 ]; then
  set -- pytest -q
fi

echo "Running: $*"
exec "$@"
