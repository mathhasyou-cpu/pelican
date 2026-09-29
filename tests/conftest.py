from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

ADMIN_URL = os.environ.get("DATABASE_URL", "postgresql://pelican:pelican@127.0.0.1:5436/pelican")
TEST_DB = "pelican_test"
FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True, scope="session")
def _isolate_files(tmp_path_factory: pytest.TempPathFactory):
    """⚠️ Ни один тест не пишет замки и кэши в боевой каталог данных: `locks.LOCK_DIR` и
    кэши живого поиска считаются от `DATA_DIR`, а стенд в это время может идти."""
    from pelican import locks
    from pelican.weak import cache

    was_locks, locks.LOCK_DIR = locks.LOCK_DIR, tmp_path_factory.mktemp("locks")
    was_cache, cache.DIR = cache.DIR, tmp_path_factory.mktemp("live-cache")
    yield
    locks.LOCK_DIR, cache.DIR = was_locks, was_cache


@pytest.fixture
def store(pg_url):
    """Хранилище на тестовой базе: схема на месте, все таблицы пусты."""
    from pelican.store import Store

    s = Store(pg_url)
    s.init_schema()
    s.conn.execute(
        "TRUNCATE works, tech_mentions, tech_groups, tech_group_members, jobs, reports"
    )
    yield s
    s.close()


@pytest.fixture(scope="session")
def pg_url() -> str:
    """Отдельная база в том же Postgres: тесты не трогают корпус и боевую очередь."""
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3) as admin:
            exists = admin.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", [TEST_DB]
            ).fetchone()
            if not exists:
                admin.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres недоступен ({exc}); поднять: docker compose up -d postgres")
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB
