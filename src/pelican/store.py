"""Хранилище корпуса и разметки — Postgres.

Корпус (`works`) — научные работы arxiv и openalex, 15 млн строк; поиск по тексту идёт
полнотекстовым индексом (`works.tsv`, GIN), а не проходом по столбцу. Векторы работ лежат
файлами-шардами рядом (`weak.shards`), в базу не кладутся: 14 млн × 768 — это отдельное
хранилище, а поиск по ним — проход по памяти, которому индекс не нужен.

Код ветки пишет SQL с плейсхолдерами `?` (как в DuckDB, на которой он вырос); обёртка
соединения переводит их в `%s` psycopg. Литеральный `%` в таком запросе удваивается там
же, поэтому `ILIKE '%x%'` рядом с параметром не ломает подстановку.
"""

from __future__ import annotations

import hashlib
import re
import struct
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

SCHEMA = Path(__file__).with_name("schema.sql")
SCIENCE_SOURCES = ("arxiv", "openalex")


def work_id(source: str, external_id: str, metric: str, observed_at: datetime) -> int:
    """Id работы из её естественного ключа — тот же, что у исходного хранилища.

    Content-addressed surrogate key: младшие 8 байт md5 от
    `source ␟ external_id ␟ metric ␟ epoch_us(observed_at)`, сдвиг вправо на бит, чтобы
    влезть в знаковый BIGINT. Одна и та же работа из двух прогонов получает один id, и
    повторный сбор дублей не даёт.
    """
    if observed_at.tzinfo is not None:
        observed_at = observed_at.astimezone(UTC).replace(tzinfo=None)
    delta = observed_at - datetime(1970, 1, 1)
    epoch_us = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    key = "\x1f".join((source, external_id, metric, str(epoch_us)))
    digest = hashlib.md5(key.encode("utf-8")).digest()
    # `md5_number_lower` DuckDB — это байты 8..16 дайджеста как little-endian UBIGINT
    # (сверено на строках исходного хранилища, тест `test_work_id_matches_source`).
    (lower,) = struct.unpack("<Q", digest[8:])
    return lower >> 1


class _Conn:
    """Соединение psycopg с интерфейсом `execute(sql, params)` → курсор."""

    def __init__(self, url: str) -> None:
        self.raw = psycopg.connect(url, autocommit=True)

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> psycopg.Cursor:
        if params:
            sql = sql.replace("%", "%%").replace("?", "%s")
            return self.raw.execute(sql, list(params))
        return self.raw.execute(sql)

    def close(self) -> None:
        self.raw.close()


class Store:
    def __init__(self, url: str) -> None:
        self.conn = _Conn(url)

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self, indexes: bool = True) -> None:
        tables, _, idx = SCHEMA.read_text(encoding="utf-8").partition("-- @indexes")
        self.conn.execute(tables)
        if indexes:
            self.conn.execute(idx)

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        return self.conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ корпус

    def insert_works(self, rows: Iterable[tuple]) -> int:
        """(id, source, term_raw, observed_at, url, field, junk) → `works`, без дублей.

        `COPY` во временную таблицу и одна вставка с `ON CONFLICT DO NOTHING`: построчный
        `INSERT` на суточном окне openalex (тысячи работ) стоил бы секунды на каждую
        сотню строк. Возвращает число реально вставленных.
        """
        rows = list(rows)
        if not rows:
            return 0
        with self.conn.raw.transaction():
            self.conn.raw.execute(
                "CREATE TEMP TABLE _w (id BIGINT, source TEXT, term_raw TEXT, "
                "observed_at TIMESTAMP, url TEXT, field TEXT, junk BOOLEAN) ON COMMIT DROP"
            )
            with self.conn.raw.cursor().copy(
                "COPY _w (id, source, term_raw, observed_at, url, field, junk) FROM STDIN"
            ) as copy:
                for row in rows:
                    copy.write_row(row)
            cur = self.conn.raw.execute(
                "INSERT INTO works (id, source, term_raw, observed_at, url, field, junk, "
                "collected_at) SELECT DISTINCT ON (id) id, source, term_raw, observed_at, url, "
                "field, junk, now() FROM _w ON CONFLICT (id) DO NOTHING"
            )
            return cur.rowcount

    def insert_signals(self, signals: Iterable[Any]) -> int:
        """Работы от сборщика (`sources.base.Signal`) → `works`. Не наука — пропускается.

        Id — из естественного ключа (`work_id`), «строка без содержания» и срез openalex
        считаются здесь один раз, а не на каждом запросе.
        """
        from pelican.weak.corpus import is_junk

        signals = self._new_arxiv_versions_only([s for s in signals if s.source in SCIENCE_SOURCES])
        rows = []
        for s in signals:
            payload = s.payload or {}
            rows.append((
                work_id(s.source, s.external_id, s.metric, s.observed_at),
                s.source,
                s.term_raw,
                _naive(s.observed_at),
                s.url,
                payload.get("field"),
                is_junk(s.source, s.term_raw, payload.get("type")),
            ))
        return self.insert_works(rows)

    def _new_arxiv_versions_only(self, signals: list[Any]) -> list[Any]:
        """Без повторных версий препринта, который уже есть в корпусе или в этой пачке.

        API arXiv отдаёт в `<id>` ТЕКУЩУЮ версию, а в `<published>` — дату v1. Суточный сбор
        читает окно в трое суток: вчера препринт пришёл как `…v1`, сегодня как `…v2` с той
        же датой и почти тем же текстом — другой `external_id`, другой id, вторая строка в
        `works` и двойной счёт в следе самых свежих работ (PEL-J-02). Ключ работы при этом
        не меняется (id — первичный ключ шардов и `tech_mentions`, совместим с родителем):
        выживает строка, пришедшая первой. Поиск — по дате публикации, у версий она общая,
        так что запрос идёт индексом `works_observed`, а не по 15 млн ссылок.
        """
        arxiv = [s for s in signals if s.source == "arxiv" and _arxiv_version(s.external_id)[1]]
        if not arxiv:
            return signals
        days = sorted({_naive(s.observed_at) for s in arxiv})
        have = {
            _arxiv_version(r[0].rsplit("/abs/", 1)[-1])[0]
            for r in self.conn.execute(
                "SELECT url FROM works WHERE source = 'arxiv' AND observed_at = ANY(?)", [days]
            ).fetchall()
            if r[0]
        }
        out, taken = [], set()
        # Младшая версия пачки — первой: из v1 и v2 одной пачки выживает v1.
        for s in sorted(signals, key=lambda s: _arxiv_version(s.external_id)[1] if s.source == "arxiv" else 0):
            if s.source == "arxiv":
                base, _v = _arxiv_version(s.external_id)
                if base in have or base in taken:
                    continue
                taken.add(base)
            out.append(s)
        return out

    def untagged_science(
        self,
        model: str,
        since: datetime | None,
        limit: int | None = None,
        ids: Sequence[int] | None = None,
    ) -> list[tuple[int, str]]:
        """(id, текст) — из чего модель ещё не доставала приём.

        Очередь — `LEFT JOIN tech_mentions` с проверкой на NULL: прерванный прогон
        продолжается с того же места, повторный не платит за сделанное. Строки без
        содержания (`works.junk`) в очередь не идут. `ids` сужает очередь до найденного
        поиском (`ask`); `since` — горизонт по дате, `None` — без горизонта.
        """
        only = ""
        if ids is not None:
            listed = ",".join(str(int(i)) for i in ids)
            if not listed:
                return []
            only = f"AND w.id IN ({listed})"
        horizon = "AND w.observed_at >= ?" if since is not None else ""
        params: list = [model] + ([_naive(since)] if since is not None else [])
        tail = f"LIMIT {int(limit)}" if limit is not None else ""
        return self.conn.execute(
            f"""
            SELECT w.id, w.term_raw
            FROM works w
            LEFT JOIN tech_mentions t ON t.signal_id = w.id AND t.model = ?
            WHERE t.signal_id IS NULL AND NOT w.junk {horizon}
              {only}
            ORDER BY w.observed_at DESC
            {tail}
            """,
            params,
        ).fetchall()

    def save_tech(self, model: str, rows: Sequence[tuple[int, str, str]]) -> None:
        """rows: (signal_id, tech, role). Пустой приём пишется тоже — иначе очередь
        никогда не опустеет."""
        if not rows:
            return
        with self.conn.raw.cursor() as cur:
            cur.executemany(
                "INSERT INTO tech_mentions (signal_id, model, tech, role, created_at) "
                "VALUES (%s, %s, %s, %s, now() AT TIME ZONE 'UTC') "
                "ON CONFLICT (signal_id, model) DO UPDATE SET "
                "tech = excluded.tech, role = excluded.role",
                [(sid, model, tech, role) for sid, tech, role in rows],
            )

_VERSION = re.compile(r"^(.*)v(\d+)$")


def _arxiv_version(external_id: str) -> tuple[str, int]:
    """`2609.06873v2` → (`2609.06873`, 2); без версии — (id, 0)."""
    m = _VERSION.match(external_id)
    return (m.group(1), int(m.group(2))) if m else (external_id, 0)


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
