"""Idempotent migrate + seed bootstrap for container deploy.

Safe to run from both API and scraping-worker: uses a Postgres advisory lock
so only one container performs the work at a time.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Arbitrary app-specific lock key
_LOCK_KEY = 87451293


def _run(label: str, args: list[str], *, required: bool = True) -> None:
    print(f"[deploy-bootstrap] {label}...")
    completed = subprocess.run(args, check=False)
    if completed.returncode != 0:
        message = f"[deploy-bootstrap] {label} failed with exit {completed.returncode}"
        if required:
            raise RuntimeError(message)
        print(message + " — continuing", file=sys.stderr)
    else:
        print(f"[deploy-bootstrap] {label} ok")


async def _with_lock(coro_factory) -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("[deploy-bootstrap] DATABASE_URL unset — skipping")
        return

    engine = create_async_engine(url)
    conn = await engine.connect()
    locked = False
    try:
        if url.startswith("postgresql"):
            await conn.execute(text(f"SELECT pg_advisory_lock({_LOCK_KEY})"))
            await conn.commit()
            locked = True
            print("[deploy-bootstrap] acquired deploy lock")
        await coro_factory()
    finally:
        if locked:
            try:
                await conn.execute(text(f"SELECT pg_advisory_unlock({_LOCK_KEY})"))
                await conn.commit()
            except Exception as exc:  # noqa: BLE001
                print(f"[deploy-bootstrap] unlock warning: {exc}", file=sys.stderr)
        await conn.close()
        await engine.dispose()


async def _bootstrap() -> None:
    _run("alembic upgrade head", [sys.executable, "-m", "alembic", "upgrade", "head"], required=True)
    _run(
        "seed system data (referee, brain, users)",
        [sys.executable, "-m", "scripts.seed"],
        required=False,
    )
    if os.environ.get("SEED_AUSTRIA_CENSUS_DEMO", "true").lower() != "false":
        _run(
            "seed Austria census demo",
            [sys.executable, "-m", "scripts.seed_austria_census_demo"],
            required=False,
        )


def main() -> int:
    try:
        asyncio.run(_with_lock(_bootstrap))
    except Exception as exc:  # noqa: BLE001
        print(f"[deploy-bootstrap] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
