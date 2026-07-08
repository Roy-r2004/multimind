#!/bin/sh
set -e

if [ -n "$DATABASE_URL" ]; then
  case "$DATABASE_URL" in
    postgresql://*)
      export DATABASE_URL="$(printf '%s' "$DATABASE_URL" | sed 's|^postgresql://|postgresql+asyncpg://|')"
      ;;
  esac
fi

port="${PORT:-8000}"

echo "Waiting for database..."
python - <<'PY'
import asyncio
import os
import sys
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

url = os.environ.get("DATABASE_URL", "")
if not url:
    sys.exit(0)


async def wait() -> None:
    for attempt in range(30):
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            return
        except Exception as exc:
            await engine.dispose()
            print(f"db not ready ({attempt + 1}/30): {exc}")
            time.sleep(2)
    print("Database not ready after 60s — starting API anyway", file=sys.stderr)


asyncio.run(wait())
PY

echo "Running migrations..."
if ! alembic upgrade head; then
  echo "Migration failed — starting API anyway so health checks can recover" >&2
fi

echo "Seeding reference data (best-effort)..."
python -m scripts.seed || echo "Seed failed — continuing" >&2

echo "Starting API on port ${port}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$port" --timeout-keep-alive 5
