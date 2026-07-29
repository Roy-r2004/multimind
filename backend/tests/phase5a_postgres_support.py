"""Shared fail-closed ephemeral PostgreSQL harness for Phase 5A tests."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

import asyncpg
import pytest

FORBIDDEN = frozenset({"multiai", "multiai_scraping_test", "postgres", "template0", "template1"})
NAME_RE = re.compile(r"^phase5a_[0-9a-f]{32}$")


@dataclass
class Phase5Postgres:
    admin: asyncpg.Connection
    database: str
    url: str

    async def alembic(self, *arguments: str) -> str:
        target = urlparse(self.url).path.lstrip("/")
        if target != self.database or target in FORBIDDEN or not NAME_RE.fullmatch(target):
            pytest.fail("Refusing Alembic against a non-ephemeral Phase 5A database.")
        command = ["alembic", *arguments]
        try:
            result = await asyncio.to_thread(
                subprocess.run, command, check=True,
                capture_output=True, text=True,
                env={**os.environ, "DATABASE_URL": self.url.replace(
                    "postgresql://", "postgresql+asyncpg://")},
            )
        except subprocess.CalledProcessError as exc:
            exc.add_note(
                "Alembic command failed\n"
                f"command: {' '.join(command)}\n"
                f"return code: {exc.returncode}\n"
                f"stdout:\n{exc.stdout or '<empty>'}\n"
                f"stderr:\n{exc.stderr or '<empty>'}"
            )
            raise
        return result.stdout + result.stderr

    async def connect(self) -> asyncpg.Connection:
        if urlparse(self.url).path.lstrip("/") != self.database:
            pytest.fail("Refusing non-ephemeral Phase 5A connection.")
        return await asyncpg.connect(self.url)


async def create_phase5_database() -> Phase5Postgres:
    admin_url = os.environ.get("POSTGRES_TEST_ADMIN_URL")
    if not admin_url:
        pytest.fail("POSTGRES_TEST_ADMIN_URL is required for Phase 5A PostgreSQL tests.")
    database = f"phase5a_{uuid.uuid4().hex}"
    if not NAME_RE.fullmatch(database) or database in FORBIDDEN:
        pytest.fail("Unsafe ephemeral database name.")
    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'CREATE DATABASE "{database}"')
    return Phase5Postgres(admin, database, admin_url.rsplit("/", 1)[0] + f"/{database}")


async def drop_phase5_database(db: Phase5Postgres) -> None:
    await db.admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
        db.database)
    await db.admin.execute(f'DROP DATABASE IF EXISTS "{db.database}"')
    await db.admin.close()
