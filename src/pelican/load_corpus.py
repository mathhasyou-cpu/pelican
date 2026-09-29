"""Перелив научного корпуса из DuckDB (через сервер Quack) в Postgres.

    python -m pelican.load_corpus --from quack:localhost

Один процесс DuckDB держит оба конца: клиент Quack (источник) и расширение `postgres`
(приёмник), и строки идут `INSERT INTO pg.works SELECT … FROM quack_query(…)` — путь
значений не проходит через питон. Расширение `postgres` пишет бинарным `COPY`.

Куски — по диапазону `id`. Id — хэш, равномерный на [0, 2^63), поэтому равные диапазоны
дают равные куски (замер: квантили 0.1/0.5/0.9 лежат на 0.1/0.5/0.9 диапазона). Результат
`quack_query` оседает в памяти клиента целиком, поэтому кусок не может быть «весь
корпус». Куски идут по возрастанию, и каждый — одна транзакция приёмника, так что
перелив продолжается с `max(id)` уже залитого: обрыв посреди не оставляет ни дыры, ни
дублей.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import duckdb
import psycopg

from pelican import quack
from pelican.weak.corpus import JUNK_TYPES, MIN_TERM_CHARS

SCIENCE = "('arxiv', 'openalex')"
#: Число кусков на весь диапазон id. ⚠️ Догадка: 32 куска ≈ 475 тыс. работ и ~150 МБ
#: текста на кусок — с запасом по памяти клиента и достаточно крупно, чтобы накладные
#: расходы запроса не играли роли.
CHUNKS = 32
ID_SPAN = 2**63


SCHEMA = Path(__file__).with_name("schema.sql")


def _junk_sql() -> str:
    """`weak.corpus.is_junk` на стороне источника: считается один раз при переливе."""
    types = ", ".join(f"'{t}'" for t in JUNK_TYPES)
    return (
        f"(source = 'openalex' AND (length(term_raw) < {MIN_TERM_CHARS}"
        f" OR coalesce(json_extract_string(payload_json, 'type') IN ({types}), false)))"
    )


def _works_sql(lo: int, hi: int) -> str:
    return (
        "SELECT id, source, term_raw, observed_at, url, "
        "json_extract_string(payload_json, '$.field') AS field, "
        f"{_junk_sql()} AS junk "
        f"FROM signals WHERE source IN {SCIENCE} AND id > {lo} AND id <= {hi}"
    )


def _schema_parts() -> tuple[str, str]:
    text = SCHEMA.read_text(encoding="utf-8")
    tables, _, indexes = text.partition("-- @indexes")
    return tables, indexes


def _pg_scalar(url: str, sql: str) -> object:
    with psycopg.connect(url) as pg:
        return pg.execute(sql).fetchone()[0]


def load(dsn: str, token: str, url: str, chunks: int = CHUNKS) -> None:
    tables, indexes = _schema_parts()
    with psycopg.connect(url, autocommit=True) as pg:
        pg.execute(tables)

    done = int(_pg_scalar(url, "SELECT coalesce(max(id), 0) FROM works"))
    have = int(_pg_scalar(url, "SELECT count(*) FROM works"))
    total = int(quack.query(quack.connect(), dsn, token,
                            f"SELECT count(*) FROM signals WHERE source IN {SCIENCE}")[0][0])
    print(f"источник: {total:,} работ; в Postgres уже {have:,} (до id {done})", flush=True)

    conn = quack.connect()
    conn.execute("INSTALL postgres")
    conn.execute("LOAD postgres")
    conn.execute(f"ATTACH {quack._lit(url)} AS pg (TYPE postgres)")

    step = ID_SPAN // chunks
    started = time.perf_counter()
    for i in range(chunks):
        lo, hi = i * step, (ID_SPAN - 1 if i == chunks - 1 else (i + 1) * step)
        if hi <= done:
            continue
        lo = max(lo, done)
        t = time.perf_counter()
        src = quack.relation(dsn, token, _works_sql(lo, hi))
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO pg.works (id, source, term_raw, observed_at, url, field, junk) "
            f"SELECT * FROM {src}"
        )
        conn.execute("COMMIT")
        have = int(_pg_scalar(url, "SELECT count(*) FROM works"))
        print(
            f"кусок {i + 1}/{chunks}: {time.perf_counter() - t:5.0f} с, "
            f"всего {have:,} из {total:,} ({time.perf_counter() - started:,.0f} с)",
            flush=True,
        )

    # tech_mentions мала (тысячи строк) — одним запросом, с заменой целиком.
    t = time.perf_counter()
    conn.execute("BEGIN")
    conn.execute("DELETE FROM pg.tech_mentions")
    conn.execute(
        "INSERT INTO pg.tech_mentions (signal_id, model, tech, role, created_at) "
        "SELECT * FROM "
        + quack.relation(
            dsn, token, "SELECT signal_id, model, tech, role, created_at FROM tech_mentions"
        )
    )
    conn.execute("COMMIT")
    n = int(_pg_scalar(url, "SELECT count(*) FROM tech_mentions"))
    print(f"tech_mentions: {n:,} строк, {time.perf_counter() - t:.0f} с", flush=True)
    conn.close()

    t = time.perf_counter()
    print("индексы (GIN по tsv — долго)…", flush=True)
    with psycopg.connect(url, autocommit=True) as pg:
        pg.execute(indexes)
        pg.execute("ANALYZE works")
        pg.execute("ANALYZE tech_mentions")
    print(f"индексы: {time.perf_counter() - t:,.0f} с", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="dsn", required=True, help="quack:<хост>[:<порт>]")
    ap.add_argument("--token", default=os.environ.get("QUACK_TOKEN", ""))
    ap.add_argument("--url", default=os.environ.get(
        "DATABASE_URL", "postgresql://pelican:pelican@127.0.0.1:5436/pelican"))
    ap.add_argument("--chunks", type=int, default=CHUNKS)
    args = ap.parse_args(argv)
    if not args.token:
        print("нужен токен Quack: --token или QUACK_TOKEN", file=sys.stderr)
        return 2
    try:
        load(args.dsn, args.token, args.url, args.chunks)
    except duckdb.Error as exc:
        print(f"отказ: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
