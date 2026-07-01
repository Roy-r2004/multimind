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
        except Exception:
            await engine.dispose()
            time.sleep(2)
    print("Database not ready after 60s", file=sys.stderr)
    sys.exit(1)


asyncio.run(wait())
PY

echo "Running migrations..."
alembic upgrade head

echo "Seeding reference data..."
python -m scripts.seed

echo "Starting API on port ${port}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$port"
